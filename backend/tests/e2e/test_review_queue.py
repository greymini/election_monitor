"""Review-queue writers that are not about one constituency.

0018 made review_queue.ac_id NOT NULL, but three writers have no AC to give:
an OCR page of a document whose AC is not yet known, a news place-name alias,
and a new roll supplement found on the CEO portal. Their inserts then failed -
OCR swallowed the error, so a low-confidence page went unreviewed. And the
supplement check re-inserted an item every day once an admin had resolved it,
hitting the (kind, ref) unique index.
"""

from __future__ import annotations

import pytest

from tests.e2e.conftest import requires_db

pytestmark = requires_db


@pytest.fixture
def db_env(db_url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", db_url)
    from common.config import get_settings
    from common.db import close_pools

    get_settings.cache_clear()
    close_pools()
    yield
    close_pools()


def _open(cursor, ref):
    cursor.execute("SELECT id, ac_id, status FROM review_queue WHERE ref = %s", (ref,))
    return cursor.fetchall()


def test_an_ocr_page_with_no_ac_is_queued(db_env, cursor, tmp_path):
    from ingest.ocr_tesseract import _queue_review

    pdf = tmp_path / "pc-level-form20.pdf"
    _queue_review(pdf, 3, 41.0, "public text")
    _queue_review(pdf, 3, 39.0, "public text")      # a re-run updates, never raises
    rows = _open(cursor, "pc-level-form20.pdf#p3")
    assert len(rows) == 1 and rows[0]["ac_id"] is None


def test_a_portal_supplement_is_flagged_once_and_not_again_after_resolution(
        db_env, cursor, monkeypatch):
    from ingest import fetch_ceo
    from worker import ops

    url = "https://ceo.example/supplement-2026-09.pdf"
    monkeypatch.setattr(fetch_ceo, "list_remote_documents",
                        lambda **k: [{"url": url, "filename": "s.pdf", "label": "Supp"}])
    assert ops.check_new_supplement()["new"] == 1
    cursor.execute("UPDATE review_queue SET status = 'resolved' WHERE ref = %s", (url,))
    # The next day's run: already handled, so neither re-flagged nor an error.
    assert ops.check_new_supplement()["new"] == 0
    assert len(_open(cursor, url)) == 1


def test_a_global_item_shows_on_every_constituency_and_can_be_resolved(
        db_env, client, tokens, ids, cursor):
    from pathlib import Path

    from ingest.ocr_tesseract import _queue_review

    _queue_review(Path("global-check.pdf"), 1, 20.0, "x")
    item_id = _open(cursor, "global-check.pdf#p1")[0]["id"]
    headers = {"Authorization": f"Bearer {tokens['admin']}"}
    for ac_number in (ids["ac_number"], ids["empty_ac"]):
        rows = client.get(f"/acs/{ac_number}/admin/review-queue", headers=headers).json()["rows"]
        assert item_id in {r["id"] for r in rows}, ac_number

    response = client.post(f"/acs/{ids['empty_ac']}/admin/review-queue/{item_id}",
                           json={"status": "resolved"}, headers=headers)
    assert response.status_code == 200
