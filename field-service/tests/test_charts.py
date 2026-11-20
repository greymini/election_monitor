"""Chart endpoints for the web screens: spend by pay mode, fund source and day, page filters, money
flow, and the reports summary additions. Finance scope and report visibility apply in the query."""
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


@pytest.fixture
def world(conn, core, limits, team):
    p, a = team["principal"], team["incharge-a"]
    ledger.create_inflow(conn, core, p, to_member_id=p, amount=D("100000"), fund_source="party", limits=limits)
    for giver, to, amount in ((p, a, "40000"), (a, team["worker-a1"], "8000"), (p, team["incharge-b"], "9000")):
        row = ledger.create_advance(conn, core, giver, to_member_id=to, amount=D(amount), limits=limits)
        ledger.acknowledge(conn, row["txn_id"], to)
    ret = ledger.create_return(conn, core, team["worker-a1"], amount=D("1000"), limits=limits)
    ledger.acknowledge(conn, ret["txn_id"], a)
    food, travel = bucket_id(conn, "FOOD"), bucket_id(conn, "TRAVEL.FUEL")
    entries = [("worker-a1", food, "300", "cash", "blk-a", "area-a1", 0),
               ("worker-a1", travel, "700", "upi", "blk-a", "area-a1", 1),
               ("incharge-a", food, "2000", "cash", "blk-a", "area-a2", 1),
               ("incharge-b", travel, "5000", "bank_transfer", "blk-b", "area-b1", 3)]
    for who, b, amount, mode, block, area, days in entries:
        ledger.create_expense(conn, core, team[who], ExpenseInput(amount=D(amount), bucket_id=b, pay_mode=mode,
                                                                  block_id=block, area_id=area,
                                                                  txn_date=TODAY - timedelta(days=days)),
                              limits=limits)
    return team


@pytest.fixture
def api(settings, pool, core, fake_tg, world):
    app = create_app(settings, pool=pool, core=core, telegram_client=fake_tg)
    with TestClient(app):
        def as_(user):
            c = TestClient(app)
            c.headers["Authorization"] = f"Bearer {core.issue_token(user)}"
            return c
        yield as_


def groups(c, group_by, **params):
    r = c.get(f"{BASE}/spend/summary", params={"group_by": group_by, **params})
    assert r.status_code == 200, r.text
    return {g[group_by]: g["spend"] for g in r.json()["groups"]}


def test_spend_by_pay_mode_fund_source_and_day(api):
    p = api("usr-principal")
    assert groups(p, "pay_mode") == {"cash": "2300.00", "upi": "700.00", "bank_transfer": "5000.00"}
    by_fund = groups(p, "fund_source")
    assert sum(D(v) for v in by_fund.values()) == D("8000.00")       # spend only: the inflow and advances are not spend
    by_day = groups(p, "day")
    assert by_day[str(TODAY)] == "300.00" and by_day[str(TODAY - timedelta(days=1))] == "2700.00"


def test_page_filters_narrow_summary_and_tail(api, team):
    p = api("usr-principal")
    assert groups(p, "bucket", area_id="area-a1") == {groups(p, "bucket", area_id="area-a1").popitem()[0]: "300.00"} \
        or sum(D(v) for v in groups(p, "bucket", area_id="area-a1").values()) == D("1000.00")
    totals = p.get(f"{BASE}/spend/summary", params={"member_id": str(team["worker-a1"])}).json()["totals"]
    assert totals["spend_total"] == "1000.00"
    assert p.get(f"{BASE}/spend/summary", params={"block_id": "blk-b"}).json()["totals"]["spend_total"] == "5000.00"
    tail = p.get(f"{BASE}/spend/tail", params={"block_id": "blk-a"}).json()
    assert tail["spend_total"] == "3000.00" and tail["entries"] == 3


def test_money_flow_nodes_and_links(api, team):
    flow = api("usr-principal").get(f"{BASE}/spend/flow").json()
    links = {(l["source"], l["target"]): (l["value"], l["kind"]) for l in flow["links"]}
    m = lambda k: f"member:{team[k]}"
    assert links[("fund:party", m("principal"))] == ("100000.00", "inflow")
    assert links[(m("principal"), m("incharge-a"))] == ("40000.00", "advance_net")
    assert links[(m("incharge-a"), m("worker-a1"))] == ("7000.00", "advance_net")      # 8,000 out, 1,000 back
    assert links[(m("incharge-b"), "bucket:TRAVEL")] == ("5000.00", "spend")
    ids = {n["id"] for n in flow["nodes"]}
    assert {s for s, _ in links} | {t for _, t in links} <= ids


def test_money_flow_follows_finance_scope(api, team):
    flow = api("usr-incharge-a").get(f"{BASE}/spend/flow").json()
    members = {n["member_id"] for n in flow["nodes"] if n["kind"] == "member"}
    assert str(team["incharge-b"]) not in members
    assert all(l["kind"] != "inflow" for l in flow["links"])
    assert api("usr-worker-a1").get(f"{BASE}/spend/flow").status_code == 403


def test_reports_summary_has_open_counts_and_week_by_category(api, conn, core, team):
    from pulse.reports import service as reports

    member = conn.execute("SELECT * FROM field.member WHERE member_id = %s", (team["worker-a1"],)).fetchone()
    from pulse.common.clock import utcnow

    now = utcnow()
    health = conn.execute("SELECT category_id FROM field.report_category WHERE code = 'HEALTH'").fetchone()["category_id"]
    for urgent, cat in ((True, health), (False, None), (False, health)):
        r = reports.open_draft(conn, core, member, now=now)
        reports.add_note(conn, r["report_id"], "test note", actor=member["member_id"], now=now)
        if cat:
            reports.set_category(conn, r["report_id"], cat, actor=member["member_id"], now=now)
        if urgent:
            reports.set_urgency(conn, r["report_id"], "urgent", source="user", actor=member["member_id"], now=now)
        reports.file(conn, core, r["report_id"], how="user", actor=member["member_id"], now=now)
    s = api("usr-principal").get(f"{BASE}/reports/summary").json()
    assert s["open"] == {"urgent": 1, "unsorted": 1, "total": 3}
    assert sum(r["n"] for r in s["by_week_category"]) == 3
    assert any(c["code"] == "HEALTH" for c in s["categories"])
    assert s["by_area"][0]["name_hi"]
