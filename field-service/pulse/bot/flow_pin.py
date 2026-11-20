"""Setting, changing and asking for the PIN in the bot (LLD §11).

Every message typed while a PIN step is open is deleted from the chat as soon as it is read
(deleteMessage: bots may delete incoming messages in private chats within 48 hours). The PIN is
never echoed, never logged and never kept in tg_session: the open step lives on tg_link
(pulse/team/pin.py) and a money draft waiting for it carries only a flag.

Steps: verify_old → new → confirm (set or change) · action (an entry at or above the PIN limit).
"""
from __future__ import annotations

import logging
from typing import Any

from pulse.bot import session as sessions
from pulse.bot import texts_hi as T
from pulse.bot.format import inr
from pulse.bot.outgoing import Confirm
from pulse.bot.ui import Ctx, btn, keyboard, send, delete
from pulse.team import pin as pins

log = logging.getLogger(__name__)

CB_SET = "m:pin"


def pin_button(conn, member: dict[str, Any]) -> dict:
    return btn(T.BTN_PIN_CHANGE if pins.has_pin(conn, member["member_id"]) else T.BTN_PIN_SET, CB_SET)


def _delete(ctx: Ctx, chat_id: int, message_id: int | None) -> None:
    """Deleted at once (after the commit); a failed delete is retried by a tg_delete job, and the member
    is told to delete it themselves only if every attempt fails."""
    delete(ctx, chat_id, message_id, retry=True)


def start(ctx: Ctx, member: dict[str, Any], chat_id: int) -> tuple[str, str | None]:
    """Menu: set a PIN, or change it (current PIN first)."""
    if pins.is_locked(ctx.conn, member["member_id"]):
        send(ctx, chat_id, T.PIN_LOCKED_MONEY)
        return "pin_locked", None
    if pins.has_pin(ctx.conn, member["member_id"]):
        pins.start(ctx.conn, member["member_id"], "verify_old", ctx.now)
        send(ctx, chat_id, T.ASK_PIN_CURRENT)
        return "pin_ask_current", None
    pins.start(ctx.conn, member["member_id"], "new", ctx.now)
    send(ctx, chat_id, T.ASK_PIN_NEW)
    return "pin_ask_new", None


def locked_reply(ctx: Ctx, member: dict[str, Any], chat_id: int) -> bool:
    """Money flows call this first: a locked member gets one reply and nothing starts."""
    if pins.is_locked(ctx.conn, member["member_id"]):
        send(ctx, chat_id, T.PIN_LOCKED_MONEY)
        return True
    return False


def require(ctx: Ctx, member: dict[str, Any], chat_id: int, amount) -> str | None:
    """Before posting an entry: None when no PIN is needed (below the limit) or it was just given;
    otherwise ask for it (or to set one) and return why the entry is waiting."""
    if amount < ctx.limits.pin_limit:
        return None
    if pins.is_locked(ctx.conn, member["member_id"]):
        send(ctx, chat_id, T.PIN_LOCKED_MONEY)
        return "pin_locked"
    if not pins.has_pin(ctx.conn, member["member_id"]):
        send(ctx, chat_id, T.PIN_NEEDED_FIRST.format(amount=inr(ctx.limits.pin_limit)),
             keyboard([[btn(T.BTN_PIN_SET, CB_SET)]]))
        return "pin_needed_first"
    pins.start(ctx.conn, member["member_id"], "action", ctx.now)
    send(ctx, chat_id, T.ASK_PIN_ACTION.format(amount=inr(amount)))
    return "pin_asked"


def on_text(ctx: Ctx, member: dict[str, Any], chat_id: int, message_id: int | None, text: str) -> str | None:
    """Text from a member with a PIN step open. None: no step, so the text goes to the other flows."""
    step, state = pins.stage(ctx.conn, member["member_id"], ctx.now)
    if step is None:
        if pins.is_own_pin(ctx.conn, member["member_id"], text):   # sent twice, or after the late window
            _delete(ctx, chat_id, message_id)
            send(ctx, chat_id, T.PIN_SENT_UNASKED)
            return "pin_unasked"
        return None
    if state == "late":
        pins.clear(ctx.conn, member["member_id"])
        if pins.looks_like_pin(text):                    # a late PIN must never become an amount
            _delete(ctx, chat_id, message_id)
            send(ctx, chat_id, T.PIN_EXPIRED_ENTRY if step == "action" else T.PIN_EXPIRED_SETUP)
            return "pin_expired"
        return None
    _delete(ctx, chat_id, message_id)                    # whatever it is, it was typed where a PIN goes
    entry = pins.normalise(text)
    me = member["member_id"]

    if step in ("verify_old", "action"):
        result = pins.verify(ctx.conn, member, entry, ctx.now)
        if result == "locked":
            send(ctx, chat_id, T.PIN_LOCKED)
            return "pin_locked"
        if result == "wrong":
            pins.start(ctx.conn, me, step, ctx.now)      # same step, fresh 2 minutes
            send(ctx, chat_id, T.PIN_WRONG.format(left=pins.attempts_left(ctx.conn, me)))
            return "pin_wrong"
        if step == "verify_old":
            pins.start(ctx.conn, me, "new", ctx.now)
            send(ctx, chat_id, T.ASK_PIN_NEW)
            return "pin_ask_new"
        pins.clear(ctx.conn, me)
        return _resume(ctx, member, chat_id)

    if step == "new":
        if not pins.acceptable(entry):
            pins.start(ctx.conn, me, "new", ctx.now)
            send(ctx, chat_id, T.PIN_WEAK)
            return "pin_weak"
        pins.hold_first_entry(ctx.conn, me, entry, ctx.now)
        send(ctx, chat_id, T.ASK_PIN_CONFIRM)
        return "pin_ask_confirm"

    if step == "confirm":
        if not pins.confirm_matches(ctx.conn, me, entry):
            pins.clear(ctx.conn, me)
            send(ctx, chat_id, T.PIN_MISMATCH)
            return "pin_mismatch"
        pins.save(ctx.conn, member, entry, ctx.now)
        waiting = _waiting_draft(ctx, chat_id)
        send(ctx, chat_id, T.PIN_SAVED_RETRY if waiting else T.PIN_SAVED, confirms=Confirm("pin"))
        return "pin_saved"
    return None


def _waiting_draft(ctx: Ctx, chat_id: int):
    sess = sessions.load(ctx.conn, chat_id)
    if sess is not None and sess.payload.get("pin_wait") and not sess.expired(ctx.now):
        return sess
    return None


def _resume(ctx: Ctx, member: dict[str, Any], chat_id: int) -> str:
    """The PIN was right: post the draft that was waiting for it."""
    sess = _waiting_draft(ctx, chat_id)
    if sess is None:
        send(ctx, chat_id, T.PIN_OK_POSTING)
        return "pin_ok"
    from pulse.bot import flow_advance, flow_expense

    d = sess.payload
    d["pin_ok"] = True
    if sess.flow == flow_expense.FLOW:
        outcome, _ = flow_expense.post_after_pin(ctx, member, chat_id, d)
    elif sess.flow == flow_advance.FLOW:
        outcome, _ = flow_advance.post_after_pin(ctx, member, chat_id, d)
    else:
        return "pin_ok"
    return outcome
