"""Ledger and spend API: creation of every kind, filters, summary, export audit, error bodies."""
import csv
import io
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from pulse.api.main import create_app
from pulse.ledger import queries
from tests.helpers import bucket_id

BASE = "/acs/32/field"


@pytest.fixture
def api_as(settings, pool, core, fake_tg):
    clients = []

    def make(user_id, raise_errors=True):
        c = TestClient(create_app(settings, pool=pool, core=core, telegram_client=fake_tg),
                       raise_server_exceptions=raise_errors)
        c.__enter__()
        c.headers["Authorization"] = f"Bearer {core.issue_token(user_id)}"
        clients.append(c)
        return c

    yield make
    for c in clients:
        c.__exit__(None, None, None)


def test_create_every_kind(conn, team, api_as):
    p = api_as("usr-principal")
    food = bucket_id(conn, "FOOD")
    r = p.post(f"{BASE}/txns", json={"kind": "inflow", "amount": "200000", "to_member_id": str(team["principal"]),
                                     "fund_source": "party"})
    assert r.status_code == 201 and r.json()["txn"]["amount"] == "200000.00" and r.json()["receiver_message"] is None
    r = p.post(f"{BASE}/txns", json={"kind": "advance", "amount": "50000", "to_member_id": str(team["incharge-a"])})
    assert r.status_code == 201 and r.json()["txn"]["status"] == "pending_ack"
    assert r.json()["receiver_message"] == "not_linked"                   # in-charge has no Telegram link
    advance_id = r.json()["txn"]["txn_id"]
    r = p.post(f"{BASE}/txns", json={"kind": "expense", "amount": "1500", "bucket_id": food,
                                     "from_member_id": str(team["worker-a1"]), "remark": "office entry"})
    assert r.status_code == 201 and r.json()["txn"]["from_member_id"] == str(team["worker-a1"])
    assert r.json()["txn"]["created_via"] == "web"
    r = p.post(f"{BASE}/txns", json={"kind": "adjustment", "amount": "50", "to_member_id": str(team["worker-a1"]),
                                     "remark": "cash count"})
    assert r.status_code == 201

    a = api_as("usr-incharge-a")
    assert a.post(f"{BASE}/txns/{advance_id}/ack", json={}).status_code == 200
    r = a.post(f"{BASE}/txns", json={"kind": "return", "amount": "1000"})
    assert r.status_code == 201 and r.json()["txn"]["to_member_id"] == str(team["principal"])


def test_validation_and_ledger_errors(conn, team, api_as):
    p = api_as("usr-principal")
    assert p.post(f"{BASE}/txns", json={"kind": "expense", "amount": "100"}).status_code == 422          # no bucket
    assert p.post(f"{BASE}/txns", json={"kind": "expense", "amount": "-1", "bucket_id": 1}).status_code == 422
    assert p.post(f"{BASE}/txns", json={"kind": "expense", "amount": "1.234", "bucket_id": 1}).status_code == 422
    r = p.post(f"{BASE}/txns", json={"kind": "expense", "amount": "100", "bucket_id": bucket_id(conn, "FOOD"),
                                     "remark": "call 9876543210"})
    assert r.status_code == 422 and "9876543210" not in r.text
    r = p.post(f"{BASE}/txns", json={"kind": "bribe", "amount": "100", "secret": "9876543210"})
    assert r.status_code == 422 and r.json()["detail"] == "invalid request" and "9876543210" not in r.text


def test_unhandled_errors_have_generic_body(team, api_as, monkeypatch):
    monkeypatch.setattr(queries, "list_txns", lambda *a, **k: 1 / 0)
    r = api_as("usr-principal", raise_errors=False).get(f"{BASE}/txns")
    assert r.status_code == 500 and r.json() == {"detail": "internal error"}
    assert "ZeroDivision" not in r.text and "Traceback" not in r.text


