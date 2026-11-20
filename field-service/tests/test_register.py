"""Election register (LLD slice 4): windows, statutory heads, register by head, review list, export."""
import csv
import io
from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from pulse.api.main import create_app
from pulse.ledger import service as ledger
from pulse.ledger.service import ExpenseInput
from tests.helpers import bucket_id

BASE = "/acs/32/field"
D = Decimal
TODAY = date.today()


@pytest.fixture(autouse=True)
def _unmap_heads(pool):
    """Buckets are reference data (not truncated between tests): put the mapping back to empty."""
    yield
    with pool.connection() as c:
        c.execute("UPDATE field.exp_bucket SET statutory_head = NULL WHERE statutory_head IS NOT NULL")


@pytest.fixture
def api(settings, pool, core, fake_tg, team):
    app = create_app(settings, pool=pool, core=core, telegram_client=fake_tg)
    with TestClient(app):
        def as_(user):
            c = TestClient(app)
            c.headers["Authorization"] = f"Bearer {core.issue_token(user)}"
            return c
        yield as_


def window_body(**kw):
    body = {"starts_on": str(TODAY - timedelta(days=10)), "ends_on": str(TODAY + timedelta(days=20)),
            "ceiling_amount": "4000000", "cash_limit": "10000"}
    body.update(kw)
    return body


def test_windows_are_managed_and_audited(api, conn):
    p = api("usr-principal")
    r = p.post(f"{BASE}/election-windows", json=window_body())
    assert r.status_code == 201
    wid = r.json()["window_id"]
    clash = p.post(f"{BASE}/election-windows", json=window_body(starts_on=str(TODAY), ends_on=str(TODAY)))
    assert clash.status_code == 422 and "overlap" in clash.json()["detail"]
    bad = p.post(f"{BASE}/election-windows", json=window_body(ends_on=str(TODAY - timedelta(days=30))))
    assert bad.status_code == 422
    assert p.put(f"{BASE}/election-windows/{wid}", json=window_body(ceiling_amount="3000000")).json()["ceiling_amount"] == "3000000.00"
    assert [w["window_id"] for w in p.get(f"{BASE}/election-windows").json()["windows"]] == [wid]
    assert api("usr-incharge-a").get(f"{BASE}/election-windows").status_code == 403
    assert p.delete(f"{BASE}/election-windows/{wid}").status_code == 204
    actions = [r["action"] for r in conn.execute("SELECT action FROM field.admin_audit ORDER BY audit_id")]
    assert actions == ["window_created", "window_changed", "window_deleted"]


def test_statutory_heads_ship_empty_and_are_admin_editable(api, conn, team):
    p = api("usr-principal")
    heads = p.get(f"{BASE}/statutory-heads").json()["buckets"]
    assert heads and all(h["statutory_head"] is None for h in heads)
    event = bucket_id(conn, "EVENT")
    r = p.put(f"{BASE}/buckets/{event}/statutory-head", json={"statutory_head": "Public meetings and rallies"})
    assert r.status_code == 200 and r.json()["statutory_head"] == "Public meetings and rallies"
    sub = next(h for h in p.get(f"{BASE}/statutory-heads").json()["buckets"] if h["code"] == "EVENT.TENT")
    assert sub["statutory_head"] is None and sub["effective_head"] == "Public meetings and rallies"
    # finance scope all but not a platform admin
    conn.execute("UPDATE field.member SET finance_scope = 'all' WHERE member_id = %s", (team["incharge-a"],))
    assert api("usr-incharge-a").put(f"{BASE}/buckets/{event}/statutory-head",
                                     json={"statutory_head": "x"}).status_code == 403
    assert p.put(f"{BASE}/buckets/{event}/statutory-head", json={"statutory_head": None}).json()["statutory_head"] is None


