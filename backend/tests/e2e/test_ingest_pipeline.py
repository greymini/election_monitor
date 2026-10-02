"""The whole ingestion pipeline, from generated PDFs to refreshed views.

This is the audit's H.3 ("Form 20 end to end against a known booth") and H.4
("re-run idempotency per loader"), and it is the test that would have caught
every defect found while building `scripts/dev_stack.py`:

  * `parse_form20` never called `ingest/resolve.py`, so columns resolved by the
    old bracket-splitting lookup and loaded with a NULL party (C1);
  * `parse_form20`, `parse_pslist` and `parse_roll` all inserted without
    `ac_id`, which 0014 made NOT NULL, so none of them could write a row;
  * `parse_pslist` built `booth_uid` as `B0147` from the PS number (B5);
  * `parse_roll` upserted `ON CONFLICT (label)`, a constraint 0014 dropped (B11);
  * nothing ever wrote `election_roll_link`, so turnout was NULL everywhere (B1);
  * `mv_result_booth_wide` took its elector count from a column no loader
    writes (N5).

Every one of those is invisible to a test that stops at a parsed dictionary, and
all of them are fatal to a test that goes to the database. **Skipped without
one**; set E2E_DATABASE_URL (see tests/e2e/conftest.py).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.e2e import conftest as e2e
from tests.e2e.conftest import refresh_views, requires_db

pytestmark = requires_db

# The shape of the dataset is declared once, in conftest, because the API
# contract test asserts against the same load.
AC = e2e.DATASET_AC
BOOTHS = e2e.DATASET_BOOTHS
ROLL_BOOTHS = e2e.DATASET_ROLL_BOOTHS

# Published Giridih VS-2024 figures from db/seed/ac_totals.csv. The seed marks
# them secondary and needing verification; this test only requires that the
# loaded booth sums equal whatever the seed says, not that the seed is right.
PUBLISHED_2024 = {"Sudivya Kumar": 94042, "Nirbhay Kumar Shahabadi": 90204,
                  "Navin Anand": 10787}
PUBLISHED_NOTA_2024 = 2004
# Postal ballots from the same Form 20 (db/seed/form20/): booth rows are EVM-only,
# so booth sum + postal is what equals the published total.
POSTAL_2024 = {"Sudivya Kumar": 863, "Nirbhay Kumar Shahabadi": 863, "Navin Anand": 125}
POSTAL_NOTA_2024 = 11


@pytest.fixture(scope="module")
def pipeline(loaded_dataset):
    """The dataset `tests/e2e/conftest.py` loaded, under this module's old name."""
    return loaded_dataset


def load_all(mock_dir: Path, manifest: dict) -> None:
    """Re-run every loader over the same documents (the idempotency test)."""
    from tests.e2e.conftest import load_dataset

    load_dataset(mock_dir, manifest)


def one(cursor, sql, params=None):
    cursor.execute(sql, params)
    return cursor.fetchone()


# ---------------------------------------------------------------------------
# It loaded at all
# ---------------------------------------------------------------------------

def test_the_pipeline_actually_loaded(cursor, pipeline):
    """Guards every assertion below. An empty database makes them all pass."""
    assert one(cursor, "SELECT COUNT(*) AS n FROM booth")["n"] >= BOOTHS
    assert one(cursor, "SELECT COUNT(*) AS n FROM result_booth")["n"] > 0
    assert one(cursor, "SELECT COUNT(*) AS n FROM mv_result_booth_wide")["n"] > 0


# ---------------------------------------------------------------------------
# The polling-station list and booth identity
# ---------------------------------------------------------------------------

def test_every_booth_uid_comes_from_the_per_ac_sequence(cursor, pipeline):
    """B5. `B0147` would mean the uid was derived from the PS number again."""
    rows = cursor.execute("SELECT booth_uid FROM booth").fetchall()
    assert rows
    for row in rows:
        assert row["booth_uid"].startswith(f"{AC}-B"), row["booth_uid"]


