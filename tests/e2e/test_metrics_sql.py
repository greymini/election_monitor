"""The SQL views against the same fixtures `tests/test_metrics.py` uses.

This is the test the audit asked for first: "materialized views against a
hand-built fixture - insert three booths with known votes across two elections,
refresh, and assert margin_pct, turnout_pct, share_pct, swing_pct and
priority_score to the decimal. This catches D1, D2, D3, B1 and B4 in one test."

Its real job is to stop the two implementations drifting. `analytics/metrics.py`
and `db/migrations/0015_metrics.sql` state the same definitions twice, which is
a duplication the master prompt accepts because the loaders need Python and the
API needs SQL - but only while something checks they agree. That is this file.

**Skipped without a database**, and recorded as NOT RUN in UAT_READINESS.md
rather than as a pass. Set E2E_DATABASE_URL to run it.
"""

from __future__ import annotations

import pytest

from analytics import metrics
from tests.e2e.conftest import refresh_views, requires_db

pytestmark = requires_db

# The same three booths as tests/test_metrics.py, so a divergence between the
# two implementations shows up as a disagreement about a number we have already
# computed by hand.
BOOTHS = {
    "A": {"JMM": 500, "BJP": 400, "JLKM": 80, "NOTA": 20},
    "B": {"JMM": 300, "BJP": 600, "JLKM": 90, "NOTA": 10},
    "C": {"JMM": 450, "BJP": 450, "JLKM": 95, "NOTA": 5},
}
ELECTORS = {"A": 1500, "B": 1400, "C": 1600}
REJECTED = {"A": 12, "B": 8, "C": 0}

# The prior election, so swing has something to compare against.
BOOTHS_PREV = {
    "A": {"JMM": 450, "BJP": 430, "JLKM": 100, "NOTA": 20},
    "B": {"JMM": 350, "BJP": 550, "JLKM": 90, "NOTA": 10},
    "C": {"JMM": 400, "BJP": 480, "JLKM": 115, "NOTA": 5},
}


# Rows this fixture creates, in the order they must be deleted. The session
# connection is autocommit, so nothing rolls back between tests: the fixture has
# to be able to run again over its own output. It could not, which is why the
# first test passed and the other thirteen died on booth_crosswalk_pkey the
# moment this file was first run against a real database.
TEST_CANDIDATE_SUFFIX = " candidate"


def _clear(cursor, ac_id: int) -> None:
    """Remove this fixture's rows, leaving the seed untouched.

    Only candidates whose name ends in " candidate" are deleted: the seeded
    candidates from db/seed/ac_totals.csv carry the published AC totals that
    `mv_ac_summary` and the Form 20 cross-check hang off, and dropping them
    would make several tests pass for the wrong reason.
    """
    cursor.execute("DELETE FROM result_booth      WHERE ac_id = %s", (ac_id,))
    cursor.execute("DELETE FROM result_booth_meta WHERE ac_id = %s", (ac_id,))
    cursor.execute("DELETE FROM booth_crosswalk   WHERE ac_id = %s", (ac_id,))
    cursor.execute("DELETE FROM ps_list_entry     WHERE ac_id = %s", (ac_id,))
    cursor.execute(
        "DELETE FROM candidate WHERE ac_id = %s AND name_en LIKE %s",
        (ac_id, f"%{TEST_CANDIDATE_SUFFIX}"),
    )
    # The roll chain, link first. Several assertions here are about what the
    # views do with *no* roll - "no link means no new-voter rows at all, rather
    # than rows of zero" (B1) - so a link left behind by another module makes
    # them pass or fail on test order. tests/e2e/test_ingest_pipeline.py loads a
    # real roll into this same database, which is how that first came up.
    cursor.execute(
        "DELETE FROM election_roll_link WHERE election_id IN "
        "(SELECT election_id FROM election WHERE ac_id = %s)", (ac_id,))
    cursor.execute("DELETE FROM roll_snapshot WHERE ac_id = %s", (ac_id,))
    cursor.execute("DELETE FROM roll_change   WHERE ac_id = %s", (ac_id,))
    cursor.execute("DELETE FROM roll_revision WHERE ac_id = %s", (ac_id,))
    cursor.execute("DELETE FROM booth         WHERE ac_id = %s", (ac_id,))


