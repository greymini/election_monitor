"""Route × finance-scope contract: for every ledger route, what none, own, team and all may see and do.

Callers: none = worker B1 (scope set to none), own = worker A1, team = in-charge A (subtree: workers
A1, A2), all = the principal. Visibility is checked on the rows themselves: team never sees outside
its subtree.
"""
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from pulse.api.main import create_app
from pulse.ledger import service as ledger
from pulse.ledger.service import ExpenseInput
from tests.helpers import bucket_id

BASE = "/acs/32/field"
CALLERS = {"none": "usr-worker-b1", "own": "usr-worker-a1", "team": "usr-incharge-a", "all": "usr-principal"}
D = Decimal


@pytest.fixture
def world(conn, core, limits, team):
    conn.execute("UPDATE field.member SET finance_scope = 'none' WHERE member_id = %s", (team["worker-b1"],))
    conn.execute("INSERT INTO field.ac_election_window (ac_id, starts_on, ends_on, ceiling_amount) "
                 "VALUES ('ac-32', current_date - 30, current_date + 30, 4000000)")
    p, a, b = team["principal"], team["incharge-a"], team["incharge-b"]
    for giver, to, amount in ((p, a, "50000"), (p, b, "30000"), (a, team["worker-a1"], "10000")):
        row = ledger.create_advance(conn, core, giver, to_member_id=to, amount=D(amount), limits=limits)
        ledger.acknowledge(conn, row["txn_id"], to)
    food = bucket_id(conn, "FOOD")
    for who, amount in (("worker-a1", "1000"), ("worker-a2", "2000"), ("incharge-b", "4000"), ("incharge-a", "500")):
        ledger.create_expense(conn, core, team[who], ExpenseInput(amount=D(amount), bucket_id=food), limits=limits)
    return team


@pytest.fixture
def clients(settings, pool, core, fake_tg, world):
    app = create_app(settings, pool=pool, core=core, telegram_client=fake_tg)
    out = {}
    with TestClient(app):                       # runs the app's lifespan once
        for scope, user in CALLERS.items():
            c = TestClient(app)
            c.headers["Authorization"] = f"Bearer {core.issue_token(user)}"
            out[scope] = c
        yield out


def _codes(clients, method, path, body_for=None, params=None):
    got = {}
    for scope, c in clients.items():
        body = body_for(scope) if body_for else None
        r = c.request(method, BASE + path, json=body, params=params)
        got[scope] = r.status_code
    return got


@pytest.mark.parametrize("path,params,expected", [
    ("/buckets", None, {"none": 403, "own": 200, "team": 200, "all": 200}),
    ("/txns", None, {"none": 403, "own": 200, "team": 200, "all": 200}),
    ("/balances", None, {"none": 403, "own": 403, "team": 200, "all": 200}),
    ("/spend/summary", {"group_by": "member"}, {"none": 403, "own": 403, "team": 200, "all": 200}),
    ("/spend/tail", None, {"none": 403, "own": 403, "team": 403, "all": 200}),
    ("/spend/flow", None, {"none": 403, "own": 403, "team": 200, "all": 200}),
    ("/spend/summary", {"group_by": "pay_mode,fund_source,day"}, {"none": 403, "own": 403, "team": 200, "all": 200}),
    ("/election-windows", None, {"none": 403, "own": 403, "team": 403, "all": 200}),
    ("/labels/pending", None, {"none": 403, "own": 403, "team": 403, "all": 200}),
    ("/txns/export.csv", {"reason": "contract test"}, {"none": 403, "own": 403, "team": 403, "all": 200}),
    ("/spend/need-vs-spend", None, {"none": 403, "own": 403, "team": 403, "all": 200}),
    ("/team/activity", None, {"none": 403, "own": 403, "team": 200, "all": 200}),
])
def test_read_routes(clients, path, params, expected):
    assert _codes(clients, "GET", path, params=params) == expected


def test_txn_rows_follow_scope(clients, world):
    t = world
    ids = {k: str(v) for k, v in t.items()}

    def rows(scope):
        return clients[scope].get(f"{BASE}/txns", params={"limit": 200}).json()["items"]

    def involved(r):
        return {r["from_member_id"], r["to_member_id"], r["created_by"]} - {None}

    own = rows("own")
    assert own and all(ids["worker-a1"] in involved(r) for r in own)
    team_set = {ids["incharge-a"], ids["worker-a1"], ids["worker-a2"]}
    team_rows = rows("team")
    assert team_rows and all(involved(r) & team_set for r in team_rows)
    assert not any(r["to_member_id"] == ids["incharge-b"] or r["from_member_id"] == ids["incharge-b"] for r in team_rows)
    assert len(rows("all")) == 7                            # 3 advances + 4 expenses
    assert len(rows("all")) > len(team_rows) > len(own)