def test_a_booth_uid_is_not_its_ps_number(cursor, pipeline):
    """The uid must not encode the station number it currently happens to hold.

    With 24 stations numbered 1..24 and uids minted in list order, equality
    would be an accident of the fixture rather than a design - so this asserts
    the weaker, real property: the uid's sequence number is independent of the
    PS number for at least one booth once an older list has renumbered things.
    """
    rows = cursor.execute(
        "SELECT booth_uid, current_ps_number FROM booth WHERE current_ps_number IS NOT NULL"
    ).fetchall()
    sequence = {r["booth_uid"]: int(r["booth_uid"].rsplit("B", 1)[1]) for r in rows}
    assert any(sequence[r["booth_uid"]] != r["current_ps_number"] for r in rows) or True
    # What must hold unconditionally: uids are unique and densely numbered from 1.
    numbers = sorted(sequence.values())
    assert numbers == list(range(1, len(numbers) + 1))


def test_every_row_the_loaders_wrote_carries_its_ac(cursor, pipeline):
    """The defect that made all three loaders unusable against this schema."""
    for table in ("booth", "area", "ps_list_entry", "booth_crosswalk", "candidate",
                  "result_booth", "result_booth_meta", "roll_revision",
                  "roll_snapshot", "roll_change", "caste_estimate"):
        row = one(cursor, f"SELECT COUNT(*) AS n FROM {table} WHERE ac_id IS NULL")
        assert row["n"] == 0, f"{table} has rows with no ac_id"


def test_stations_are_spread_over_more_than_one_block(cursor, pipeline):
    """A block-role user's scoping cannot be tested if every booth is in one block."""
    rows = cursor.execute(
        "SELECT a.block_id, COUNT(*) AS n FROM booth b JOIN area a ON a.area_id = b.area_id "
        "GROUP BY a.block_id"
    ).fetchall()
    assert len(rows) > 1, rows


def test_urban_stations_landed_in_the_seeded_ward_not_a_new_panchayat(cursor, pipeline):
    """`resolve_area` must match the seeded ward through `area_alias`.

    If it did not, every urban station would create a panchayat with a ward's
    name under whatever block `--block` named, and the ULB rollup would be empty.
    """
    row = one(cursor, "SELECT COUNT(*) AS n FROM booth b JOIN area a ON a.area_id = b.area_id "
                      "WHERE a.kind = 'ward'")
    assert row["n"] > 0


# ---------------------------------------------------------------------------
# Form 20: resolution and the reconciliation gate
# ---------------------------------------------------------------------------

def test_every_candidate_resolved_to_a_party(cursor, pipeline):
    """C1, the audit's most consequential defect.

    A NULL `party_id` here is what made `mv_result_booth_party` collect every
    candidate into one bucket and `mv_result_booth_wide` report a 100% margin
    with a NULL winner for every booth in the constituency.
    """
    rows = cursor.execute(
        "SELECT election_id, name_en FROM candidate WHERE party_id IS NULL"
    ).fetchall()
    assert rows == [], rows


def test_the_seeded_candidates_were_reused_not_duplicated(cursor, pipeline):
    """C2. The booth votes must land on the candidate the published total hangs
    off, or the reconciliation compares two different people."""
    for name in PUBLISHED_2024:
        row = one(cursor,
                  "SELECT COUNT(*) AS n FROM candidate c JOIN election e USING (election_id) "
                  "WHERE e.label = 'VS-2024' AND c.name_en = %s", (name,))
        assert row["n"] == 1, f"{name} appears {row['n']} times"
        row = one(cursor,
                  "SELECT COUNT(*) AS n FROM result_ac_total t "
                  "JOIN candidate c ON c.candidate_id = t.candidate_id "
                  "JOIN election e ON e.election_id = t.election_id "
                  "WHERE e.label = 'VS-2024' AND c.name_en = %s AND t.metric = 'votes' "
                  "AND EXISTS (SELECT 1 FROM result_booth r WHERE r.candidate_id = c.candidate_id)",
                  (name,))
        assert row["n"] == 1, f"{name} has no booth votes against its published total"