@pytest.fixture(scope="module")
def loaded(conn):
    """Build a minimal but complete AC: two elections, three booths, a roll.

    Module-scoped, and it has to be. `conn` is session-scoped, so the schema and
    this dataset outlive any one test; as a function-scoped fixture this rebuilt
    the same rows for every test and the second one died on
    `booth_crosswalk_pkey`. Thirteen of the fourteen tests in this file errored
    that way the first time the suite was ever executed - which is also the
    first evidence that "skipped" had been hiding more than a missing database.

    Module scope is right on the merits too: every test here reads the dataset
    and none mutates it, so there is nothing to isolate and rebuilding it
    fourteen times would only be slower.
    """
    with conn.cursor() as cursor:
        cursor.execute("SELECT ac_id FROM ac WHERE ac_number = 32")
        ac_id = cursor.fetchone()["ac_id"]
        _clear(cursor, ac_id)

        cursor.execute(
            "SELECT election_id, label FROM election WHERE ac_id = %s AND label IN "
            "('VS-2024', 'VS-2019')", (ac_id,),
        )
        elections = {r["label"]: r["election_id"] for r in cursor.fetchall()}
        assert set(elections) == {"VS-2024", "VS-2019"}, (
            "the seed must create a contest row per event per AC"
        )

        cursor.execute("SELECT block_id FROM block WHERE ac_id = %s ORDER BY block_id LIMIT 1",
                       (ac_id,))
        block_id = cursor.fetchone()["block_id"]
        cursor.execute(
            "INSERT INTO area (block_id, ac_id, kind, name_en, name_hi) "
            "VALUES (%s, %s, 'panchayat', 'Testpur', 'टेस्टपुर') "
            "ON CONFLICT (block_id, kind, name_en) DO UPDATE SET ac_id = EXCLUDED.ac_id "
            "RETURNING area_id",
            (block_id, ac_id),
        )
        area_id = cursor.fetchone()["area_id"]

        parties = {}
        cursor.execute("SELECT party_id, abbr FROM party")
        for row in cursor.fetchall():
            parties[row["abbr"]] = row["party_id"]

        booth_uids = {}
        for index, name in enumerate(sorted(BOOTHS), start=1):
            cursor.execute("SELECT next_booth_uid(32) AS uid")
            uid = cursor.fetchone()["uid"]
            booth_uids[name] = uid
            cursor.execute(
                "INSERT INTO booth (booth_uid, ac_id, area_id, building, current_ps_number) "
                "VALUES (%s, %s, %s, %s, %s)",
                (uid, ac_id, area_id, f"Test building {name}", index),
            )

        for label, votes_by_booth in (("VS-2024", BOOTHS), ("VS-2019", BOOTHS_PREV)):
            election_id = elections[label]
            for index, (name, votes) in enumerate(sorted(votes_by_booth.items()), start=1):
                cursor.execute(
                    "INSERT INTO booth_crosswalk (election_id, ac_id, ps_number, booth_uid, "
                    "match_method, confidence, reviewed) VALUES (%s, %s, %s, %s, 'anchor', 1.0, true)",
                    (election_id, ac_id, index, booth_uids[name]),
                )
                cursor.execute(
                    "INSERT INTO ps_list_entry (election_id, ac_id, ps_number, building) "
                    "VALUES (%s, %s, %s, %s)",
                    (election_id, ac_id, index, f"Test building {name}"),
                )
                for party, vote_count in votes.items():
                    cursor.execute(
                        "INSERT INTO candidate (election_id, ac_id, name_en, party_id) "
                        "VALUES (%s, %s, %s, %s) "
                        "ON CONFLICT (election_id, name_en, party_id) DO UPDATE "
                        "SET ac_id = EXCLUDED.ac_id RETURNING candidate_id",
                        (election_id, ac_id, f"{party} candidate", parties[party]),
                    )
                    candidate_id = cursor.fetchone()["candidate_id"]
                    cursor.execute(
                        "INSERT INTO result_booth (election_id, ac_id, ps_number, candidate_id, votes) "
                        "VALUES (%s, %s, %s, %s, %s)",
                        (election_id, ac_id, index, candidate_id, vote_count),
                    )
                if label == "VS-2024":
                    cursor.execute(
                        "INSERT INTO result_booth_meta (election_id, ac_id, ps_number, electors, "
                        "total_valid, nota, rejected) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                        (election_id, ac_id, index, ELECTORS[name],
                         sum(votes.values()), votes["NOTA"], REJECTED[name]),
                    )

        refresh_views(cursor)
        return {"ac_id": ac_id, "elections": elections, "booths": booth_uids, "area_id": area_id}


