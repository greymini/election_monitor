"""Expense links (slice 3): the optional "किस रिपोर्ट के लिए?" step in "और विवरण", report_id and
issue_id on POST /txns, and linked spend on report and issue detail within the caller's finance scope."""
from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from pulse.api.main import create_app
from pulse.bot import texts_hi as T
from pulse.ledger import service as ledger
from pulse.ledger.service import ExpenseInput
from pulse.reports import access, issues
from pulse.common.clock import utcnow
from tests.helpers import bucket_id, link
from tests.test_flow_expense import Chat
from tests.triage_world import file_report

BASE = "/acs/32/field"
D = Decimal


@pytest.fixture
def api(settings, pool, core, fake_tg, storage):
    app = create_app(settings, pool=pool, core=core, telegram_client=fake_tg, storage=storage)
    with TestClient(app):
        def as_(user):
            c = TestClient(app)
            c.headers["Authorization"] = f"Bearer {core.issue_token(user)}"
            return c
        yield as_


def _walk_details(chat, choose=None):
    """Open "और विवरण" and skip every step; at the report step tap `choose` if given. Returns the
    texts of the steps seen."""
    seen = []
    chat.tap(T.BTN_DETAILS)
    for _ in range(12):
        text = chat.text
        seen.append(text)
        if T.ASK_REPORT_LINK in text and choose:
            label = next(b for b in chat.buttons() if b.startswith(choose))
            chat.tap(label)
            continue
        if T.BTN_SKIP not in chat.buttons():
            break
        chat.tap(T.BTN_SKIP)
    return seen


def test_bot_step_lists_open_reports_and_links(conn, core, pool, bot, team):
    r = file_report(conn, core, team["worker-a1"])
    closed = file_report(conn, core, team["worker-a1"])
    conn.execute("UPDATE field.report SET status = 'resolved' WHERE report_id = %s", (closed["report_id"],))
    link(pool, bot, team["worker-a1"])
    chat = Chat(pool, bot)
    assert chat.say("1500 chai nashta") == "confirm"
    seen = _walk_details(chat, choose=r["report_no"])
    step = next(t for t in seen if T.ASK_REPORT_LINK in t)
    assert r["report_no"] in str(chat.fake.cards) and closed["report_no"] not in step
    assert T.LINE_REPORT.format(report_no=r["report_no"]) in chat.text
    assert chat.tap(T.BTN_CONFIRM) == "posted"
    row = conn.execute("SELECT report_id FROM field.money_txn").fetchone()
    assert row["report_id"] == r["report_id"]


def test_no_open_reports_no_step(conn, pool, bot, team):
    link(pool, bot, team["worker-a1"])
    chat = Chat(pool, bot)
    chat.say("1500 chai nashta")
    assert all(T.ASK_REPORT_LINK not in t for t in _walk_details(chat))
    chat.tap(T.BTN_CONFIRM)
    assert conn.execute("SELECT report_id FROM field.money_txn").fetchone()["report_id"] is None


def test_api_accepts_report_and_issue(api, conn, core, team):
    r = file_report(conn, core, team["worker-a1"])
    hidden = file_report(conn, core, team["worker-b1"], category="SCHEME")
    v = access.viewer_for(conn, team["principal"])
    issue = issues.create(conn, core, v, now=utcnow(), title="डेमो मुद्दा", area_id="area-a1")
    rep = api("usr-worker-a1")
    body = {"kind": "expense", "amount": "700", "bucket_id": bucket_id(conn, "FOOD")}
    ok = rep.post(f"{BASE}/txns", json={**body, "report_id": str(r["report_id"])})
    assert ok.status_code == 201 and ok.json()["txn"]["report_id"] == str(r["report_id"])
    assert rep.post(f"{BASE}/txns", json={**body, "report_id": str(hidden["report_id"])}).status_code == 422
    assert rep.post(f"{BASE}/txns", json={**body, "issue_id": str(issue["issue_id"])}).status_code == 201
    assert rep.post(f"{BASE}/txns", json={"kind": "return", "amount": "10", "report_id": str(r["report_id"])}
                    ).status_code == 422


def test_linked_spend_follows_finance_scope(api, conn, core, limits, team):
    r = file_report(conn, core, team["worker-a1"])
    food = bucket_id(conn, "FOOD")
    for who, amount in (("worker-a1", "500"), ("principal", "1000")):
        ledger.create_expense(conn, core, team[who], ExpenseInput(amount=D(amount), bucket_id=food,
                                                                   report_id=r["report_id"]), limits=limits)
    voided = ledger.create_expense(conn, core, team["worker-a1"], ExpenseInput(amount=D("99"), bucket_id=food,
                                                                                report_id=r["report_id"]), limits=limits)
    ledger.void(conn, voided["txn_id"], team["worker-a1"], "undo", limits=limits)

    def spend(user):
        return api(user).get(f"{BASE}/reports/{r['report_id']}").json().get("linked_spend")

    assert spend("usr-principal") == {"total": "1500.00", "entries": 2, "scope": "all"}
    assert spend("usr-incharge-a") == {"total": "500.00", "entries": 1, "scope": "team"}   # own subtree only
    assert spend("usr-worker-a1") == {"total": "500.00", "entries": 1, "scope": "own"}
    conn.execute("UPDATE field.member SET finance_scope = 'none' WHERE member_id = %s", (team["worker-a1"],))
    detail = api("usr-worker-a1").get(f"{BASE}/reports/{r['report_id']}").json()
    assert "linked_spend" not in detail and detail["report"]["report_id"] == str(r["report_id"])


def test_issue_spend_counts_its_reports(api, conn, core, limits, team):
    r = file_report(conn, core, team["worker-a1"])
    food = bucket_id(conn, "FOOD")
    ledger.create_expense(conn, core, team["principal"], ExpenseInput(amount=D("300"), bucket_id=food,
                                                                      report_id=r["report_id"]), limits=limits)
    v = access.viewer_for(conn, team["principal"])
    issue = issues.create(conn, core, v, now=utcnow(), from_report_id=r["report_id"])      # linked later
    ledger.create_expense(conn, core, team["principal"], ExpenseInput(amount=D("200"), bucket_id=food,
                                                                      issue_id=issue["issue_id"]), limits=limits)
    got = api("usr-principal").get(f"{BASE}/issues/{issue['issue_id']}").json()["linked_spend"]
    assert got == {"total": "500.00", "entries": 2, "scope": "all"}
    conn.execute("UPDATE field.member SET finance_scope = 'none', is_coordinator = true WHERE member_id = %s",
                 (team["incharge-b"],))
    assert "linked_spend" not in api("usr-incharge-b").get(f"{BASE}/issues/{issue['issue_id']}").json()