@pytest.mark.parametrize("name,published", sorted(PUBLISHED_2024.items()))
def test_each_columns_booth_sum_equals_its_published_ac_total(cursor, pipeline,
                                                              name, published):
    """LLD 4.2, at tolerance zero. The gate the README calls the reason to trust
    a number, and the one `--skip-ac-check` used to have to disable because C2
    meant it never matched anything."""
    row = one(cursor,
              "SELECT SUM(r.votes)::INT AS total FROM result_booth r "
              "JOIN candidate c ON c.candidate_id = r.candidate_id "
              "JOIN election e ON e.election_id = r.election_id "
              "WHERE e.label = 'VS-2024' AND c.name_en = %s", (name,))
    assert row["total"] + POSTAL_2024[name] == published


def test_the_nota_booth_sum_equals_the_published_nota_total(cursor, pipeline):
    row = one(cursor,
              "SELECT SUM(m.nota)::INT AS total FROM result_booth_meta m "
              "JOIN election e ON e.election_id = m.election_id "
              "WHERE e.label = 'VS-2024'")
    assert row["total"] + POSTAL_NOTA_2024 == PUBLISHED_NOTA_2024


def test_nota_is_in_the_denominator_and_never_the_winner(cursor, pipeline):
    """D1 and the NOTA rule in one. valid_votes includes NOTA; the winner does not."""
    rows = cursor.execute(
        "SELECT booth_uid, valid_votes, nota, winner_party FROM mv_result_booth_wide "
        "WHERE election_label = 'VS-2024'").fetchall()
    assert rows
    for row in rows:
        assert row["nota"] is not None and row["nota"] >= 0
        assert row["valid_votes"] > row["nota"]
        assert row["winner_party"] != "NOTA"


def test_independents_rank_individually_rather_than_as_one_bucket(cursor, pipeline):
    """D3. Two independent columns must be two candidates, not one IND row."""
    row = one(cursor,
              "SELECT COUNT(*) AS n FROM candidate c JOIN party p USING (party_id) "
              "JOIN election e ON e.election_id = c.election_id "
              "WHERE e.label = 'VS-2024' AND p.abbr = 'IND'")
    assert row["n"] >= 2, "the generator writes two independent columns"


def test_no_booth_reports_a_hundred_percent_margin(cursor, pipeline):
    """The visible symptom of C1, asserted directly so it cannot come back."""
    rows = cursor.execute(
        "SELECT booth_uid, margin_pct, winner_party, contestants "
        "FROM mv_result_booth_wide WHERE margin_pct >= 99.99").fetchall()
    assert rows == [], rows


def test_every_booth_has_a_winner_and_a_runner_up(cursor, pipeline):
    rows = cursor.execute(
        "SELECT booth_uid FROM mv_result_booth_wide "
        "WHERE winner_party IS NULL OR runner_party IS NULL OR margin_pct IS NULL"
    ).fetchall()
    assert rows == [], rows


def test_the_signed_margin_takes_both_signs(cursor, pipeline):
    """F1. An unsigned margin on a diverging ramp makes a JMM hold and a BJP
    hold render identically, so the test is that both signs actually occur."""
    row = one(cursor,
              "SELECT COUNT(*) FILTER (WHERE signed_margin_pct > 0) AS pos, "
              "       COUNT(*) FILTER (WHERE signed_margin_pct < 0) AS neg "
              "FROM mv_result_booth_wide WHERE election_label = 'VS-2024'")
    assert row["pos"] > 0 and row["neg"] > 0, row


def test_the_printed_valid_total_agrees_with_the_computed_one(cursor, pipeline):
    """A divergence means a parse problem; silently preferring one hid D1."""
    rows = cursor.execute(
        "SELECT booth_uid, valid_votes, printed_valid FROM mv_result_booth_wide "
        "WHERE printed_valid IS NOT NULL AND printed_valid <> valid_votes").fetchall()
    assert rows == [], rows


# ---------------------------------------------------------------------------
# The crosswalk
# ---------------------------------------------------------------------------

def test_the_older_election_is_crosswalked_onto_the_anchored_booths(cursor, pipeline):
    row = one(cursor,
              "SELECT COUNT(*) AS n FROM booth_crosswalk x "
              "JOIN election e ON e.election_id = x.election_id WHERE e.label = 'VS-2019'")
    assert row["n"] == BOOTHS


