"""Giving money by bot (LLD §5.2): menu flow, typed shortcut, acknowledgement card, dispute."""
from decimal import Decimal

import pytest

from pulse.bot import texts_hi as T
from pulse.bot.ui import CB_MENU_GIVE
from tests import updates as U
from tests.helpers import link
from tests.test_flow_expense import Chat

P_NAME, A_NAME, B_NAME = "डेमो प्रत्याशी", "डेमो प्रभारी क", "डेमो प्रभारी ख"


@pytest.fixture
def phones(pool, bot, team, drain):
    """Principal on USER_A, in-charge A on USER_B; the worker runs after every update."""
    link(pool, bot, team["principal"], U.USER_A)
    link(pool, bot, team["incharge-a"], U.USER_B)
    return Chat(pool, bot, U.USER_A, after=drain), Chat(pool, bot, U.USER_B, after=drain)


def _txns(conn):
    return conn.execute("SELECT * FROM field.money_txn ORDER BY created_at").fetchall()


def _menu(chat):
    chat.say("namaste")
    return chat.mine[-1]


def test_give_button_only_for_members_who_may_give(pool, bot, team, phones):
    principal, _ = phones
    rows = [[b["text"] for b in r] for r in _menu(principal)["reply_markup"]["inline_keyboard"]]
    assert rows == [[T.BTN_EXPENSE, T.BTN_GIVE], [T.BTN_REPORT, T.BTN_ME]]
    link(pool, bot, team["worker-a1"], U.USER_C)
    worker = Chat(pool, bot, U.USER_C)
    assert [b["text"] for b in _menu(worker)["reply_markup"]["inline_keyboard"][0]] == [T.BTN_EXPENSE, T.BTN_REPORT, T.BTN_ME]


def test_menu_flow_and_acknowledgement(conn, phones, team):
    principal, incharge = phones
    menu = _menu(principal)
    assert principal.tap_data(CB_MENU_GIVE, menu["message_id"]) == "pick_person"
    people = principal.buttons()
    assert people[:2] == [A_NAME, B_NAME]                       # direct reports first
    assert len([p for p in people if p != T.BTN_CANCEL]) == 5
    assert principal.tap(A_NAME) == "await_amount"
    assert principal.text == T.ASK_ADVANCE_AMOUNT.format(name=A_NAME)
    assert principal.say("50000") == "confirm"
    assert principal.card["text"] == f"₹50,000 → {A_NAME} · आज · नकद"
    assert principal.tap(T.BTN_PURPOSE) == "await_purpose"
    assert principal.say("booth ka kharcha") == "confirm"
    assert T.LINE_PURPOSE.format(purpose="booth ka kharcha") in principal.card["text"]
    assert principal.tap(T.BTN_RECORD) == "posted"

    [row] = _txns(conn)
    assert row["kind"] == "advance" and row["status"] == "pending_ack" and row["remark"] == "booth ka kharcha"
    posted = principal.mine[-1]
    assert posted["text"].startswith(T.POSTED.format(txn_no="32-A-000001"))
    assert T.RECEIVER_QUEUED.format(name=A_NAME) in posted["text"] and "शेष" not in posted["text"]

    card = incharge.card
    assert card["text"].startswith(T.ACK_REQUEST_ADVANCE.format(giver=P_NAME, amount="₹50,000", txn_no="32-A-000001"))
    assert incharge.buttons() == [T.BTN_ACK_YES, T.BTN_ACK_DIFF, T.BTN_ACK_NO]
    assert incharge.tap(T.BTN_ACK_YES) == "acknowledged"
    assert incharge.mine[-1]["text"] == (T.ACKED_CARD.format(amount="₹50,000", txn_no="32-A-000001") + "\n"
                                         + T.BALANCE_HELD.format(amount="₹50,000"))
    assert principal.mine[-1]["text"] == T.ACK_NOTICE_TO_GIVER.format(receiver=A_NAME, amount="₹50,000",
                                                                      txn_no="32-A-000001")
    assert _txns(conn)[0]["status"] == "posted"


def test_double_tap_and_redelivered_acknowledgement_change_one_row(conn, phones):
    principal, incharge = phones
    principal.say("diya 5000 प्रभारी क")
    principal.tap(T.BTN_RECORD)
    card = incharge.card
    data = incharge.data_for(T.BTN_ACK_YES, card)
    assert incharge.tap_data(data, card["message_id"], update_id=880_001) == "acknowledged"
    assert incharge.tap_data(data, card["message_id"], update_id=880_001) == "duplicate"
    assert incharge.tap_data(data, card["message_id"]) == "ack_already_posted"
    assert incharge.toast == T.ALREADY_ACKED
    assert incharge.tap_data(incharge.data_for(T.BTN_ACK_NO, card), card["message_id"]) == "ack_already_posted"
    assert conn.execute("SELECT count(*) AS n FROM field.txn_audit WHERE action = 'ack'").fetchone()["n"] == 1
    assert _txns(conn)[0]["status"] == "posted"


