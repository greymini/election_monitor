"""ingest.validate on the loaded dataset: the checks an operator is told to run.

Three checks failed on correct data, so `validate --strict` and `--privacy`
(which RUN.md says "must report clean") could never pass:

- form20_ac_totals compared every seeded published total, including those of
  constituencies with no booth results loaded at all (booth sum 0).
- form20_row_arithmetic added result_booth_meta.nota on top of a candidate sum
  that already includes NOTA, since NOTA is loaded as a candidate row (OD-N7).
- privacy_database flagged staff login phones (app_user.phone, the users' own
  credentials, not voter data) and a migration checksum (hex digits matching
  the 12-digit Aadhaar pattern).
"""

from __future__ import annotations

import pytest

from tests.e2e.conftest import requires_db

pytestmark = requires_db


@pytest.fixture
def validate(db_url, monkeypatch, loaded_dataset, users):
    monkeypatch.setenv("DATABASE_URL", db_url)
    from common.config import get_settings
    from common.db import close_pools

    get_settings.cache_clear()
    close_pools()
    from ingest import validate as module

    yield module
    close_pools()


def _one(checks, name):
    return next(c for c in checks if c.name == name)


def test_published_totals_reconcile_where_results_are_loaded(validate):
    check = _one(validate.check_form20_totals(), "form20_ac_totals")
    assert check.passed, (check.rows or [])[:5]


def test_every_loaded_row_adds_up(validate):
    check = _one(validate.check_row_arithmetic(), "form20_row_arithmetic")
    assert check.passed, (check.rows or [])[:5]


def test_the_database_privacy_scan_is_clean(validate):
    check = _one(validate.check_privacy_database(), "privacy_database")
    assert check.passed, check.rows


def test_the_privacy_scan_still_finds_a_real_leak(validate, conn):
    """The exemptions are exact (table, column) pairs, not a blind spot."""
    with conn.cursor() as cur:
        cur.execute("SELECT ac_id FROM ac WHERE ac_number = 32")
        ac_id = cur.fetchone()["ac_id"]
        cur.execute("INSERT INTO review_queue (kind, ref, ac_id, payload) VALUES "
                    "('ocr_page', 'leak-test', %s, '{\"excerpt\": \"call 9876543210\"}')", (ac_id,))
    try:
        check = _one(validate.check_privacy_database(), "privacy_database")
        assert not check.passed
        assert any(h["table"] == "review_queue" for h in check.rows)
    finally:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM review_queue WHERE ref = 'leak-test'")