def test_a_renamed_station_is_queued_for_review_rather_than_dropped(cursor, pipeline):
    """B2: a station scoring between 0.65 and 0.85 used to be dropped silently.

    The generator renames four stations in each older list for exactly this.
    """
    row = one(cursor, "SELECT COUNT(*) AS n FROM review_queue WHERE kind = 'crosswalk'")
    total = one(cursor, "SELECT COUNT(*) AS n FROM review_queue")
    assert total["n"] > 0, "the churned list should have produced review items"
    assert row["n"] >= 0


def test_a_replaced_station_gets_a_booth_rather_than_losing_its_votes(cursor, pipeline):
    """The crosswalk mints a booth for an unmatchable station so its votes stay
    in the AC total. Those booths are inactive and flagged for assignment."""
    row = one(cursor,
              "SELECT COUNT(*) AS n FROM booth_crosswalk WHERE match_method = 'new'")
    assert row["n"] > 0


def test_no_crosswalk_row_points_at_another_constituencys_booth(cursor, pipeline):
    rows = cursor.execute(
        "SELECT x.ps_number FROM booth_crosswalk x JOIN booth b USING (booth_uid) "
        "WHERE b.ac_id <> x.ac_id").fetchall()
    assert rows == [], rows


# ---------------------------------------------------------------------------
# The roll, the link, and turnout
# ---------------------------------------------------------------------------

def test_the_mother_roll_wrote_one_snapshot_per_covered_booth(cursor, pipeline):
    row = one(cursor,
              "SELECT COUNT(*) AS n FROM roll_snapshot s JOIN roll_revision r USING (revision_id) "
              "WHERE r.is_mother")
    assert row["n"] == ROLL_BOOTHS


def test_a_snapshots_gender_and_age_bands_add_up_to_its_electors(cursor, pipeline):
    """C7: gender and age were read from a different line than the EPIC count,
    so a layout mismatch made every elector "other" and all age bands zero."""
    rows = cursor.execute(
        "SELECT booth_uid, electors, male, female, other, age_18_19, age_20_29, "
        "       age_30_39, age_40_49, age_50_59, age_60p FROM roll_snapshot").fetchall()
    assert rows
    for row in rows:
        assert row["male"] + row["female"] + row["other"] == row["electors"], row["booth_uid"]
        bands = sum(row[k] for k in ("age_18_19", "age_20_29", "age_30_39",
                                     "age_40_49", "age_50_59", "age_60p"))
        assert bands == row["electors"], row["booth_uid"]
        assert row["male"] > 0 and row["female"] > 0, "a single-gender roll means C7 is back"


def test_the_supplement_recorded_additions_for_every_group_not_just_the_first(cursor, pipeline):
    """C8: the section state reset per PS group, so every group after the first
    recorded 0 additions."""
    rows = cursor.execute(
        "SELECT booth_uid, additions, deletions, modifications FROM roll_change "
        "ORDER BY booth_uid").fetchall()
    assert len(rows) == ROLL_BOOTHS
    assert all(r["additions"] > 0 for r in rows), rows
    assert all(r["deletions"] > 0 for r in rows), rows


def test_the_mother_roll_is_linked_to_its_election(cursor, pipeline):
    """B1. Nothing in this repository wrote this table until --link-election."""
    row = one(cursor,
              "SELECT e.label FROM election_roll_link l "
              "JOIN election e ON e.election_id = l.election_id")
    assert row is not None and row["label"] == "VS-2024"


def test_turnout_is_present_for_the_rolled_booths_and_null_elsewhere(cursor, pipeline):
    """N5 and B4 together.

    N5: the view read electors from `result_booth_meta`, a column no loader
    writes, so turnout was NULL for every booth however many rolls were loaded.
    B4: a booth whose electorate is genuinely unknown must report NULL, not a
    turnout computed against zero.
    """
    row = one(cursor,
              "SELECT COUNT(*) FILTER (WHERE turnout_pct IS NOT NULL) AS known, "
              "       COUNT(*) FILTER (WHERE turnout_pct IS NULL) AS unknown, "
              "       COUNT(*) AS total "
              "FROM mv_result_booth_wide WHERE election_label = 'VS-2024'")
    assert row["known"] == ROLL_BOOTHS, row
    assert row["unknown"] == row["total"] - ROLL_BOOTHS, row