def test_double_tap_on_record_creates_one_advance(conn, phones):
    principal, _ = phones
    principal.say("diya 5000 प्रभारी क")
    card = principal.card
    data = principal.data_for(T.BTN_RECORD, card)
    assert principal.tap_data(data, card["message_id"]) == "posted"
    assert principal.tap_data(data, card["message_id"]) == "already_posted"
    assert len(_txns(conn)) == 1


@pytest.mark.parametrize("button,reason", [(T.BTN_ACK_DIFF, "amount_differs"), (T.BTN_ACK_NO, "not_received")])
def test_dispute_tells_giver_and_every_treasurer(conn, pool, bot, team, phones, button, reason, drain):
    principal, incharge = phones
    conn.execute("UPDATE field.member SET finance_scope = 'all' WHERE member_id = %s", (team["incharge-b"],))
    link(pool, bot, team["incharge-b"], U.USER_C)
    treasurer = Chat(pool, bot, U.USER_C, after=drain)
    principal.say("diya 7000 प्रभारी क")
    principal.tap(T.BTN_RECORD)
    assert incharge.tap(button) == "disputed"
    assert incharge.mine[-1]["text"] == T.DISPUTED_CARD.format(reason=T.DISPUTE_REASONS[reason], txn_no="32-A-000001")
    notice = T.DISPUTE_NOTICE.format(receiver=A_NAME, txn_no="32-A-000001", amount="₹7,000", giver=P_NAME,
                                     reason=T.DISPUTE_REASONS[reason])
    assert [m["text"] for m in principal.mine].count(notice) == 1          # giver, also a treasurer: told once
    assert treasurer.mine[-1]["text"] == notice
    assert [m["text"] for m in incharge.mine].count(notice) == 0          # not the one who raised it
    assert _txns(conn)[0]["status"] == "disputed"


def test_unlinked_receiver_leaves_advance_pending_and_giver_is_told(conn, phones):
    principal, _ = phones
    principal.say("diya 3000 प्रभारी ख")                                   # in-charge B has no Telegram link
    assert principal.tap(T.BTN_RECORD) == "posted"
    assert T.RECEIVER_NOT_LINKED.format(name=B_NAME) in principal.mine[-1]["text"]
    assert _txns(conn)[0]["status"] == "pending_ack"


def test_failed_send_leaves_advance_pending_and_giver_is_told(conn, phones, fake_tg):
    principal, _ = phones
    fake_tg.fail_chats.add(U.USER_B["id"])
    principal.say("diya 3000 प्रभारी क")
    assert principal.tap(T.BTN_RECORD) == "posted"                     # the worker then finds the bot blocked
    texts = [m["text"] for m in principal.mine]
    assert any(T.RECEIVER_QUEUED.format(name=A_NAME) in t for t in texts)
    assert texts[-1] == T.RECEIVER_UNDELIVERED.format(name=A_NAME, txn_no="32-A-000001")
    assert conn.execute("SELECT status FROM field.alert_message WHERE kind = 'ack_request'").fetchone()["status"] == "blocked"
    assert _txns(conn)[0]["status"] == "pending_ack"


# ---- typed shortcut ---------------------------------------------------------------------------

def test_shortcut_one_match_goes_to_confirm(phones):
    principal, _ = phones
    assert principal.say("दिया 50000 प्रभारी क") == "name_matched"
    assert principal.card["text"] == f"₹50,000 → {A_NAME} · आज · नकद"
    assert principal.say("diya 2000 in-charge b") == "name_matched"       # Roman, via the core display name
    assert principal.card["text"].startswith(f"₹2,000 → {B_NAME}")


def test_shortcut_several_matches_show_a_list(phones):
    principal, _ = phones
    assert principal.say("diya 5000 प्रभारी") == "name_several"
    assert principal.text.startswith(T.NAME_SEVERAL.format(name="प्रभारी"))
    assert principal.buttons() == [A_NAME, B_NAME, T.BTN_ALL_PEOPLE, T.BTN_CANCEL]
    assert principal.tap(T.BTN_ALL_PEOPLE) == "pick_person"
    assert len(principal.buttons()) == 6
    assert principal.tap(B_NAME) == "confirm"                              # amount came with the shortcut


