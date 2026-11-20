"""Reports API: inbox order and filters, detail, signed media links, web entry, actions, summary."""
from __future__ import annotations

from datetime import timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from pulse.api import media_tokens
from pulse.api.main import create_app
from pulse.common.clock import utcnow
from pulse.metrics import reports as rm
from tests.conftest import TEST_JWT_SECRET
from tests.test_storage_imagemeta import make_jpeg
from tests.triage_world import category_id, file_report

BASE = "/acs/32/field/reports"


@pytest.fixture
def api_as(settings, pool, core, fake_tg, storage):
    clients = []

    def make(user_id):
        c = TestClient(create_app(settings, pool=pool, core=core, telegram_client=fake_tg, storage=storage))
        c.__enter__()
        if user_id:
            c.headers["Authorization"] = f"Bearer {core.issue_token(user_id)}"
        clients.append(c)
        return c

    yield make
    for c in clients:
        c.__exit__(None, None, None)


def test_inbox_order_and_filters(conn, core, team, api_as):
    t = utcnow()
    old_normal = file_report(conn, core, team["worker-a1"], now=t - timedelta(hours=3))
    unsorted = file_report(conn, core, team["worker-a1"], category=None, how="auto", now=t - timedelta(hours=2))
    urgent_new = file_report(conn, core, team["worker-a2"], urgency="urgent", now=t - timedelta(hours=1))
    urgent_old = file_report(conn, core, team["worker-a1"], urgency="urgent", now=t - timedelta(hours=4))
    api = api_as("usr-principal")
    order = [r["report_no"] for r in api.get(BASE).json()["items"]]
    assert order == [urgent_old["report_no"], urgent_new["report_no"], unsorted["report_no"], old_normal["report_no"]]
    assert [r["report_no"] for r in api.get(BASE, params={"unsorted": True}).json()["items"]] == [unsorted["report_no"]]
    assert api.get(BASE, params={"urgency": "urgent"}).json()["total"] == 2
    assert api.get(BASE, params={"reporter_member_id": str(team["worker-a2"])}).json()["total"] == 1
    assert api.get(BASE, params={"category_id": category_id(conn, "SERVICES"), "status": "new"}).json()["total"] == 3


def test_detail_signed_media_and_audit(conn, core, team, api_as, fake_tg, worker, drain):
    fake_tg.files["api-ph"] = make_jpeg()
    r = file_report(conn, core, team["worker-a1"], photo="api-ph")
    drain()                                                              # downloads and stores the photo
    principal = api_as("usr-principal")
    detail = principal.get(f"{BASE}/{r['report_id']}").json()
    [m] = detail["media"]
    assert m["status"] == "stored" and "url" in m
    assert {e["kind"] for e in detail["events"]} >= {"draft_opened", "filed"}
    anonymous = api_as(None)                                             # the link works without a bearer header
    got = anonymous.get(m["url"])
    assert got.status_code == 200 and got.headers["cache-control"] == "private, no-store"
    assert got.content[:3] == b"\xff\xd8\xff"
    viewed = conn.execute("SELECT actor_member_id, data FROM field.report_event WHERE kind = 'media_viewed'").fetchall()
    assert len(viewed) == 1 and viewed[0]["actor_member_id"] == team["principal"]
    # the reporter's own view is not audited
    own = api_as("usr-worker-a1").get(f"{BASE}/{r['report_id']}").json()["media"][0]["url"]
    assert anonymous.get(own).status_code == 200
    assert conn.execute("SELECT count(*) AS n FROM field.report_event WHERE kind = 'media_viewed'").fetchone()["n"] == 1


def test_media_token_expiry_and_binding(conn, core, team, api_as, storage):
    r = file_report(conn, core, team["worker-a1"], photo="tok-ph")
    asset = conn.execute("SELECT asset_id FROM field.media_asset").fetchone()["asset_id"]
    obj = storage.put(make_jpeg())
    conn.execute("UPDATE field.media_asset SET status = 'stored', sha256 = %s, storage_key = %s WHERE asset_id = %s",
                 (obj.sha256, obj.key, asset))
    api = api_as(None)
    url = f"{BASE}/{r['report_id']}/media/{asset}"
    fresh = media_tokens.issue(TEST_JWT_SECRET, team["principal"], r["report_id"], asset)
    assert api.get(url, params={"token": fresh}).status_code == 200
    stale = media_tokens.issue(TEST_JWT_SECRET, team["principal"], r["report_id"], asset,
                               now=utcnow() - timedelta(minutes=5, seconds=1))
    assert api.get(url, params={"token": stale}).status_code == 403          # 5 minutes and no more
    other = media_tokens.issue(TEST_JWT_SECRET, team["principal"], r["report_id"], UUID(int=1))
    assert api.get(url, params={"token": other}).status_code == 403
    login = core.issue_token("usr-principal")
    assert api.get(url, params={"token": login}).status_code == 403          # a login token is not a media link
    outsider = media_tokens.issue(TEST_JWT_SECRET, team["worker-b1"], r["report_id"], asset)
    assert api.get(url, params={"token": outsider}).status_code == 404       # cannot see the report
    with pytest.raises(media_tokens.BadToken):
        media_tokens.verify(TEST_JWT_SECRET, fresh, r["report_id"], asset, now=utcnow() + timedelta(minutes=6))