def test_the_fixture_actually_loaded(cursor, loaded):
    """Guards every assertion below. A view with no rows makes them all pass."""
    cursor.execute("SELECT COUNT(*) AS n FROM mv_result_booth_wide WHERE ac_id = %s",
                   (loaded["ac_id"],))
    assert cursor.fetchone()["n"] == 6, "expected three booths across two elections"


def test_valid_votes_includes_nota_and_matches_the_python(cursor, loaded):
    cursor.execute(
        "SELECT booth_uid, valid_votes, nota, votes_polled, rejected "
        "FROM mv_result_booth_wide WHERE election_id = %s ORDER BY booth_uid",
        (loaded["elections"]["VS-2024"],),
    )
    rows = {r["booth_uid"]: r for r in cursor.fetchall()}
    for name, uid in loaded["booths"].items():
        row = rows[uid]
        assert row["valid_votes"] == metrics.valid_votes(BOOTHS[name]) == 1000
        assert row["nota"] == BOOTHS[name]["NOTA"]
        assert row["votes_polled"] == metrics.votes_polled(1000, REJECTED[name])


def test_margin_and_signed_margin_match_the_python(cursor, loaded):
    cursor.execute(
        "SELECT booth_uid, winner_party, runner_party, margin_votes, margin_pct, "
        "signed_margin_pct, contest_party_a, contest_party_b "
        "FROM mv_result_booth_wide WHERE election_id = %s",
        (loaded["elections"]["VS-2024"],),
    )
    rows = {r["booth_uid"]: r for r in cursor.fetchall()}
    for name, uid in loaded["booths"].items():
        row = rows[uid]
        ranking = metrics.rank_candidates(BOOTHS[name])
        assert row["margin_votes"] == metrics.margin_votes(ranking)
        assert float(row["margin_pct"]) == metrics.margin_pct(ranking, 1000)
        expected_signed = metrics.signed_margin_pct(
            ranking, 1000, row["contest_party_a"], row["contest_party_b"]
        )
        got = None if row["signed_margin_pct"] is None else float(row["signed_margin_pct"])
        assert got == expected_signed