def test_shortcut_no_match_shows_everyone(phones):
    principal, _ = phones
    assert principal.say("diya 5000 ramesh") == "name_not_found"
    assert principal.text.startswith(T.NAME_NOT_FOUND.format(name="ramesh"))
    assert len([b for b in principal.buttons() if b != T.BTN_CANCEL]) == 5


def test_shortcut_only_searches_own_subtree(phones):
    _, incharge = phones
    assert incharge.say("diya 500 कार्यकर्ता ख1") == "name_not_found"      # worker B1 is in the other branch
    assert set(incharge.buttons()) == {"डेमो कार्यकर्ता क1", "डेमो कार्यकर्ता क2", T.BTN_CANCEL}


def test_shortcut_without_amount_asks_for_it(phones):
    principal, _ = phones
    assert principal.say("diya प्रभारी क") == "name_matched"
    assert principal.text == T.ASK_ADVANCE_AMOUNT.format(name=A_NAME)


def test_shortcut_big_amount_is_confirmed(phones):
    principal, _ = phones
    principal.say("diya 15 lakh प्रभारी क")
    assert principal.text == T.CONFIRM_BIG_AMOUNT.format(amount="₹15,00,000")
    assert principal.tap(T.BTN_YES_CORRECT) == "confirm"


def test_member_who_may_not_give_is_told(pool, bot, team, phones):
    link(pool, bot, team["worker-a1"], U.USER_C)
    worker = Chat(pool, bot, U.USER_C)
    assert worker.say("diya 500 कार्यकर्ता क2") == "no_permission"
    assert worker.text == T.NO_ADVANCE_PERMISSION


# ---- undo and replaced drafts -----------------------------------------------------------------

def test_undo_pending_advance_tells_receiver(conn, phones):
    principal, incharge = phones
    principal.say("diya 5000 प्रभारी क")
    principal.tap(T.BTN_RECORD)
    ack_card = incharge.card
    assert principal.tap(T.BTN_UNDO) == "undone"
    assert incharge.mine[-1]["text"] == T.REMOVED_NOTICE.format(giver=P_NAME, txn_no="32-A-000001", amount="₹5,000")
    assert incharge.tap(T.BTN_ACK_YES, card=ack_card) == "ack_already_void"
    assert incharge.toast == T.ENTRY_REMOVED
    assert _txns(conn)[0]["status"] == "void"


def test_no_undo_after_acknowledgement(conn, phones):
    principal, incharge = phones
    principal.say("diya 5000 प्रभारी क")
    principal.tap(T.BTN_RECORD)
    posted = principal.card
    incharge.tap(T.BTN_ACK_YES)
    assert principal.tap(T.BTN_UNDO, card=posted) == "undo_not_allowed"
    assert principal.toast == T.UNDO_NOT_ALLOWED
    assert _txns(conn)[0]["status"] == "posted"


def test_new_expense_closes_open_advance_draft(conn, phones):
    principal, _ = phones
    principal.say("diya 5000 प्रभारी")
    draft = principal.card
    assert principal.say("1500 chai") == "pick_area"                    # the principal has three block scopes
    assert T.DRAFT_ABANDONED in [m["text"] for m in principal.mine]
    assert draft["message_id"] in principal.fake.stripped
    assert principal.tap(A_NAME, card=draft) == "card_stale"
    assert _txns(conn) == []


def test_amount_typed_in_amount_state_is_the_amount(conn, phones):
    principal, _ = phones
    principal.say("diya प्रभारी क")
    assert principal.say("1500 chai ke liye") == "confirm"               # not an expense: it answers the question
    assert principal.card["text"].startswith(f"₹1,500 → {A_NAME}")
    assert T.LINE_PURPOSE.format(purpose="chai ke liye") in principal.card["text"]
    assert Decimal("1500") == Decimal(principal.card["text"].split()[0].lstrip("₹").replace(",", ""))


def test_pin_lock_after_the_draft_opened_stops_the_post(conn, phones, team):
    principal, _ = phones
    menu = _menu(principal)
    principal.tap_data(CB_MENU_GIVE, menu["message_id"])
    principal.tap(A_NAME)
    assert principal.say("5000") == "confirm"
    conn.execute("UPDATE field.member SET pin_locked_at = now() WHERE member_id = %s", (team["principal"],))
    assert principal.tap(T.BTN_RECORD) == "pin_locked"
    assert principal.text == T.PIN_LOCKED_MONEY
    assert _txns(conn) == []
