"""Custom-label review and promotion, keyword edits, and report titles."""
from datetime import timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from pulse.api.main import create_app
from pulse.common.clock import utcnow
from pulse.common.text import normalise
from pulse.ledger import classify, labels
from pulse.ledger import service as ledger
from pulse.ledger.service import ExpenseInput
from pulse.reports import service as reports
from pulse.reports import title
from tests.helpers import bucket_id

BASE = "/acs/32/field"


@pytest.fixture(autouse=True)
def _restore_reference_data(pool):
    """Buckets and keywords are reference data, not truncated between tests: undo what these add."""
    with pool.connection() as c:
        before = {(r["keyword"], r["bucket_id"]) for r in c.execute("SELECT keyword, bucket_id FROM field.exp_keyword")}
    yield
    with pool.connection() as c:
        for r in c.execute("SELECT keyword, bucket_id FROM field.exp_keyword").fetchall():
            if (r["keyword"], r["bucket_id"]) not in before:
                c.execute("DELETE FROM field.exp_keyword WHERE keyword = %s AND bucket_id = %s",
                          (r["keyword"], r["bucket_id"]))
        c.execute("TRUNCATE field.exp_custom_label")
        c.execute("DELETE FROM field.exp_bucket WHERE ac_id IS NOT NULL")


@pytest.fixture
def api(settings, pool, core, fake_tg, team):
    app = create_app(settings, pool=pool, core=core, telegram_client=fake_tg)
    with TestClient(app):
        def as_(user):
            c = TestClient(app)
            c.headers["Authorization"] = f"Bearer {core.issue_token(user)}"
            return c
        yield as_


def other(conn, core, limits, member, label, times):
    for _ in range(times):
        ledger.create_expense(conn, core, member, ExpenseInput(amount=Decimal("100"), bucket_id=bucket_id(conn, "OTHER"),
                                                               custom_label=label), limits=limits)


def test_labels_used_three_times_are_listed_and_the_job_marks_them(conn, core, limits, team):
    other(conn, core, limits, team["worker-a1"], "Ghadi marammat", 3)
    other(conn, core, limits, team["worker-a1"], "chhata", 2)
    assert [r["label_norm"] for r in labels.pending(conn, "ac-32")] == [normalise("ghadi marammat")]
    assert labels.review(conn) == {"ac-32": 1}
    assert labels.pending(conn, "ac-32")[0]["review_listed_at"] is not None


def test_promote_makes_an_ac_sub_head_and_a_keyword(api, conn, core, limits, team):
    other(conn, core, limits, team["worker-a1"], "ghadi marammat", 3)
    p = api("usr-principal")
    assert p.get(f"{BASE}/labels/pending").json()["items"][0]["count"] == 3
    travel = bucket_id(conn, "TRAVEL")
    r = p.post(f"{BASE}/labels/promote", json={"label_norm": normalise("ghadi marammat"), "parent_bucket_id": travel,
                                               "name_hi": "गाड़ी मरम्मत", "name_en": "Vehicle repair"})
    assert r.status_code == 201, r.text
    sub = r.json()
    assert sub["parent_id"] == travel and sub["ac_id"] == "ac-32" and sub["code"].startswith("TRAVEL.")
    assert p.get(f"{BASE}/labels/pending").json()["items"] == []
    assert classify.suggest(conn, "ac-32", "ghadi marammat").bucket_id == travel
    again = p.post(f"{BASE}/labels/promote", json={"label_norm": normalise("ghadi marammat"), "parent_bucket_id": travel,
                                                   "name_hi": "x", "name_en": "x"})
    assert again.status_code == 422
    assert conn.execute("SELECT action FROM field.admin_audit").fetchone()["action"] == "label_promoted"
    assert api("usr-incharge-a").get(f"{BASE}/labels/pending").status_code == 403


def test_keywords_are_admin_only(api, conn, team):
    food = bucket_id(conn, "FOOD")
    p = api("usr-principal")
    assert p.post(f"{BASE}/buckets/{food}/keywords", json={"keyword": "Demotestword"}).status_code == 201
    assert any(k["keyword"] == normalise("demotestword") for k in p.get(f"{BASE}/keywords").json()["items"])
    assert p.post(f"{BASE}/buckets/{food}/keywords", json={"keyword": "Demotestword"}).status_code == 422
    conn.execute("UPDATE field.member SET finance_scope = 'all' WHERE member_id = %s", (team["incharge-a"],))
    assert api("usr-incharge-a").delete(f"{BASE}/buckets/{food}/keywords/demotestword").status_code == 403
    assert p.delete(f"{BASE}/buckets/{food}/keywords/demotestword").status_code == 204
    assert p.delete(f"{BASE}/buckets/{food}/keywords/demotestword").status_code == 404


# ---- titles -------------------------------------------------------------------------------------

def test_first_sentence():
    assert title.first_sentence("चापाकल खराब है। पानी नहीं मिल रहा") == "चापाकल खराब है"
    assert title.first_sentence("  \n ok. Road broken! more") == "Road broken"
    assert title.first_sentence("ab") is None and title.first_sentence(None) is None
    assert len(title.first_sentence("x" * 200)) == title.TITLE_MAX


def _filed(conn, core, member_id, note):
    member = conn.execute("SELECT * FROM field.member WHERE member_id = %s", (member_id,)).fetchone()
    now = utcnow()
    r = reports.open_draft(conn, core, member, now=now)
    reports.add_note(conn, r["report_id"], note, actor=member_id, now=now)
    return reports.file(conn, core, r["report_id"], how="user", actor=member_id, now=now + timedelta(seconds=1))


def test_filed_report_gets_an_automatic_title_a_person_can_change(api, conn, core, team):
    r = _filed(conn, core, team["worker-a1"], "स्कूल की छत टपक रही है। बच्चे परेशान")
    assert (r["title"], r["title_source"]) == ("स्कूल की छत टपक रही है", "auto")
    p = api("usr-principal")
    path = f"{BASE}/reports/{r['report_id']}/title"
    got = p.post(path, json={"title": "School roof leak"}).json()["report"]
    assert (got["title"], got["title_source"]) == ("School roof leak", "person")
    reports.add_note(conn, r["report_id"], "और जानकारी", actor=team["worker-a1"], now=utcnow())
    assert reports.get(conn, r["report_id"])["title"] == "School roof leak"            # a person's title stays
    back = p.post(path, json={"title": None}).json()["report"]
    assert (back["title"], back["title_source"]) == ("स्कूल की छत टपक रही है", "auto")
    assert api("usr-worker-b1").post(path, json={"title": "x"}).status_code in (403, 404)
    listed = p.get(f"{BASE}/reports").json()["items"][0]
    assert listed["title"] == back["title"] and listed["title_source"] == "auto"