def test_nota_never_wins_in_sql_either(cursor, loaded):
    """A booth where NOTA outpolls every candidate still has a real winner."""
    ac_id = loaded["ac_id"]
    election_id = loaded["elections"]["VS-2024"]
    cursor.execute("SELECT next_booth_uid(32) AS uid")
    uid = cursor.fetchone()["uid"]
    cursor.execute(
        "INSERT INTO booth (booth_uid, ac_id, area_id, building, current_ps_number) "
        "VALUES (%s, %s, %s, 'NOTA heavy', 99)",
        (uid, ac_id, loaded["area_id"]),
    )
    cursor.execute(
        "INSERT INTO booth_crosswalk (election_id, ac_id, ps_number, booth_uid, "
        "match_method, confidence, reviewed) VALUES (%s, %s, 99, %s, 'anchor', 1.0, true)",
        (election_id, ac_id, uid),
    )
    for party, votes in (("JMM", 100), ("BJP", 90), ("NOTA", 500)):
        cursor.execute(
            "SELECT candidate_id FROM candidate WHERE election_id = %s AND name_en = %s",
            (election_id, f"{party} candidate"),
        )
        candidate_id = cursor.fetchone()["candidate_id"]
        cursor.execute(
            "INSERT INTO result_booth (election_id, ac_id, ps_number, candidate_id, votes) "
            "VALUES (%s, %s, 99, %s, %s)",
            (election_id, ac_id, candidate_id, votes),
        )
    try:
        refresh_views(cursor)

        cursor.execute(
            "SELECT winner_party, runner_party, valid_votes, margin_votes "
            "FROM mv_result_booth_wide WHERE booth_uid = %s", (uid,),
        )
        row = cursor.fetchone()
        assert row["winner_party"] == "JMM"
        assert row["runner_party"] == "BJP"
        assert row["valid_votes"] == 690          # NOTA is inside the denominator
        assert row["margin_votes"] == 10
    finally:
        # This is the one test here that adds to the shared dataset, so it is
        # the one that has to put it back. `loaded` is module-scoped over a
        # session-scoped connection, so a fourth booth left behind is a fourth
        # booth for every test that follows - which is exactly how
        # test_ac_summary_reproduces_the_booth_sums came to read 4 booths where
        # the fixture builds 3. Cleaning up here rather than loosening that
        # assertion, because an AC summary that agrees with the booth count is
        # worth asserting exactly.
        cursor.execute(
            "DELETE FROM result_booth WHERE election_id = %s AND ps_number = 99",
            (election_id,),
        )
        cursor.execute(
            "DELETE FROM booth_crosswalk WHERE election_id = %s AND ps_number = 99",
            (election_id,),
        )
        cursor.execute("DELETE FROM booth WHERE booth_uid = %s", (uid,))
        refresh_views(cursor)


def test_turnout_matches_the_python(cursor, loaded):
    cursor.execute(
        "SELECT booth_uid, electors, votes_polled, turnout_pct "
        "FROM mv_result_booth_wide WHERE election_id = %s",
        (loaded["elections"]["VS-2024"],),
    )
    for row in cursor.fetchall():
        expected = metrics.turnout_pct(row["votes_polled"], row["electors"])
        got = None if row["turnout_pct"] is None else float(row["turnout_pct"])
        assert got == expected


def test_turnout_is_null_where_electors_are_unknown(cursor, loaded):
    """B4. The prior election has no result_booth_meta rows at all."""
    cursor.execute(
        "SELECT COUNT(*) AS n, COUNT(turnout_pct) AS with_turnout "
        "FROM mv_result_booth_wide WHERE election_id = %s",
        (loaded["elections"]["VS-2019"],),
    )
    row = cursor.fetchone()
    assert row["n"] == 3
    assert row["with_turnout"] == 0


def test_share_pct_matches_the_python(cursor, loaded):
    cursor.execute(
        "SELECT booth_uid, party, votes, valid_votes, share_pct "
        "FROM mv_booth_party_share WHERE election_id = %s",
        (loaded["elections"]["VS-2024"],),
    )
    for row in cursor.fetchall():
        expected = metrics.share_pct(row["votes"], row["valid_votes"])
        assert float(row["share_pct"]) == expected


def test_swing_matches_the_python(cursor, loaded):
    cursor.execute(
        "SELECT s.booth_uid, s.party, s.share_pct, s.prev_share_pct, "
        "       s.swing_pct, s.crosswalk_confidence, s.crosswalk_reviewed, "
        "       s.lineage_kind "
        "FROM mv_swing s WHERE s.election_id = %s AND s.party = 'JMM'",
        (loaded["elections"]["VS-2024"],),
    )
    rows = cursor.fetchall()
    assert rows, "no swing rows - the crosswalk join is probably wrong"
    for row in rows:
        # The row's own crosswalk quality, not an assumed anchor (N1). The
        # gate is part of the metric, so comparing only the subtraction
        # would leave the half of swing_pct that decides whether there is a
        # swing at all unchecked on the SQL side.
        link = (
            None if row["crosswalk_confidence"] is None
            else metrics.CrosswalkLink(
                confidence=float(row["crosswalk_confidence"]),
                reviewed=bool(row["crosswalk_reviewed"]),
            )
        )
        expected = metrics.swing_pct(
            None if row["share_pct"] is None else float(row["share_pct"]),
            None if row["prev_share_pct"] is None
            else float(row["prev_share_pct"]),
            link,
            lineage_kind=row["lineage_kind"],
        )
        actual = (
            None if row["swing_pct"] is None else float(row["swing_pct"])
        )
        assert actual == expected, row["booth_uid"]


