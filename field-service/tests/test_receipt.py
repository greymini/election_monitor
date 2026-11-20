"""The expense receipt photo (deferred from 1b) and media sent while an expense draft is open."""
from __future__ import annotations

import itertools
from decimal import Decimal

import pytest

from pulse.bot import flow_expense
from pulse.bot import texts_hi as T
from pulse.ledger import service as ledger
from pulse.ledger.errors import Invalid
from pulse.ledger.service import ExpenseInput
from pulse.reports import media
from tests import updates as U
from tests.helpers import bucket_id, link
from tests.test_flow_report import Reporter

_ids = itertools.count(600_000)


@pytest.fixture
def rep(pool, bot, team):
    link(pool, bot, team["worker-a1"])
    return Reporter(pool, bot)


def _txn(conn):
    return conn.execute("SELECT * FROM field.money_txn").fetchone()


def _reports(conn):
    return conn.execute("SELECT * FROM field.report ORDER BY created_at").fetchall()


def _to_receipt_step(rep):
    rep.say("1500 chai nashta")
    rep.tap(T.BTN_DETAILS)
    for _ in range(4):                                     # sub-head, date, pay mode, paid from
        rep.tap(T.BTN_SKIP)
    assert T.ASK_RECEIPT in rep.card["text"]


def test_photo_in_receipt_step_is_the_receipt(conn, rep):
    _to_receipt_step(rep)
    assert rep.photo("rc1") == "receipt_added"
    assert T.LINE_RECEIPT in rep.card["text"] and T.ASK_REMARK in rep.card["text"]
    rep.tap(T.BTN_SKIP)
    rep.tap(T.BTN_CONFIRM)
    asset = conn.execute("SELECT * FROM field.media_asset").fetchone()
    assert _txn(conn)["receipt_asset_id"] == asset["asset_id"] and asset["kind"] == "photo"
    assert _reports(conn) == []
    assert conn.execute("SELECT kind FROM field.job").fetchone()["kind"] == "media_fetch"   # still downloaded


def test_pdf_receipt(conn, rep):
    _to_receipt_step(rep)
    assert rep.send(U.document_msg(next(_ids), "bill", "application/pdf")) == "receipt_added"


def test_photo_in_another_state_asks_receipt_or_report(conn, rep):
    rep.say("1500 chai nashta")
    assert rep.photo("q1") == "held_asked"
    question = rep.mine[-1]
    assert question["text"] == T.HOLD_QUESTION
    assert [b["text"] for row in question["reply_markup"]["inline_keyboard"] for b in row] == \
        [T.BTN_HOLD_RECEIPT, T.BTN_HOLD_REPORT, T.BTN_HOLD_LATER]
    assert rep.tap(T.BTN_HOLD_RECEIPT, card=question) == "receipt_added"
    assert T.LINE_RECEIPT in rep.card["text"]
    rep.tap(T.BTN_CONFIRM)
    assert _txn(conn)["receipt_asset_id"] is not None and _reports(conn) == []


def test_choosing_new_report_closes_the_expense(conn, rep):
    rep.say("1500 chai nashta")
    rep.photo("q2", caption="chapakal kharab")
    assert rep.tap(T.BTN_HOLD_REPORT, card=rep.mine[-1]) == "held_to_report"
    [r] = _reports(conn)
    assert r["status"] == "draft" and r["note_text"] == "chapakal kharab"
    assert conn.execute("SELECT count(*) AS n FROM field.report_media").fetchone()["n"] == 1
    assert conn.execute("SELECT flow FROM field.tg_session").fetchone()["flow"] == "report"
    assert _txn(conn) is None


def test_later_opens_a_report_when_the_expense_is_posted(conn, rep):
    rep.say("1500 chai nashta")
    expense_card = rep.card
    rep.photo("q3")
    assert rep.tap(T.BTN_HOLD_LATER, card=rep.mine[-1]) == "held_kept"
    assert rep.tap(T.BTN_CONFIRM, card=expense_card) == "posted"
    assert _txn(conn)["receipt_asset_id"] is None
    [r] = _reports(conn)
    assert conn.execute("SELECT count(*) AS n FROM field.report_media WHERE report_id = %s",
                        (r["report_id"],)).fetchone()["n"] == 1
    assert T.HELD_TO_REPORT in [m["text"] for m in rep.mine]


def test_album_during_expense_one_question_rest_becomes_report(conn, rep):
    rep.say("1500 chai nashta")
    for i in range(3):
        rep.photo(f"alb{i}", group="exp-album")
    questions = [m for m in rep.mine if m["text"] == T.HOLD_QUESTION]
    assert len(questions) == 1
    rep.tap(T.BTN_HOLD_RECEIPT, card=questions[0])
    rep.tap(T.BTN_CONFIRM)
    receipt = _txn(conn)["receipt_asset_id"]
    assert receipt == conn.execute("SELECT asset_id FROM field.media_asset WHERE tg_file_id = 'alb0'").fetchone()["asset_id"]
    [r] = _reports(conn)
    assert conn.execute("SELECT count(*) AS n FROM field.report_media WHERE report_id = %s",
                        (r["report_id"],)).fetchone()["n"] == 2


def test_location_during_expense_cannot_be_a_receipt(conn, rep):
    rep.say("1500 chai nashta")
    rep.send(U.location_msg(next(_ids)))
    question = rep.mine[-1]
    assert question["text"] == T.HOLD_QUESTION_NO_RECEIPT
    assert T.BTN_HOLD_RECEIPT not in [b["text"] for row in question["reply_markup"]["inline_keyboard"] for b in row]


def test_cancelled_expense_still_keeps_held_media(conn, rep):
    rep.say("1500 chai nashta")
    expense_card = rep.card
    rep.photo("q4")
    assert rep.tap(T.BTN_CANCEL, card=expense_card) == "cancelled"
    [r] = _reports(conn)
    assert conn.execute("SELECT count(*) AS n FROM field.report_media").fetchone()["n"] == 1
    assert _txn(conn) is None and r["status"] == "draft"


def test_receipt_must_belong_to_the_member(conn, core, limits, team):
    other = conn.execute("SELECT * FROM field.member WHERE member_id = %s", (team["worker-a2"],)).fetchone()
    item = media.Item(tg_kind="photo", kind="photo", file_id="x", file_unique_id="x", file_size=1, mime="image/jpeg",
                      duration_s=None, width=1, height=1, media_group_id=None)
    asset = media.register(conn, other, item)
    with pytest.raises(Invalid):
        ledger.create_expense(conn, core, team["worker-a1"], ExpenseInput(amount=Decimal("100"),
                              bucket_id=bucket_id(conn, "FOOD"), receipt_asset_id=asset["asset_id"]), limits=limits)
    row = ledger.create_expense(conn, core, team["worker-a2"], ExpenseInput(amount=Decimal("100"),
                                bucket_id=bucket_id(conn, "FOOD"), receipt_asset_id=asset["asset_id"]), limits=limits)
    assert row["receipt_asset_id"] == asset["asset_id"]


def test_receipt_step_order():
    assert flow_expense.DETAIL_STATES.index(flow_expense.S_D_RECEIPT) == len(flow_expense.DETAIL_STATES) - 2