def test_list_filters_and_pagination(conn, core, limits, team, api_as):
    from pulse.ledger import service as ledger
    from pulse.ledger.service import ExpenseInput

    food, travel = bucket_id(conn, "FOOD"), bucket_id(conn, "TRAVEL.FUEL")
    for i in range(5):
        ledger.create_expense(conn, core, team["worker-a1"], ExpenseInput(amount=Decimal(100 + i), bucket_id=food,
                              area_id="area-a1"), limits=limits)
    ledger.create_expense(conn, core, team["worker-a1"], ExpenseInput(amount=Decimal("30000"), bucket_id=travel),
                          limits=limits)
    p = api_as("usr-principal")
    page = p.get(f"{BASE}/txns", params={"limit": 2, "offset": 0}).json()
    assert page["total"] == 6 and len(page["items"]) == 2
    assert p.get(f"{BASE}/txns", params={"bucket_id": bucket_id(conn, "TRAVEL")}).json()["total"] == 1   # incl. sub-heads
    assert p.get(f"{BASE}/txns", params={"flag": "large"}).json()["total"] == 1
    assert p.get(f"{BASE}/txns", params={"area_id": "area-a1", "kind": "expense"}).json()["total"] == 5
    assert p.get(f"{BASE}/txns", params={"member_id": str(team["worker-b1"])}).json()["total"] == 0
    assert p.get(f"{BASE}/txns", params={"limit": 500}).status_code == 422


def test_summary_groups_and_electors(conn, core, limits, team, api_as):
    from pulse.ledger import service as ledger
    from pulse.ledger.service import ExpenseInput

    food = bucket_id(conn, "FOOD")
    ledger.create_expense(conn, core, team["worker-a1"], ExpenseInput(amount=Decimal("8420"), bucket_id=food,
                          area_id="area-a1"), limits=limits)
    ledger.create_expense(conn, core, team["worker-a2"], ExpenseInput(amount=Decimal("1000"), bucket_id=food,
                          area_id="area-a3"), limits=limits)
    p = api_as("usr-principal")
    s = p.get(f"{BASE}/spend/summary", params={"group_by": "area"}).json()
    by_area = {g["area"]: g for g in s["groups"]}
    assert by_area["area-a1"]["electors"] == 4210 and by_area["area-a1"]["spend_per_1000_electors"] == "2000.00"
    assert by_area["area-a3"]["electors"] is None and by_area["area-a3"]["spend_per_1000_electors"] is None
    assert by_area["area-a1"]["share_pct"] == "89.38"
    s = p.get(f"{BASE}/spend/summary", params={"group_by": "bucket,member,month"}).json()
    assert {g["member_name"] for g in s["groups"]} == {"डेमो कार्यकर्ता क1", "डेमो कार्यकर्ता क2"}
    assert all(g["bucket_code"] == "FOOD" for g in s["groups"])
    assert p.get(f"{BASE}/spend/summary", params={"group_by": "area,area"}).status_code == 422
    assert p.get(f"{BASE}/spend/summary", params={"group_by": "password"}).status_code == 422
    tail = p.get(f"{BASE}/spend/tail").json()
    assert [b["band"] for b in tail["bands"]][:2] == ["0-500", "501-2000"] and tail["tail_count_pct"] == "50.00"