@pytest.fixture
def register_world(api, conn, core, limits, team):
    p = api("usr-principal")
    early = ledger.create_expense(conn, core, team["worker-a1"],
                                  ExpenseInput(amount=D("700"), bucket_id=bucket_id(conn, "FOOD"),
                                               txn_date=TODAY - timedelta(days=2)), limits=limits)
    wid = p.post(f"{BASE}/election-windows", json=window_body()).json()["window_id"]
    conn.execute("UPDATE field.exp_bucket SET statutory_head = 'Public meetings and rallies' WHERE code = 'EVENT'")
    rows = {
        "rally": (team["incharge-a"], "EVENT.TENT", "5000", "upi"),
        "cash": (team["incharge-a"], "PUBLICITY", "15000", "cash"),         # over the 10,000 cash limit
        "feast": (team["principal"], "SOCIAL.FEAST", "3000", "upi"),         # sensitive in window
        "out": (team["principal"], "FOOD", "999", "upi"),                    # outside: dated before the window
    }
    made = {}
    for key, (who, code, amount, mode) in rows.items():
        made[key] = ledger.create_expense(
            conn, core, who, ExpenseInput(amount=D(amount), bucket_id=bucket_id(conn, code), pay_mode=mode,
                                          txn_date=TODAY - timedelta(days=40) if key == "out" else None),
            limits=limits)
    return p, wid, made, early


def test_register_groups_by_head_and_keeps_unmapped(register_world):
    p, wid, made, early = register_world
    r = p.get(f"{BASE}/spend/register", params={"window_id": wid}).json()
    heads = {h["statutory_head"]: h for h in r["heads"]}
    assert heads["Public meetings and rallies"] == {"statutory_head": "Public meetings and rallies", "mapped": True,
                                                    "entries": 1, "amount": "5000.00"}
    assert heads["unmapped"]["entries"] == 3 and heads["unmapped"]["mapped"] is False      # cash, feast, early
    assert r["heads"][-1]["statutory_head"] == "unmapped"
    assert r["total"] == "23700.00" and r["entries"] == 4
    assert r["counted"] == "23000.00"                       # the entry recorded before the window is not marked
    assert r["recorded_before_window"] == {"entries": 1, "amount": "700.00"}
    assert r["undecided"] == 3 and r["daily"][-1]["counted_to_date"] == "23000.00"


def test_review_list_and_ceiling_decision(register_world, team):
    p, wid, made, _ = register_world
    items = p.get(f"{BASE}/spend/register/review", params={"window_id": wid}).json()["items"]
    flags = {i["txn_no"]: set(i["flagged"]) for i in items}
    assert "cash_over_limit" in flags[made["cash"]["txn_no"]]
    assert "sensitive_in_window" in flags[made["feast"]["txn_no"]]
    assert made["rally"]["txn_no"] in flags                       # undecided ceiling
    assert p.post(f"{BASE}/txns/{made['feast']['txn_id']}/ceiling", json={"counts_to_ceiling": False}).status_code == 200
    r = p.get(f"{BASE}/spend/register", params={"window_id": wid}).json()
    assert r["counted"] == "20000.00" and r["undecided"] == 2


def test_register_export_needs_reason_is_audited_and_keeps_unmapped(register_world, conn):
    p, wid, made, _ = register_world
    assert p.get(f"{BASE}/spend/register/export.csv", params={"window_id": wid}).status_code == 422
    r = p.get(f"{BASE}/spend/register/export.csv", params={"window_id": wid, "reason": "agent's weekly check"})
    assert r.status_code == 200
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert [x["statutory_head"] for x in rows] == ["Public meetings and rallies", "unmapped", "unmapped", "unmapped"]
    audit = conn.execute("SELECT note, new FROM field.txn_audit WHERE action = 'export'").fetchone()
    assert audit["note"] == "agent's weekly check" and audit["new"]["filters"] == {"register_window_id": wid}
    assert audit["new"]["row_count"] == 4


def test_register_is_finance_all_only(register_world, api):
    _, wid, _, _ = register_world
    for user in ("usr-incharge-a", "usr-worker-a1"):
        c = api(user)
        assert c.get(f"{BASE}/spend/register", params={"window_id": wid}).status_code == 403
        assert c.get(f"{BASE}/spend/register/export.csv", params={"window_id": wid, "reason": "x y z"}).status_code == 403


def test_unknown_window_is_404(api, team):
    assert api("usr-principal").get(f"{BASE}/spend/register", params={"window_id": 999}).status_code == 404