def test_balances_and_summary_follow_scope(clients, world):
    names = {m["display_name"] for m in clients["team"].get(f"{BASE}/balances").json()["members"]}
    assert names == {"डेमो प्रभारी क", "डेमो कार्यकर्ता क1", "डेमो कार्यकर्ता क2"}
    assert len(clients["all"].get(f"{BASE}/balances").json()["members"]) == 6
    team_total = clients["team"].get(f"{BASE}/spend/summary").json()["totals"]["spend_total"]
    all_total = clients["all"].get(f"{BASE}/spend/summary").json()["totals"]["spend_total"]
    assert team_total == "3500.00" and all_total == "7500.00"          # in-charge B's 4,000 is outside the subtree
    assert clients["team"].get(f"{BASE}/spend/summary").json()["ceiling"] == []
    assert clients["all"].get(f"{BASE}/spend/summary").json()["ceiling"][0]["counted"] == "7500.00"


def test_create_routes(clients, conn, world):
    t = {k: str(v) for k, v in world.items()}
    food = bucket_id(conn, "FOOD")
    assert _codes(clients, "POST", "/txns", lambda s: {"kind": "expense", "amount": "10", "bucket_id": food}) == \
        {"none": 403, "own": 201, "team": 201, "all": 201}
    assert _codes(clients, "POST", "/txns", lambda s: {"kind": "advance", "amount": "10", "to_member_id": t["worker-a2"]}) == \
        {"none": 403, "own": 403, "team": 201, "all": 201}
    assert _codes(clients, "POST", "/txns", lambda s: {"kind": "advance", "amount": "10", "to_member_id": t["incharge-b"]}) == \
        {"none": 403, "own": 403, "team": 403, "all": 201}               # outside the in-charge's subtree
    assert _codes(clients, "POST", "/txns", lambda s: {"kind": "inflow", "amount": "10", "to_member_id": t["principal"],
                                                       "fund_source": "party"}) == \
        {"none": 403, "own": 403, "team": 403, "all": 201}
    assert _codes(clients, "POST", "/txns", lambda s: {"kind": "adjustment", "amount": "10", "remark": "x",
                                                       "to_member_id": t["worker-a1"]}) == \
        {"none": 403, "own": 403, "team": 403, "all": 201}
    assert _codes(clients, "POST", "/txns", lambda s: {"kind": "expense", "amount": "10", "bucket_id": food,
                                                       "from_member_id": t["worker-a2"]}) == \
        {"none": 403, "own": 403, "team": 403, "all": 201}               # on someone's behalf


def _per_caller(conn, core, limits, team, make):
    return {scope: str(make()["txn_id"]) for scope in CALLERS}


@pytest.mark.parametrize("action,body", [("ack", {}), ("dispute", {"reason": "not_received"})])
def test_answer_routes(clients, conn, core, limits, world, action, body):
    """Only the receiver (own: worker A1), or finance all on their behalf."""
    targets = _per_caller(conn, core, limits, world, lambda: ledger.create_advance(
        conn, core, world["incharge-a"], to_member_id=world["worker-a1"], amount=D("100"), limits=limits))
    got = {s: clients[s].post(f"{BASE}/txns/{targets[s]}/{action}", json=body).status_code for s in CALLERS}
    assert got == {"none": 403, "own": 200, "team": 403, "all": 200}


@pytest.mark.parametrize("action,body", [("void", {"reason": "entered twice"}),
                                         ("reclassify", {"bucket_id": None})])
def test_change_routes(clients, conn, core, limits, world, action, body):
    """The creator (own: worker A1) within 10 minutes, or finance all."""
    if action == "reclassify":
        body = {"bucket_id": bucket_id(conn, "TRAVEL")}
    targets = _per_caller(conn, core, limits, world, lambda: ledger.create_expense(
        conn, core, world["worker-a1"], ExpenseInput(amount=D("10"), bucket_id=bucket_id(conn, "FOOD")), limits=limits))
    got = {s: clients[s].post(f"{BASE}/txns/{targets[s]}/{action}", json=body).status_code for s in CALLERS}
    assert got == {"none": 403, "own": 200, "team": 403, "all": 200}


def test_ceiling_route(clients, conn, core, limits, world):
    targets = _per_caller(conn, core, limits, world, lambda: ledger.create_expense(
        conn, core, world["worker-a1"], ExpenseInput(amount=D("10"), bucket_id=bucket_id(conn, "FOOD")), limits=limits))
    got = {s: clients[s].post(f"{BASE}/txns/{targets[s]}/ceiling", json={"counts_to_ceiling": False}).status_code
           for s in CALLERS}
    assert got == {"none": 403, "own": 403, "team": 403, "all": 200}


def test_team_cannot_act_on_rows_outside_subtree(clients, conn, core, limits, world):
    outside = ledger.create_advance(conn, core, world["principal"], to_member_id=world["incharge-b"], amount=D("10"),
                                    limits=limits)
    assert clients["team"].post(f"{BASE}/txns/{outside['txn_id']}/ack", json={}).status_code == 403
    assert clients["team"].get(f"{BASE}/txns", params={"member_id": str(world["incharge-b"])}).json()["total"] == 0
