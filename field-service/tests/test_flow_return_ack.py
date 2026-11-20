"""Returning money by bot and the extended "my account" screen."""
from datetime import timedelta
from decimal import Decimal

import pytest

from pulse.bot import texts_hi as T
from pulse.bot.ui import CB_MENU_ME
from pulse.common.clock import utcnow
from pulse.ledger import service as ledger
from pulse.ledger.service import ExpenseInput
from tests import updates as U
from tests.helpers import bucket_id, link
from tests.test_flow_expense import Chat

A_NAME, W_NAME = "डेमो प्रभारी क", "डेमो कार्यकर्ता क1"


@pytest.fixture
def pair(pool, bot, team, conn, core, limits, drain):
    """In-charge A (USER_B) gave worker A1 (USER_C) ₹20,000, acknowledged; worker spent ₹12,500."""
    link(pool, bot, team["incharge-a"], U.USER_B)
    link(pool, bot, team["worker-a1"], U.USER_C)
    row = ledger.create_advance(conn, core, team["incharge-a"], to_member_id=team["worker-a1"], amount=Decimal("20000"),
                                limits=limits)
    ledger.acknowledge(conn, row["txn_id"], team["worker-a1"])
    ledger.create_expense(conn, core, team["worker-a1"], ExpenseInput(amount=Decimal("12500"),
                          bucket_id=bucket_id(conn, "TRAVEL")), limits=limits)
    return Chat(pool, bot, U.USER_B, after=drain), Chat(pool, bot, U.USER_C, after=drain)


def _me(chat):
    chat.say("namaste")
    chat.tap_data(CB_MENU_ME, chat.mine[-1]["message_id"])
    return chat.mine[-1]


def test_return_flow_with_single_giver(conn, team, pair):
    incharge, worker = pair
    me = _me(worker)
    assert worker.tap(T.BTN_RETURN, card=me) == "await_amount"            # one giver: no question who
    assert worker.text == T.ASK_RETURN_AMOUNT.format(name=A_NAME, balance="₹7,500")
    assert worker.say("8000") == "await_amount"
    assert worker.text.startswith(T.RETURN_TOO_MUCH.format(balance="₹7,500"))
    assert worker.say("7500") == "confirm"
    assert worker.card["text"] == f"₹7,500 लौटाया → {A_NAME} · आज · नकद"
    assert worker.tap(T.BTN_RECORD) == "posted"
    posted = worker.mine[-1]["text"]
    assert T.RECEIVER_QUEUED.format(name=A_NAME) in posted and T.BALANCE_HELD.format(amount="₹0") in posted

    card = incharge.card
    assert card["text"].startswith(T.ACK_REQUEST_RETURN.format(giver=W_NAME, amount="₹7,500", txn_no="32-L-000001"))
    assert incharge.tap(T.BTN_ACK_YES) == "acknowledged"
    row = conn.execute("SELECT * FROM field.money_txn WHERE kind = 'return'").fetchone()
    assert row["status"] == "posted" and row["to_member_id"] == team["incharge-a"]


def test_return_asks_who_when_several_gave(conn, core, limits, team, pair):
    _, worker = pair
    gift = ledger.create_advance(conn, core, team["principal"], to_member_id=team["worker-a1"], amount=Decimal("1000"),
                                 limits=limits)
    ledger.acknowledge(conn, gift["txn_id"], team["worker-a1"])
    me = _me(worker)
    assert worker.tap(T.BTN_RETURN, card=me) == "pick_giver"
    assert worker.buttons() == ["डेमो प्रत्याशी", A_NAME, T.BTN_CANCEL]      # most recent giver first
    assert worker.tap("डेमो प्रत्याशी") == "await_amount"


def test_nothing_to_return(conn, core, limits, team, pool, bot):
    link(pool, bot, team["worker-b1"], U.USER_C)
    chat = Chat(pool, bot, U.USER_C)
    me = _me(chat)
    assert chat.tap(T.BTN_RETURN, card=me) == "no_giver"
    assert chat.mine[-1]["text"] == T.NO_GIVER


def test_me_shows_transit_dispute_waiting_and_team(conn, core, limits, team, pair):
    incharge, worker = pair
    p = team["principal"]
    pending = ledger.create_advance(conn, core, p, to_member_id=team["incharge-a"], amount=Decimal("30000"), limits=limits)
    disputed = ledger.create_advance(conn, core, p, to_member_id=team["incharge-a"], amount=Decimal("4000"), limits=limits)
    ledger.dispute(conn, disputed["txn_id"], team["incharge-a"], "amount_differs")
    ledger.create_advance(conn, core, team["incharge-a"], to_member_id=team["worker-a2"], amount=Decimal("2500"),
                          limits=limits)                                    # in transit from the in-charge
    shown = _me(incharge)
    text = shown["text"]
    assert T.BALANCE_OWED.format(amount="₹22,500") in text                 # passed on 20,000 + 2,500 pending; 30,000 not yet acknowledged
    assert T.ME_IN_TRANSIT.format(amount="₹2,500") in text
    assert T.ME_DISPUTED.format(amount="₹4,000") in text
    assert T.ME_WAITING_ENTRY.format(txn_no=pending["txn_no"], amount="₹30,000", name="डेमो प्रत्याशी") in text
    assert T.ME_TEAM_ENTRY.format(name=W_NAME, balance=T.TEAM_HELD.format(amount="₹7,500")) in text
    assert T.ME_TEAM_ENTRY.format(name="डेमो कार्यकर्ता क2", balance=T.TEAM_HELD.format(amount="₹0")) in text

    assert incharge.tap(T.BTN_ACK_OPEN.format(txn_no=pending["txn_no"]), card=shown) == "ack_card"
    assert incharge.tap(T.BTN_ACK_YES) == "acknowledged"
    assert conn.execute("SELECT status FROM field.money_txn WHERE txn_id = %s", (pending["txn_id"],)).fetchone()["status"] == "posted"


def test_team_lines_only_for_finance_team(pair):
    _, worker = pair
    assert T.ME_TEAM not in _me(worker)["text"]


def test_last_month(conn, core, limits, team, pair):
    _, worker = pair
    now = utcnow()
    last_month = (now.replace(day=1) - timedelta(days=3))
    ledger.create_expense(conn, core, team["worker-a1"], ExpenseInput(amount=Decimal("900"), bucket_id=bucket_id(conn, "FOOD"),
                          txn_date=last_month.date()), limits=limits, now=now)
    me = _me(worker)
    assert worker.tap(T.BTN_LAST_MONTH, card=me) == "last_month"
    text = worker.mine[-1]["text"]
    assert text.splitlines()[0] == T.LAST_MONTH_TITLE.format(month=T.MONTHS[last_month.month], year=last_month.year)
    assert T.LM_SPENT.format(amount="₹900", count=1) in text
    assert T.LM_TOP_ENTRY.format(name="भोजन और नाश्ता", amount="₹900") in text
    assert worker.tap(T.BTN_ME) == "me"
