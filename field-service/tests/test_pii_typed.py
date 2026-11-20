"""PII screen on typed web fields: report titles, issue titles (create, rename), issue close notes and
member names (API and CSV import). Forms and import refuse; nothing is stored. Report notes and
transcripts keep being masked instead (tests/test_text_pii.py, test_transcripts.py)."""
from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from pulse.api.main import create_app
from pulse.common.clock import utcnow
from pulse.reports import service as reports
from tests.triage_world import file_report

BASE = "/acs/32/field"
PHONE = "9876543210"                     # invented, valid-looking mobile number
AADHAAR = "2345 6789 0123"


@pytest.fixture
def api(settings, pool, core, fake_tg, storage, team, conn):
    conn.execute("UPDATE field.member SET is_coordinator = true WHERE member_id = %s", (team["incharge-b"],))
    app = create_app(settings, pool=pool, core=core, telegram_client=fake_tg, storage=storage)
    with TestClient(app):
        def as_(user):
            c = TestClient(app)
            c.headers["Authorization"] = f"Bearer {core.issue_token(user)}"
            return c
        yield as_


def _refused(r, field):
    assert r.status_code == 422, r.text
    assert field in r.json()["detail"] and "identifier" in r.json()["detail"]


def test_report_title_typed_by_a_person(api, conn, core, team):
    rep = file_report(conn, core, team["worker-a1"])
    path = f"{BASE}/reports/{rep['report_id']}/title"
    before = reports.get(conn, rep["report_id"])["title"]
    _refused(api("usr-principal").post(path, json={"title": f"call {PHONE}"}), "title")
    assert reports.get(conn, rep["report_id"])["title"] == before
    ok = api("usr-principal").post(path, json={"title": "School roof leak"})
    assert ok.status_code == 200 and ok.json()["report"]["title"] == "School roof leak"


def test_issue_title_on_create_and_rename_and_close_note(api, conn, core, team):
    coord = api("usr-incharge-b")
    rep = file_report(conn, core, team["worker-a1"])
    _refused(coord.post(f"{BASE}/issues", json={"report_id": str(rep["report_id"]), "title": f"ask {PHONE}"}), "title")
    assert conn.execute("SELECT count(*) AS n FROM field.issue").fetchone()["n"] == 0

    iid = coord.post(f"{BASE}/issues", json={"report_id": str(rep["report_id"])}).json()["issue"]["issue_id"]
    _refused(coord.patch(f"{BASE}/issues/{iid}", json={"title": f"aadhaar {AADHAAR}"}), "title")
    _refused(coord.post(f"{BASE}/issues/{iid}/close", json={"note": f"fixed, ring {PHONE}"}), "note")
    issue = coord.get(f"{BASE}/issues/{iid}").json()["issue"]
    assert issue["status"] == "open" and PHONE not in issue["title"] and "2345" not in issue["title"]
    assert coord.post(f"{BASE}/issues/{iid}/close", json={"note": "fixed"}).json()["issue"]["status"] == "closed"


def test_member_name_on_create_and_update(api, conn, team):
    admin = api("usr-principal")
    _refused(admin.post(f"{BASE}/members", json={"user_id": "usr-worker-c1", "display_name": f"Demo C1 {PHONE}",
                                                 "parent_member_id": str(team["principal"])}), "display_name")
    assert conn.execute("SELECT 1 FROM field.member WHERE user_id = 'usr-worker-c1'").fetchone() is None
    _refused(admin.patch(f"{BASE}/members/{team['worker-a1']}", json={"display_name": f"A1 {PHONE}"}), "display_name")
    name = conn.execute("SELECT display_name FROM field.member WHERE member_id = %s", (team["worker-a1"],)).fetchone()
    assert PHONE not in name["display_name"]


def test_member_name_in_csv_import(api, conn):
    csv = ("user_id,display_name,parent_user_id\n"
           "usr-worker-c1,Demo Worker C1,usr-principal\n"
           f"usr-worker-c2,Demo C2 {PHONE},usr-principal\n")
    body = api("usr-principal").post(f"{BASE}/members/import?dry_run=false", json={"csv": csv}).json()
    assert body["imported"] is False and body["errors"] == 1
    bad = next(r for r in body["rows"] if not r["ok"])
    assert bad["user_id"] == "usr-worker-c2" and "identifier" in bad["error"]
    assert conn.execute("SELECT count(*) AS n FROM field.member WHERE user_id LIKE 'usr-worker-c%'").fetchone()["n"] == 0


def test_report_notes_are_still_masked_not_refused(conn, core, team):
    """Refusing a note would lose the report: it is kept, with the number masked."""
    member = conn.execute("SELECT * FROM field.member WHERE member_id = %s", (team["worker-a1"],)).fetchone()
    now = utcnow()
    r = reports.open_draft(conn, core, member, now=now)
    reports.add_note(conn, r["report_id"], f"handpump broken, call {PHONE}", actor=member["member_id"], now=now)
    filed = reports.file(conn, core, r["report_id"], how="user", actor=member["member_id"], now=now + timedelta(seconds=1))
    assert PHONE not in (filed["note_text"] or "") and "handpump broken" in filed["note_text"]
