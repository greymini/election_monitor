"""Returning money (flow_return): taps on an expired, older, replaced or already-used card, by a member
who became PIN-locked or was deactivated meanwhile, and redelivered taps. Never silent; at most one
return row; balances only move when a return is really recorded."""
from datetime import timedelta
from decimal import Decimal

from pulse.bot import texts_hi as T
from pulse.common.clock import utcnow
from pulse.ledger import service as ledger
from pulse.ledger.service import ExpenseInput
from pulse.metrics.ledger import member_balance_sql
from pulse.team import service as team_service
from tests.helpers import bucket_id
from tests.test_flow_return_ack import _me, pair  # noqa: F401  (pair is a fixture)

D = Decimal


def _returns(conn):
    return conn.execute("SELECT * FROM field.money_txn WHERE kind = 'return' ORDER BY created_at").fetchall()


def _bal(conn, member_id):
    return conn.execute(member_balance_sql(), {"member_id": member_id}).fetchone()["balance"]


def _to_confirm(worker, amount="5000", now=None):
    me = _me(worker)
    assert worker.tap(T.BTN_RETURN, card=me, now=now) == "await_amount"
    assert worker.say(amount, now=now) == "confirm"
    return worker.card


def test_confirm_after_the_draft_expired(conn, team, pair):
    _, worker = pair
    t0 = utcnow()
    card = _to_confirm(worker, now=t0)
    assert worker.tap(T.BTN_RECORD, card=card, now=t0 + timedelta(minutes=16)) == "card_expired"
    assert worker.toast == T.CARD_EXPIRED
    assert worker.mine[-1]["text"] == T.CARD_EXPIRED                       # the card itself says so too
    assert _returns(conn) == [] and _bal(conn, team["worker-a1"]) == D("7500.00")


def test_typed_amount_after_the_draft_expired_is_not_lost_silently(conn, team, pair):
    _, worker = pair
    t0 = utcnow()
    me = _me(worker)
    worker.tap(T.BTN_RETURN, card=me, now=t0)
    worker.say("5000", now=t0 + timedelta(minutes=16))
    assert any(m["text"] == T.DRAFT_EXPIRED_GENERIC for m in worker.mine)  # told the return draft ended
    assert _returns(conn) == []


def test_button_from_an_older_card_version(conn, team, pair):
    _, worker = pair
    me = _me(worker)
    worker.tap(T.BTN_RETURN, card=me)
    amount_card = worker.card                                             # version 1: asks the amount
    old_cancel = worker.data_for(T.BTN_CANCEL, amount_card)
    assert worker.say("5000") == "confirm"                                # version 2 sent below
    assert worker.tap_data(old_cancel, amount_card["message_id"]) == "card_stale"
    assert worker.toast == T.CARD_STALE
    assert worker.tap(T.BTN_RECORD) == "posted"                           # the live card still works
    assert len(_returns(conn)) == 1


def test_card_of_a_draft_replaced_by_a_new_message(conn, team, pair):
    _, worker = pair
    card = _to_confirm(worker)
    assert worker.say("500 chai nashta") == "confirm"                     # a new expense replaces the draft
    assert any(m["text"] == T.DRAFT_ABANDONED for m in worker.mine)
    assert worker.tap(T.BTN_RECORD, card=card) == "card_stale"
    assert worker.toast == T.CARD_STALE
    assert _returns(conn) == [] and _bal(conn, team["worker-a1"]) == D("7500.00")
    assert T.BTN_CONFIRM in worker.buttons()                              # the new expense card is live


def test_typed_text_on_the_confirm_step_resends_the_card(conn, pair):
    _, worker = pair
    card = _to_confirm(worker)
    assert worker.say("namaste") == "confirm"
    assert worker.text.startswith(T.USE_BUTTONS)                          # answered, draft kept
    assert worker.tap(T.BTN_RECORD, card=card) == "card_stale"            # the old copy is stale
    assert worker.tap(T.BTN_RECORD) == "posted"
    assert len(_returns(conn)) == 1


def test_record_tapped_twice(conn, team, pair):
    _, worker = pair
    card = _to_confirm(worker)
    data = worker.data_for(T.BTN_RECORD, card)
    assert worker.tap_data(data, card["message_id"]) == "posted"
    assert worker.tap_data(data, card["message_id"]) == "already_posted"
    [row] = _returns(conn)
    assert worker.toast == T.ALREADY_POSTED.format(txn_no=row["txn_no"])
    assert _bal(conn, team["worker-a1"]) == D("2500.00")


def test_redelivered_record_tap(conn, team, pair):
    _, worker = pair
    card = _to_confirm(worker)
    data = worker.data_for(T.BTN_RECORD, card)
    assert worker.tap_data(data, card["message_id"], update_id=880001) == "posted"
    assert worker.tap_data(data, card["message_id"], update_id=880001) == "duplicate"
    assert len(_returns(conn)) == 1 and _bal(conn, team["worker-a1"]) == D("2500.00")


def test_balance_spent_meanwhile(conn, core, limits, team, pair):
    """The amount was fine when typed; by the tap, more was spent. Refused with the new balance."""
    _, worker = pair
    card = _to_confirm(worker, "7000")
    ledger.create_expense(conn, core, team["worker-a1"], ExpenseInput(amount=D("1000"),
                          bucket_id=bucket_id(conn, "FOOD")), limits=limits)
    assert worker.tap(T.BTN_RECORD, card=card) == "return_too_much"
    assert worker.text.startswith(T.RETURN_TOO_MUCH.format(balance="₹6,500"))
    assert _returns(conn) == []


def test_pin_locked_after_the_draft_opened(conn, team, pair):
    """A lock stops every bot money action, including a return already at the confirm step."""
    _, worker = pair
    card = _to_confirm(worker)
    conn.execute("UPDATE field.member SET pin_locked_at = now() WHERE member_id = %s", (team["worker-a1"],))
    assert worker.tap(T.BTN_RECORD, card=card) == "pin_locked"
    assert worker.text == T.PIN_LOCKED_MONEY
    assert _returns(conn) == [] and _bal(conn, team["worker-a1"]) == D("7500.00")


def test_deactivated_member_taps_record(conn, core, team, pair):
    _, worker = pair
    card = _to_confirm(worker)
    team_service.update_member(conn, core, "ac-32", team["worker-a1"], {"active": False})
    assert worker.tap(T.BTN_RECORD, card=card) == "unlinked"
    assert worker.text == T.UNREGISTERED
    assert _returns(conn) == []


def test_someone_else_taps_the_card_data(conn, team, pair):
    """Another member sending the same callback data from their own chat finds no such draft."""
    incharge, worker = pair
    card = _to_confirm(worker)
    data = worker.data_for(T.BTN_RECORD, card)
    assert incharge.tap_data(data, 4242) == "card_stale"
    assert incharge.toast == T.CARD_STALE
    assert _returns(conn) == []
    assert worker.tap(T.BTN_RECORD, card=card) == "posted"                # the owner's card is untouched