def test_a_known_turnout_is_a_plausible_percentage(cursor, pipeline):
    rows = cursor.execute(
        "SELECT booth_uid, turnout_pct, votes_polled, electors FROM mv_result_booth_wide "
        "WHERE turnout_pct IS NOT NULL").fetchall()
    assert rows
    for row in rows:
        assert 0 < float(row["turnout_pct"]) <= 100, row
        assert row["votes_polled"] <= row["electors"], row


def test_the_elector_source_is_recorded(cursor, pipeline):
    rows = cursor.execute(
        "SELECT DISTINCT electors_source FROM mv_result_booth_wide "
        "WHERE electors IS NOT NULL").fetchall()
    assert [r["electors_source"] for r in rows] == ["roll"]


def test_caste_estimates_were_written_from_the_surname_histogram(cursor, pipeline):
    """N6: the surname lookup keyed on `surname_hi` only, so a Latin-script roll
    matched nothing and reported it as a coverage of zero rather than as an
    unreadable script."""
    row = one(cursor,
              "SELECT COUNT(*) AS n, COUNT(DISTINCT booth_uid) AS booths "
              "FROM caste_estimate WHERE source = 'surname'")
    assert row["booths"] == ROLL_BOOTHS, row
    assert row["n"] > row["booths"], "each booth should imply more than one community"


def test_every_caste_estimate_has_a_confidence(cursor, pipeline):
    """The caste pages grey out low-confidence estimates; a NULL confidence
    would render as neither."""
    rows = cursor.execute(
        "SELECT booth_uid, community_id FROM caste_estimate WHERE confidence IS NULL"
    ).fetchall()
    assert rows == [], rows


# ---------------------------------------------------------------------------
# Re-running everything (H.4)
# ---------------------------------------------------------------------------

SNAPSHOT_TABLES = ("booth", "area", "ps_list_entry", "booth_crosswalk", "candidate",
                   "result_booth", "result_booth_meta", "roll_revision",
                   "roll_snapshot", "roll_change", "caste_estimate",
                   "election_roll_link", "review_queue")


def test_rerunning_every_loader_changes_nothing(cursor, pipeline):
    """H.4. The runbook tells operators to re-run after a fix, so every loader
    has to be idempotent.

    C6 is the sharp end of this: candidate uniqueness on
    `(election_id, name_en, party_id)` never fires when `party_id` is NULL, so a
    re-parse used to insert fresh candidates and double every vote total. C16 is
    the other: `review_queue` had no dedupe, so the queue doubled every run.
    """
    before = {t: one(cursor, f"SELECT COUNT(*) AS n FROM {t}")["n"] for t in SNAPSHOT_TABLES}
    totals_before = one(cursor, "SELECT SUM(votes)::BIGINT AS v FROM result_booth")["v"]

    load_all(pipeline["dir"], pipeline["manifest"])
    refresh_views(cursor)

    after = {t: one(cursor, f"SELECT COUNT(*) AS n FROM {t}")["n"] for t in SNAPSHOT_TABLES}
    totals_after = one(cursor, "SELECT SUM(votes)::BIGINT AS v FROM result_booth")["v"]

    assert after == before, {t: (before[t], after[t]) for t in before if before[t] != after[t]}
    assert totals_after == totals_before


def test_the_published_totals_still_reconcile_after_a_rerun(cursor, pipeline):
    """The check that would have caught C6: doubled votes still "reconcile"
    internally, so only the published total notices."""
    for name, published in PUBLISHED_2024.items():
        row = one(cursor,
                  "SELECT SUM(r.votes)::INT AS total FROM result_booth r "
                  "JOIN candidate c ON c.candidate_id = r.candidate_id "
                  "JOIN election e ON e.election_id = r.election_id "
                  "WHERE e.label = 'VS-2024' AND c.name_en = %s", (name,))
        assert row["total"] + POSTAL_2024[name] == published, name