def test_swing_is_null_for_the_earliest_election(cursor, loaded):
    """D2: the fabricated swing. The 2019 rows have no predecessor."""
    cursor.execute(
        "SELECT COUNT(*) AS n, COUNT(swing_pct) AS with_swing FROM mv_swing "
        "WHERE election_id = %s", (loaded["elections"]["VS-2019"],),
    )
    row = cursor.fetchone()
    assert row["n"] > 0
    assert row["with_swing"] == 0, "the earliest election must have no swing, not a full share"


def test_floating_vote_is_null_with_only_one_poll_type(cursor, loaded):
    """D4: the uniform 50.00%. Only assembly elections are loaded here."""
    cursor.execute("SELECT COUNT(*) AS n, COUNT(floating_pct) AS with_value "
                   "FROM mv_floating_vote WHERE ac_id = %s", (loaded["ac_id"],))
    row = cursor.fetchone()
    assert row["with_value"] == 0, "floating_pct must be NULL, never 50.00, with one poll type"


def test_new_voter_share_is_null_without_a_roll_link(cursor, loaded):
    """B1: 0 additions everywhere. No election_roll_link rows exist here."""
    cursor.execute("SELECT COUNT(*) AS n FROM mv_new_voter_share WHERE ac_id = %s",
                   (loaded["ac_id"],))
    assert cursor.fetchone()["n"] == 0, (
        "with no roll link there must be no new-voter rows at all, rather than rows of zero"
    )


def test_priority_score_records_which_inputs_contributed(cursor, loaded):
    """Two of the four inputs are absent in this fixture, so the score must be
    renormalised over the ones present and say so."""
    cursor.execute(
        "SELECT booth_uid, priority_score, inputs_used, weight_used "
        "FROM mv_booth_priority WHERE ac_id = %s", (loaded["ac_id"],),
    )
    rows = cursor.fetchall()
    assert rows
    for row in rows:
        assert "new_voter_pct" not in (row["inputs_used"] or [])
        assert "floating_pct" not in (row["inputs_used"] or [])
        if row["priority_score"] is not None:
            assert 0.0 <= float(row["priority_score"]) <= 1.0
            assert float(row["weight_used"]) < 1.0


def test_ac_summary_reproduces_the_booth_sums(cursor, loaded):
    cursor.execute(
        "SELECT winner_party, runner_party, margin_votes, margin_pct, valid_votes, booths "
        "FROM mv_ac_summary WHERE election_id = %s",
        (loaded["elections"]["VS-2024"],),
    )
    row = cursor.fetchone()
    assert row["booths"] == 3
    assert row["valid_votes"] == 3000
    # JMM 1250, BJP 1450 across the three booths, so BJP leads by 200.
    assert row["winner_party"] == "BJP"
    assert row["runner_party"] == "JMM"
    assert row["margin_votes"] == 200
    assert float(row["margin_pct"]) == round(100 * 200 / 3000, 2)


def test_rerunning_the_refresh_is_byte_identical(cursor, loaded):
    """The audit's idempotency requirement, as a checksum over every view."""
    def digest() -> dict[str, str]:
        out = {}
        from analytics.refresh import VIEWS

        for view in VIEWS:
            cursor.execute(
                f"SELECT md5(COALESCE(string_agg(t::text, '' ORDER BY t::text), '')) AS d "
                f"FROM {view} t"
            )
            out[view] = cursor.fetchone()["d"]
        return out

    before = digest()
    refresh_views(cursor)
    assert digest() == before
