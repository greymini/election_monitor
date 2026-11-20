"""GET /spend/need-vs-spend (finance scope all) and GET /team/activity (admin or finance scope team)."""
from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from pulse.api.main import create_app
from pulse.ledger import service as ledger
from pulse.ledger.service import ExpenseInput
from tests import updates as U
from tests.helpers import bucket_id, link
from tests.test_flow_expense import Chat
from tests.triage_world import file_report

BASE = "/acs/32/field"
D = Decimal


@pytest.fixture
def api(settings, pool, core, fake_tg):
    app = create_app(settings, pool=pool, core=core, telegram_client=fake_tg)
    with TestClient(app):
        def as_(user):
            c = TestClient(app)
            c.headers["Authorization"] = f"Bearer {core.issue_token(user)}"
            return c
        yield as_


def test_need_vs_spend_route(api, conn, core, limits, team):
    file_report(conn, core, team["worker-a1"])
    ledger.create_expense(conn, core, team["principal"], ExpenseInput(
        amount=D("4000"), bucket_id=bucket_id(conn, "SOCIAL"), block_id="blk-b", area_id="area-b1",
        reach_count=40, reach_unit="person"), limits=limits)
    codes = {u: api(u).get(f"{BASE}/spend/need-vs-spend").status_code
             for u in ("usr-principal", "usr-incharge-a", "usr-worker-a1")}
    assert codes == {"usr-principal": 200, "usr-incharge-a": 403, "usr-worker-a1": 403}
    body = api("usr-principal").get(f"{BASE}/spend/need-vs-spend").json()
    assert body["interpretation"] == "association, not cause"
    by = {a["area_id"]: a for a in body["areas"]}
    assert len(by) == 8
    a1, b1, a3 = by["area-a1"], by["area-b1"], by["area-a3"]
    assert a1["open_reports"] == 1 and a1["electors"] == 4210 and a1["name_hi"] == "डेमो पंचायत क1"
    assert b1["spend"] == "4000.00" and b1["reach"] == {"person": 40} and b1["spend_per_1000"] == "781.25"
    assert a3["electors"] is None and a3["need_rank"] is None and a3["rank_gap"] is None
    assert a1["need_rank"] == "100.0" and a1["spend_rank"] == "0.0" and a1["rank_gap"] == "100.0"
    assert body["areas"][0]["area_id"] == "area-a1"                  # largest gap first
    later = api("usr-principal").get(f"{BASE}/spend/need-vs-spend", params={"date_from": "2099-01-01"}).json()
    assert {a["area_id"]: a["spend"] for a in later["areas"]}["area-b1"] == "0.00"


def test_team_activity(api, conn, core, limits, pool, bot, team):
    link(pool, bot, team["worker-a1"], U.USER_A)
    Chat(pool, bot, U.USER_A).say("namaste")                          # last seen
    ledger.create_expense(conn, core, team["worker-a1"], ExpenseInput(amount=D("300"), bucket_id=bucket_id(conn, "FOOD")),
                          limits=limits)
    file_report(conn, core, team["worker-a1"])
    admin = api("usr-principal").get(f"{BASE}/team/activity").json()
    rows = {r["member_id"]: r for r in admin["members"]}
    assert len(rows) == 6
    a1 = rows[str(team["worker-a1"])]
    assert a1["active_days"] == 1 and a1["entries"] == 1 and a1["reports"] == 1
    assert a1["last_seen_at"] is not None and a1["logging_lag_days"] == "0.0"
    b1 = rows[str(team["worker-b1"])]
    assert b1["active_days"] == 0 and b1["last_seen_at"] is None and b1["logging_lag_days"] is None
    team_view = api("usr-incharge-a").get(f"{BASE}/team/activity").json()
    assert {r["member_id"] for r in team_view["members"]} == {str(team[k]) for k in ("incharge-a", "worker-a1", "worker-a2")}
    assert api("usr-worker-a1").get(f"{BASE}/team/activity").status_code == 403
    assert api("usr-principal").get(f"{BASE}/team/activity", params={"days": 0}).status_code == 422
