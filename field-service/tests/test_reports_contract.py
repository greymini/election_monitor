"""Route × role contract for reports, including restricted categories.

Roles: principal; coordinator (in-charge B made coordinator); in-charge A (above the reporter, scope
blk-a); reporter (worker A1); peer (worker A2, also the assignee where noted); outsider (worker B1).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from pulse.api.main import create_app
from tests.triage_world import category_id, file_report

BASE = "/acs/32/field/reports"
ROLES = {"principal": "usr-principal", "coord": "usr-incharge-b", "incharge": "usr-incharge-a",
         "reporter": "usr-worker-a1", "peer": "usr-worker-a2", "outsider": "usr-worker-b1"}


@pytest.fixture
def clients(settings, pool, core, fake_tg, storage, team, conn):
    conn.execute("UPDATE field.member SET is_coordinator = true WHERE member_id = %s", (team["incharge-b"],))
    app = create_app(settings, pool=pool, core=core, telegram_client=fake_tg, storage=storage)
    out = {}
    with TestClient(app):
        for role, user in ROLES.items():
            c = TestClient(app)
            c.headers["Authorization"] = f"Bearer {core.issue_token(user)}"
            out[role] = c
        yield out


@pytest.fixture
def reps(conn, core, team):
    return {
        "normal": file_report(conn, core, team["worker-a1"]),
        "restricted": file_report(conn, core, team["worker-a1"], category="LAW"),
        "outside": file_report(conn, core, team["worker-b1"], category="SCHEME"),        # block B
    }


def _visible(clients, role):
    return {r["report_id"] for r in clients[role].get(BASE, params={"limit": 200}).json()["items"]}


def test_list_visibility(clients, reps):
    ids = {k: str(v["report_id"]) for k, v in reps.items()}
    assert _visible(clients, "principal") == _visible(clients, "coord") == set(ids.values())
    assert _visible(clients, "incharge") == {ids["normal"]}                   # restricted hidden; block B outside
    assert _visible(clients, "reporter") == {ids["normal"], ids["restricted"]}   # their own, restricted included
    assert _visible(clients, "peer") == set()                                 # other areas, not above, not assigned
    assert _visible(clients, "outsider") == {ids["outside"]}


@pytest.mark.parametrize("which,expected", [
    ("normal", {"principal": 200, "coord": 200, "incharge": 200, "reporter": 200, "peer": 404, "outsider": 404}),
    ("restricted", {"principal": 200, "coord": 200, "incharge": 404, "reporter": 200, "peer": 404, "outsider": 404}),
])
def test_detail(clients, reps, which, expected):
    rid = reps[which]["report_id"]
    assert {role: c.get(f"{BASE}/{rid}").status_code for role, c in clients.items()} == expected


TRIAGE = {
    "normal": {"principal": 200, "coord": 200, "incharge": 200, "reporter": 403, "peer": 404, "outsider": 404},
    "restricted": {"principal": 200, "coord": 200, "incharge": 404, "reporter": 403, "peer": 404, "outsider": 404},
}


@pytest.mark.parametrize("which", ["normal", "restricted"])
@pytest.mark.parametrize("action", ["ack", "urgency", "category", "assign"])
def test_triage_actions(clients, conn, core, team, which, action):
    body = {"ack": None, "urgency": {"urgency": "urgent"}, "category": {"category_id": category_id(conn, "LAW" if which == "restricted" else "EDU")},
            "assign": {"member_id": str(team["principal"])}}[action]
    got = {}
    for role, c in clients.items():
        r = file_report(conn, core, team["worker-a1"], category="LAW" if which == "restricted" else "SERVICES")
        got[role] = c.post(f"{BASE}/{r['report_id']}/{action}", json=body).status_code
    assert got == TRIAGE[which]


def test_merge_rights(clients, conn, core, team):
    got = {}
    for role, c in clients.items():
        a = file_report(conn, core, team["worker-a1"])
        b = file_report(conn, core, team["worker-a1"], note="same again")
        got[role] = c.post(f"{BASE}/{b['report_id']}/merge", json={"into_report_id": str(a["report_id"])}).status_code
    assert got == TRIAGE["normal"]


def test_work_and_reopen_rights(clients, conn, core, team):
    """Start/close: the assignee (and triage for closing). Reopen: principal and coordinators only."""
    r = file_report(conn, core, team["worker-a1"])
    rid = r["report_id"]
    clients["incharge"].post(f"{BASE}/{rid}/assign", json={"member_id": str(team["worker-a2"])})
    start = {role: clients[role].post(f"{BASE}/{rid}/status", json={"status": "in_progress"}).status_code
             for role in ("reporter", "incharge", "outsider")}
    assert start == {"reporter": 403, "incharge": 403, "outsider": 404}
    assert clients["peer"].post(f"{BASE}/{rid}/status", json={"status": "in_progress"}).status_code == 200
    assert clients["reporter"].post(f"{BASE}/{rid}/status", json={"status": "resolved"}).status_code == 403
    assert clients["incharge"].post(f"{BASE}/{rid}/status", json={"status": "resolved"}).status_code == 200
    reopen = {role: clients[role].post(f"{BASE}/{rid}/status", json={"status": "acknowledged"}).status_code
              for role in ("incharge", "peer", "reporter")}
    assert reopen == {"incharge": 403, "peer": 403, "reporter": 403}
    assert clients["coord"].post(f"{BASE}/{rid}/status", json={"status": "acknowledged"}).status_code == 200


def test_summary_counts_only_what_you_can_see(clients, reps):
    total = {role: sum(x["n"] for x in c.get(f"{BASE}/summary").json()["by_status"]) for role, c in clients.items()}
    assert total == {"principal": 3, "coord": 3, "incharge": 1, "reporter": 2, "peer": 0, "outsider": 1}


def test_no_member_row_is_refused(clients, conn, team, reps):
    conn.execute("UPDATE field.member SET active = false, deactivated_at = now() WHERE member_id = %s", (team["worker-b1"],))
    assert clients["outsider"].get(BASE).status_code == 403


# ---- slice 3: transcript correction and issues ----------------------------------------------

def test_transcript_correction(clients, conn, core, team):
    expected = {"principal": 200, "coord": 200, "incharge": 403, "reporter": 403, "peer": 404, "outsider": 404}
    got = {}
    for role, c in clients.items():
        r = file_report(conn, core, team["worker-a1"])
        got[role] = c.post(f"{BASE}/{r['report_id']}/transcript", json={"text": "डेमो सुधार"}).status_code
    assert got == expected


ISSUE_WRITE = {"principal": 201, "coord": 201, "incharge": 403, "reporter": 403, "peer": 403, "outsider": 403}


def test_issue_routes(clients, conn, core, team):
    base = "/acs/32/field/issues"
    got = {role: c.post(base, json={"title": "डेमो", "area_id": "area-a1"}).status_code for role, c in clients.items()}
    assert got == ISSUE_WRITE
    iid = clients["principal"].post(base, json={"title": "डेमो", "area_id": "area-a1"}).json()["issue"]["issue_id"]
    reads = {role: c.get(f"{base}/{iid}").status_code for role, c in clients.items()}
    assert reads == {role: 200 for role in clients}                       # open, not restricted: all members
    assert {role: c.get(base).status_code for role, c in clients.items()} == {role: 200 for role in clients}
    for action, method, body in (("rename", "PATCH", {"title": "नया"}), ("close", "POST", {})):
        path = f"{base}/{iid}" if action == "rename" else f"{base}/{iid}/close"
        codes = {role: c.request(method, path, json=body).status_code for role, c in clients.items()
                 if role not in ("principal", "coord")}
        assert set(codes.values()) == {403}
    r = file_report(conn, core, team["worker-a1"])
    links = {role: c.post(f"{base}/{iid}/reports", json={"report_id": str(r["report_id"])}).status_code
             for role, c in clients.items() if role not in ("principal", "coord")}
    assert set(links.values()) == {403}
    assert clients["coord"].post(f"{base}/{iid}/reports", json={"report_id": str(r["report_id"])}).status_code == 200
    unlinks = {role: c.delete(f"{base}/{iid}/reports/{r['report_id']}").status_code for role, c in clients.items()
               if role not in ("principal", "coord")}
    assert set(unlinks.values()) == {403}


def test_restricted_issue_hidden(clients, conn, core, team):
    r = file_report(conn, core, team["worker-a1"], category="LAW")
    iid = clients["principal"].post("/acs/32/field/issues", json={"report_id": str(r["report_id"])}).json()["issue"]["issue_id"]
    reads = {role: c.get(f"/acs/32/field/issues/{iid}").status_code for role, c in clients.items()}
    assert reads == {"principal": 200, "coord": 200, "incharge": 404, "reporter": 404, "peer": 404, "outsider": 404}
