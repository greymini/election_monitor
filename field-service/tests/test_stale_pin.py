"""PIN steps (flow_pin) around stale and late input: a PIN for a draft that is gone, a PIN sent again
after it was used, a PIN typed after the late window, a redelivered PIN, taps while locked, and a
member removed mid-step. Every PIN message is deleted and never read as an amount or echoed."""
from datetime import timedelta

import pytest

from pulse.bot import texts_hi as T
from pulse.bot.flow_pin import CB_SET
from pulse.common.clock import utcnow
from pulse.team import pin as pins
from pulse.team import service as team_service
from tests import updates as U
from tests.test_flow_expense import Chat
from tests.test_pin import PIN, WRONG, _post, _set_pin, _txns, fast_bcrypt, pinbot, worker_chat  # noqa: F401


def _last_text_id(chat):
    return chat.fake_last_text_id


@pytest.fixture(autouse=True)
def _track(monkeypatch):
    """Remember the message_id of each text this test sends, to check PIN messages were deleted."""
    orig = U.text

    def text(update_id, body, user=U.USER_A):
        u = orig(update_id, body, user)
        Chat.fake_last_text_id = u["message"]["message_id"]
        return u
    monkeypatch.setattr(U, "text", text)


def _deleted(chat):
    return (U.USER_A["id"], chat.fake_last_text_id) in chat.fake.deleted


def _echoed(fake, secret=PIN):
    return any(secret in (m.get("text") or "") for m in fake.sent + fake.edits)


def _ask_pin(chat, now=None):
    _set_pin(chat)
    chat.say("60000 petrol bolero", now=now)
    assert _post(chat, now=now) == "pin_asked"


def test_pin_for_a_draft_cancelled_meanwhile(conn, worker_chat):
    _ask_pin(worker_chat)
    worker_chat.tap(T.BTN_CANCEL)
    assert worker_chat.say(PIN) == "pin_ok"
    assert _deleted(worker_chat)
    assert worker_chat.text == T.PIN_OK_POSTING                        # answered; nothing to post
    assert _txns(conn) == 0


def test_pin_sent_again_after_it_was_used(conn, worker_chat):
    """A double send: the second copy must be deleted, not read as a ₹5,83,914 expense."""
    _ask_pin(worker_chat)
    assert worker_chat.say(PIN) == "posted"
    assert worker_chat.say(PIN) == "pin_unasked"
    assert _deleted(worker_chat)
    assert worker_chat.text == T.PIN_SENT_UNASKED
    assert _txns(conn) == 1 and not _echoed(worker_chat.fake)


def test_pin_typed_after_the_late_window(conn, worker_chat):
    t0 = utcnow()
    _ask_pin(worker_chat, now=t0)
    assert worker_chat.say(PIN, now=t0 + timedelta(minutes=11)) == "pin_unasked"   # prompt 2 min + late 8 min
    assert _deleted(worker_chat) and worker_chat.text == T.PIN_SENT_UNASKED
    assert _txns(conn) == 0 and not _echoed(worker_chat.fake)


def test_a_wrong_pin_like_number_is_still_an_amount(conn, worker_chat):
    """Only the member's own PIN is swallowed outside a PIN step; other bare numbers stay amounts."""
    _set_pin(worker_chat)
    assert worker_chat.say(WRONG) == "pick_bucket"                      # an expense draft, as before
    assert pins.attempts_left(conn, worker_chat_member(conn)) == pins.MAX_FAILURES   # not counted as a PIN try


def worker_chat_member(conn):
    return conn.execute("SELECT member_id FROM field.member WHERE user_id = 'usr-worker-a1'").fetchone()["member_id"]


def test_redelivered_wrong_pin_counts_once(conn, worker_chat):
    from tests.test_flow_expense import _ids

    _ask_pin(worker_chat)
    uid = next(_ids)
    from pulse.bot.dispatch import dispatch
    first = dispatch(worker_chat.pool, worker_chat.bot, U.text(uid, WRONG, worker_chat.user))
    again = dispatch(worker_chat.pool, worker_chat.bot, U.text(uid, WRONG, worker_chat.user))
    assert (first, again) == ("pin_wrong", "duplicate")
    assert pins.attempts_left(conn, worker_chat_member(conn)) == pins.MAX_FAILURES - 1
    assert worker_chat.say(PIN) == "posted" and _txns(conn) == 1


def test_text_typed_while_the_pin_is_asked_is_a_pin_attempt(conn, worker_chat):
    """Whatever is typed where a PIN goes is deleted (it may be a mistyped PIN) and counts as wrong;
    the draft keeps waiting."""
    _ask_pin(worker_chat)
    assert worker_chat.say("500 chai") == "pin_wrong"
    assert _deleted(worker_chat)
    assert worker_chat.text == T.PIN_WRONG.format(left=2)
    assert worker_chat.say(PIN) == "posted" and _txns(conn) == 1


def test_locked_member_taps_the_waiting_card_or_the_pin_button(conn, worker_chat):
    _ask_pin(worker_chat)
    for _ in range(3):
        worker_chat.say(WRONG)
    assert pins.is_locked(conn, worker_chat_member(conn))
    card = next(m for m in reversed(worker_chat.mine) if m["reply_markup"] and T.BTN_POST_ANYWAY in worker_chat.buttons(m))
    assert worker_chat.tap(T.BTN_POST_ANYWAY, card=card) == "pin_locked"
    assert worker_chat.text == T.PIN_LOCKED_MONEY
    assert worker_chat.tap_data(CB_SET, 1) == "pin_locked"
    assert worker_chat.text == T.PIN_LOCKED_MONEY
    assert _txns(conn) == 0


def test_set_pin_confirm_after_the_prompt_expired(conn, worker_chat):
    t0 = utcnow()
    assert worker_chat.tap_data(CB_SET, 1, now=t0) == "pin_ask_new"
    assert worker_chat.say(PIN, now=t0) == "pin_ask_confirm"
    assert worker_chat.say(PIN, now=t0 + timedelta(minutes=3)) == "pin_expired"
    assert _deleted(worker_chat) and worker_chat.text == T.PIN_EXPIRED_SETUP
    assert not pins.has_pin(conn, worker_chat_member(conn))


def test_member_deactivated_mid_step_still_has_the_pin_deleted(conn, core, worker_chat):
    _ask_pin(worker_chat)
    team_service.update_member(conn, core, "ac-32", worker_chat_member(conn), {"active": False})
    assert worker_chat.say(PIN) == "unlinked"
    assert worker_chat.text == T.UNREGISTERED
    assert _deleted(worker_chat)