def test_export_writes_audit_row(conn, core, limits, team, api_as):
    from pulse.ledger import service as ledger
    from pulse.ledger.service import ExpenseInput

    ledger.create_expense(conn, core, team["worker-a1"], ExpenseInput(amount=Decimal("700"),
                          bucket_id=bucket_id(conn, "FOOD")), limits=limits)
    p = api_as("usr-principal")
    assert p.get(f"{BASE}/txns/export.csv").status_code == 422                       # reason required
    assert p.get(f"{BASE}/txns/export.csv", params={"reason": "ok call 9876543210"}).status_code == 422
    r = p.get(f"{BASE}/txns/export.csv", params={"reason": "weekly review", "kind": "expense"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert len(rows) == 1 and rows[0]["amount"] == "700.00" and rows[0]["created_at_ist"].endswith("+05:30")
    audit = conn.execute("SELECT * FROM field.txn_audit WHERE action = 'export'").fetchone()
    assert audit["note"] == "weekly review" and audit["actor_member_id"] == team["principal"]
    assert audit["ac_id"] == "ac-32" and audit["txn_id"] is None
    assert audit["new"]["row_count"] == 1 and audit["new"]["filters"]["kind"] == "expense"


def test_member_row_is_required(conn, team, api_as):
    conn.execute("UPDATE field.member SET active = false, deactivated_at = now() WHERE member_id = %s", (team["worker-a2"],))
    assert api_as("usr-worker-a2").get(f"{BASE}/buckets").status_code == 403
    assert api_as("usr-principal").get("/acs/99/field/buckets").status_code == 404


def test_buckets_nested(conn, team, api_as):
    rows = api_as("usr-worker-a1").get(f"{BASE}/buckets").json()
    assert len(rows) == 12 and rows[2]["code"] == "FOOD" and [s["code"] for s in rows[2]["subheads"]][0] == "FOOD.TEA"


def test_ledger_summary_covers_every_row_not_just_a_page(conn, team, api_as):
    """GET /txns/summary: the ledger screen's figures over all matching rows (the list pages by 200).
    Only non-void expenses count as spend; advances and voids are rows, never spend."""
    from tests.helpers import raw_txn

    food, travel = bucket_id(conn, "FOOD"), bucket_id(conn, "TRAVEL")
    w1, w2 = team["worker-a1"], team["worker-a2"]
    for i in range(230):                                     # more than one page of 200
        raw_txn(conn, kind="expense", amount="10", from_member_id=w1, bucket_id=food, created_by=w1)
    raw_txn(conn, kind="expense", amount="5000", from_member_id=w2, bucket_id=travel, created_by=w2)
    raw_txn(conn, kind="expense", amount="99999", from_member_id=w1, bucket_id=food, created_by=w1, status="void")
    raw_txn(conn, kind="advance", amount="7000", from_member_id=team["incharge-a"], to_member_id=w1,
            created_by=team["incharge-a"])

    p = api_as("usr-principal")
    s = p.get(f"{BASE}/txns/summary").json()
    assert s["totals"] == {"spend_total": "7300.00", "spend_entries": 231, "rows": 233}
    assert s["largest"]["amount"] == "5000.00" and s["largest"]["top_bucket_code"] == "TRAVEL"
    assert {b["top_bucket_code"]: b["spend"] for b in s["by_bucket"]} == {"FOOD": "2300.00", "TRAVEL": "5000.00"}
    day = s["groups"]
    assert len(day) == 1 and day[0]["rows"] == 233 and day[0]["spend"] == "7300.00" and day[0]["transfers"] == 1

    by_bucket = {g["key"]: g for g in p.get(f"{BASE}/txns/summary", params={"grouping": "bucket"}).json()["groups"]}
    assert by_bucket["FOOD"]["rows"] == 231 and by_bucket["FOOD"]["spend"] == "2300.00"   # the void is a row, not spend
    assert by_bucket["~advance"]["spend"] == "0.00"
    by_member = {g["key"]: g for g in p.get(f"{BASE}/txns/summary", params={"grouping": "member"}).json()["groups"]}
    assert by_member[str(w2)]["spend"] == "5000.00" and by_member[str(w2)]["member_name"]

    # same filters as GET /txns, and the same finance scope: a worker sees only their own
    assert p.get(f"{BASE}/txns/summary", params={"kind": "advance"}).json()["totals"]["spend_total"] == "0.00"
    own = api_as("usr-worker-a2").get(f"{BASE}/txns/summary").json()
    assert own["totals"] == {"spend_total": "5000.00", "spend_entries": 1, "rows": 1}
    assert p.get(f"{BASE}/txns/summary", params={"grouping": "week"}).status_code == 422


def test_team_position_is_not_netted(conn, team, api_as):
    """GET /balances team_position: one member holding money and another owed money stay separate."""
    from tests.helpers import raw_txn

    food = bucket_id(conn, "FOOD")
    p, a1, a2 = team["principal"], team["worker-a1"], team["worker-a2"]
    raw_txn(conn, kind="advance", amount="5000", from_member_id=p, to_member_id=a1, created_by=p)
    raw_txn(conn, kind="expense", amount="3000", from_member_id=a2, bucket_id=food, created_by=a2)   # out of pocket
    body = api_as("usr-principal").get(f"{BASE}/balances").json()
    assert body["team_position"] == {"holds": "5000.00", "owed": "3000.00"}
    by = {m["member_id"]: m for m in body["members"]}
    assert by[str(a2)]["balance"] == "-3000.00" and by[str(p)]["balance"] is None
