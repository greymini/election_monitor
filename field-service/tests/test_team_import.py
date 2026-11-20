"""Team import from CSV: dry run by default, all or nothing, same rules as creating one member."""
import pytest
from fastapi.testclient import TestClient

from pulse.api.main import create_app
from pulse.team import csv_import

BASE = "/acs/32/field/members"
HEADER = "user_id,display_name,parent_user_id,finance_scope,can_give_advance,is_coordinator,areas,blocks\n"


@pytest.fixture
def api(settings, pool, core, fake_tg):
    app = create_app(settings, pool=pool, core=core, telegram_client=fake_tg)
    with TestClient(app) as c:
        c.headers["Authorization"] = f"Bearer {core.issue_token('usr-principal')}"
        assert c.post(BASE, json={"user_id": "usr-principal", "display_name": "Demo Candidate", "is_principal": True,
                                  "finance_scope": "all", "can_give_advance": True}).status_code == 201
        yield c


GOOD = HEADER + (
    "usr-incharge-a,Demo In-charge A,usr-principal,team,yes,no,,blk-a\n"
    "usr-worker-a1,Demo Worker A1,usr-incharge-a,own,,,area-a1;area-a2,\n"   # parent is an earlier row
    "\n"                                                                      # blank lines are skipped
    "usr-coordinator,Demo Coordinator,usr-principal,none,no,yes,,\n"
)


def _users(api):
    return sorted(m["user_id"] for m in api.get(BASE).json())


def test_dry_run_checks_and_keeps_nothing(api):
    r = api.post(f"{BASE}/import", json={"csv": GOOD})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["imported"] is False and body["total"] == 3 and body["errors"] == 0
    assert [x["row"] for x in body["rows"]] == [2, 3, 5]
    assert _users(api) == ["usr-principal"]


def test_import_creates_the_tree(api):
    r = api.post(f"{BASE}/import?dry_run=false", json={"csv": "﻿" + GOOD})   # Excel's byte-order mark
    assert r.json()["imported"] is True, r.text
    ms = {m["user_id"]: m for m in api.get(BASE).json()}
    assert sorted(ms) == ["usr-coordinator", "usr-incharge-a", "usr-principal", "usr-worker-a1"]
    a, w, c = ms["usr-incharge-a"], ms["usr-worker-a1"], ms["usr-coordinator"]
    assert a["parent_member_id"] == ms["usr-principal"]["member_id"] and a["finance_scope"] == "team"
    assert a["can_give_advance"] is True and a["scopes"] == [{"block_id": "blk-a", "area_id": None}]
    assert w["parent_member_id"] == a["member_id"] and w["finance_scope"] == "own"
    assert {s["area_id"] for s in w["scopes"]} == {"area-a1", "area-a2"}
    assert c["is_coordinator"] is True and c["finance_scope"] == "none"


def test_one_bad_row_imports_nothing_and_every_row_is_reported(api):
    bad = GOOD + (
        "usr-nobody,Unknown Person,usr-principal,own,,,,\n"          # not a core user
        "usr-worker-b1,Demo Worker B1,usr-worker-zz,own,,,,\n"       # parent not a member
        "usr-incharge-b,Demo In-charge B,usr-principal,boss,,,,\n"   # bad finance scope
        "usr-worker-a2,Demo Worker A2,usr-principal,own,maybe,,,\n"  # bad flag
        "usr-treasurer,Demo Treasurer,usr-principal,all,,,area-zz,\n"  # unknown area
        "usr-incharge-a,Again,usr-principal,own,,,,\n"               # duplicate of row 2
    )
    body = api.post(f"{BASE}/import?dry_run=false", json={"csv": bad}).json()
    assert body["imported"] is False and body["ok"] is False and body["errors"] == 6
    errors = {x["user_id"]: x["error"] for x in body["rows"] if not x["ok"]}
    assert "unknown user" in errors["usr-nobody"]
    assert "not a member yet" in errors["usr-worker-b1"]
    assert "finance_scope" in errors["usr-incharge-b"]
    assert "yes or no" in errors["usr-worker-a2"]
    assert "unknown area" in errors["usr-treasurer"]
    assert "already a member" in errors["usr-incharge-a"]
    assert _users(api) == ["usr-principal"]


def test_file_level_errors(api):
    assert api.post(f"{BASE}/import", json={"csv": "user_id,name\nx,y\n"}).status_code == 422          # unknown column
    assert api.post(f"{BASE}/import", json={"csv": "user_id,display_name\nx,y\n"}).status_code == 422  # missing column
    assert api.post(f"{BASE}/import", json={"csv": HEADER}).status_code == 422                          # no rows
    too_many = HEADER + "".join(f"u{i},n,usr-principal,,,,,\n" for i in range(csv_import.MAX_ROWS + 1))
    assert api.post(f"{BASE}/import", json={"csv": too_many}).status_code == 422


def test_admin_only(api, core):
    rep = core.issue_token("usr-worker-a1")
    r = api.post(f"{BASE}/import", json={"csv": GOOD}, headers={"Authorization": f"Bearer {rep}"})
    assert r.status_code == 403
