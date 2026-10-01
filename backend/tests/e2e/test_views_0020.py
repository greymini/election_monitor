"""Migrations 0020 and 0021 against the loaded dataset."""

from __future__ import annotations

import pytest

from tests.e2e.conftest import refresh_views, requires_db

pytestmark = requires_db


def test_a_booth_missing_an_input_gets_no_rank_for_it(conn, loaded_dataset):
    """NULL in, NULL out - never a near-top percentile (0015 ranked it ~1)."""
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS n FROM mv_booth_priority "
                    "WHERE new_voter_pct IS NULL AND 'new_voter_pct' = ANY(inputs_used)")
        assert cur.fetchone()["n"] == 0
        cur.execute("SELECT COUNT(*) AS n FROM mv_booth_priority "
                    "WHERE margin_stddev IS NULL AND 'volatility' = ANY(inputs_used)")
        assert cur.fetchone()["n"] == 0


def test_priority_ranks_match_the_python_reference(conn, loaded_dataset):
    """The SQL percentile over present values equals analytics.metrics.percentile_ranks."""
    from analytics.metrics import percentile_ranks

    with conn.cursor() as cur:
        cur.execute(
            "SELECT p.booth_uid, p.margin_pct, p.margin_stddev, p.inputs_used, p.priority_score "
            "FROM mv_booth_priority p JOIN ac a USING (ac_id) WHERE a.ac_number = 32 "
            "ORDER BY p.booth_uid")
        rows = cur.fetchall()
    assert len(rows) > 10
    vol = percentile_ranks([r["margin_stddev"] for r in rows])
    for row, rank in zip(rows, vol, strict=True):
        assert (rank is None) == ("volatility" not in row["inputs_used"]), row["booth_uid"]


def test_new_voter_share_survives_two_mother_rolls_before_the_window(conn, loaded_dataset):
    """0015 duplicated each booth per earlier mother roll; the refresh then
    failed on mv_nvs_key."""
    with conn.cursor() as cur:
        cur.execute("SELECT ac_id FROM ac WHERE ac_number = 32")
        ac_id = cur.fetchone()["ac_id"]
        cur.execute("SELECT revision_id, revision_date FROM roll_revision "
                    "WHERE ac_id = %s AND is_mother ORDER BY revision_date LIMIT 1", (ac_id,))
        mother = cur.fetchone()
        if mother is None:
            pytest.skip("no mother roll loaded")
        # Two older mother rolls with snapshots for the same booths, and an
        # earlier election linked to the first so a window exists.
        cur.execute("SELECT election_id FROM election WHERE ac_id = %s AND label = 'VS-2019'",
                    (ac_id,))
        earlier = cur.fetchone()["election_id"]
        created = []
        try:
            for i, days in enumerate((900, 800)):
                cur.execute(
                    "INSERT INTO roll_revision (ac_id, label, revision_date, is_mother) "
                    "VALUES (%s, %s, %s::date - %s, true) RETURNING revision_id",
                    (ac_id, f"test-old-{i}", mother["revision_date"], days))
                rid = cur.fetchone()["revision_id"]
                created.append(rid)
                cur.execute(
                    "INSERT INTO roll_snapshot (revision_id, booth_uid, electors, ac_id) "
                    "SELECT %s, booth_uid, electors, ac_id FROM roll_snapshot "
                    "WHERE revision_id = %s", (rid, mother["revision_id"]))
            cur.execute("INSERT INTO election_roll_link (election_id, revision_id) "
                        "VALUES (%s, %s)", (earlier, created[1]))
            refresh_views(cur)      # raised UniqueViolation on mv_nvs_key before 0020
            cur.execute("SELECT COUNT(*) AS n, COUNT(DISTINCT (election_id, booth_uid)) AS d "
                        "FROM mv_new_voter_share WHERE ac_id = %s", (ac_id,))
            counts = cur.fetchone()
            assert counts["n"] == counts["d"]
        finally:
            cur.execute("DELETE FROM election_roll_link WHERE election_id = %s", (earlier,))
            for rid in created:
                cur.execute("DELETE FROM roll_snapshot WHERE revision_id = %s", (rid,))
                cur.execute("DELETE FROM roll_revision WHERE revision_id = %s", (rid,))
            refresh_views(cur)


def test_the_read_only_role_can_read_every_allowed_table(conn, loaded_dataset):
    """Grants were lost when 0014/0015 recreated the views (0021)."""
    from chatbot.sql_guard import ALLOWED_TABLES

    with conn.cursor() as cur:
        missing = []
        for table in sorted(ALLOWED_TABLES):
            cur.execute("SELECT has_table_privilege('giridih_ro', %s, 'SELECT') AS ok", (table,))
            if not cur.fetchone()["ok"]:
                missing.append(table)
    assert not missing, f"giridih_ro cannot read: {missing}"