def test_web_entry_files_and_alerts(conn, team, api_as):
    api = api_as("usr-worker-a1")
    res = api.post(BASE, json={"note_text": "school mein shikshak nahi", "category_id": category_id(conn, "EDU"),
                               "area_id": "area-a1", "block_id": "blk-a"})
    assert res.status_code == 201
    r = res.json()["report"]
    assert r["status"] == "new" and r["created_via"] == "web" and r["report_no"].startswith("32-R-")
    assert conn.execute("SELECT count(*) AS n FROM field.alert_message WHERE kind = 'alert'").fetchone()["n"] == 1
    assert api.post(BASE, json={"note_text": ""}).status_code == 422


def test_actions(conn, core, team, api_as):
    r = file_report(conn, core, team["worker-a1"], urgency="urgent")
    p = api_as("usr-principal")
    rid = r["report_id"]
    assert p.post(f"{BASE}/{rid}/ack").json()["report"]["status"] == "acknowledged"
    assert p.post(f"{BASE}/{rid}/ack").json()["changed"] is False
    assert p.post(f"{BASE}/{rid}/assign", json={"member_id": str(team["worker-a2"])}).json()["report"]["status"] == "assigned"
    assert p.post(f"{BASE}/{rid}/urgency", json={"urgency": "normal"}).json()["report"]["urgency"] == "normal"
    assert p.post(f"{BASE}/{rid}/category", json={"category_id": category_id(conn, "EDU")}).status_code == 200
    w2 = api_as("usr-worker-a2")
    assert w2.post(f"{BASE}/{rid}/status", json={"status": "in_progress"}).json()["report"]["status"] == "in_progress"
    done = w2.post(f"{BASE}/{rid}/status", json={"status": "resolved", "note": "ho gaya"}).json()["report"]
    assert done["status"] == "resolved" and done["resolution_note"] == "ho gaya"
    assert w2.post(f"{BASE}/{rid}/status", json={"status": "acknowledged"}).status_code == 403      # reopen
    assert p.post(f"{BASE}/{rid}/status", json={"status": "acknowledged"}).json()["report"]["status"] == "acknowledged"
    assert p.post(f"{BASE}/{rid}/status", json={"status": "in_progress"}).status_code == 403        # not the assignee
    other = file_report(conn, core, team["worker-a2"], area=("blk-a", "area-a1"))
    merged = p.post(f"{BASE}/{other['report_id']}/merge", json={"into_report_id": str(rid)}).json()["report"]
    assert merged["status"] == "duplicate" and merged["duplicate_of"] == str(rid)
    assert p.post(f"{BASE}/{rid}/merge", json={"into_report_id": str(rid)}).status_code == 422


def test_summary_and_median_parity(conn, core, team, api_as):
    t = utcnow()
    rows = []
    for minutes, days in ((10, None), (30, 2), (50, 4)):
        r = file_report(conn, core, team["worker-a1"], now=t - timedelta(days=5))
        conn.execute("UPDATE field.report SET ack_at = filed_at + %s, status = %s, resolved_at = %s WHERE report_id = %s",
                     (timedelta(minutes=minutes), "resolved" if days else "acknowledged",
                      (r["filed_at"] + timedelta(days=days)) if days else None, r["report_id"]))
    rows = conn.execute("SELECT * FROM field.report").fetchall()
    sql = conn.execute(rm.medians_sql("TRUE")).fetchone()
    assert sql["ack_minutes"] == rm.ack_minutes(rows) == 30
    assert sql["resolve_days"] == rm.resolve_days(rows) == 3
    s = api_as("usr-principal").get(f"{BASE}/summary").json()
    assert s["medians"] == {"ack_minutes": "30.0", "resolve_days": "3.0"}
    assert {x["status"]: x["n"] for x in s["by_status"]} == {"resolved": 2, "acknowledged": 1}
    assert s["by_area"] == [{"area_id": "area-a1", "n": 3, "name_hi": "डेमो पंचायत क1", "name_en": "Demo Panchayat A1"}]


def test_medians_empty_are_null(conn):
    assert conn.execute(rm.medians_sql("FALSE")).fetchone() == {"ack_minutes": None, "resolve_days": None}
    assert rm.ack_minutes([]) is None and rm.resolve_days([]) is None

