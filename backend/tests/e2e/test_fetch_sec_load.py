"""`ingest.fetch_sec --load-csv` loads local results into the right constituency.

Election labels are per AC since 0014 (PANCHAYAT-2022 exists in all six), and
the loader looked the election up by bare label and wrote no ac_id, so results
landed against an arbitrary AC's election and were invisible to
/acs/{ac}/local-results, which filters on ac_id.
"""

from __future__ import annotations

import pytest

from tests.e2e.conftest import requires_db

pytestmark = requires_db

CSV = (
    "seat_type,seat_name,area_name,winner,runner_up,votes,runner_up_votes,"
    "tagged_party,tag_source,tag_confidence\n"
    "mukhiya,Test Panchayat Seat,,Asha Devi,Sunita Kumari,812,640,,,\n"
)


@pytest.fixture
def csv_file(tmp_path):
    path = tmp_path / "sec.csv"
    path.write_text(CSV, encoding="utf-8")
    return path


def test_without_ac_an_ambiguous_label_is_refused(conn, csv_file, db_url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", db_url)
    from ingest.acscope import AmbiguousScope
    from ingest.fetch_sec import load_csv

    with pytest.raises(AmbiguousScope):
        load_csv(csv_file, "PANCHAYAT-2022")


def test_with_ac_the_rows_carry_that_constituency(conn, csv_file, db_url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", db_url)
    from ingest.fetch_sec import load_csv

    stats = load_csv(csv_file, "PANCHAYAT-2022", ac_number=32)
    assert stats["loaded"] == 1

    with conn.cursor() as cur:
        cur.execute(
            "SELECT a.ac_number, e.ac_id = lr.ac_id AS same FROM local_result lr "
            "JOIN election e ON e.election_id = lr.election_id "
            "JOIN ac a ON a.ac_id = lr.ac_id WHERE lr.seat_name = 'Test Panchayat Seat'")
        row = cur.fetchone()
    assert row["ac_number"] == 32 and row["same"]
