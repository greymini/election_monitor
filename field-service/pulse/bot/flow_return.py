"""Handing unspent money back (LLD §5.2): "my account" → return money → (giver, if more than one)
→ amount → confirm. The giver gets the same acknowledgement card as for an advance.

Flow 'return' in tg_session; cards carry `r:<sid>:<v>:<op>`.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any
from uuid import UUID

from pulse.bot import cards, flow_ack
from pulse.bot import texts_hi as T
from pulse.bot.format import day_label, inr
from pulse.bot.session import Session
from pulse.bot.ui import Ctx, btn, edit_or_send, keyboard, two_columns
from pulse.common import pii
from pulse.common.clock import today_ist
from pulse.ledger import service as ledger
from pulse.ledger.errors import Invalid, LedgerError
from pulse.ledger.parse_amount import AmountError, parse_amount
from pulse.metrics.ledger import member_balance_sql

log = logging.getLogger(__name__)

FLOW = "return"
PREFIX = "r"
S_PICK = "pick_giver"
S_AMOUNT = "await_amount"
S_CONFIRM = "confirm"
S_POSTED = cards.POSTED

Outcome = tuple[str, str | None]


def _cb(d: dict[str, Any], op: str) -> str:
    return cards.cb(PREFIX, d, op)


def _balance(ctx: Ctx, member: dict[str, Any]) -> Decimal:
    return ctx.conn.execute(member_balance_sql(), {"member_id": member["member_id"]}).fetchone()["balance"]


def _render(ctx: Ctx, member: dict[str, Any], d: dict[str, Any], state: str) -> tuple[str, dict | None]:
    cancel = [btn(T.BTN_CANCEL, _cb(d, "x"))]
    if state == S_PICK:
        givers = d["givers"]
        grid = two_columns([btn(g[1], _cb(d, f"p:{i}")) for i, g in enumerate(givers)])
        return T.ASK_RETURN_TO, keyboard(grid + [cancel])
    if state == S_AMOUNT:
        return T.ASK_RETURN_AMOUNT.format(name=d["to_name"], balance=inr(_balance(ctx, member))), keyboard([cancel])
    today = today_ist(ctx.now)
    line = T.RETURN_LINE.format(amount=inr(Decimal(d["amount"])), name=d["to_name"], day=day_label(today, today),
                                mode=T.PAY_MODES["cash"])
    if state == S_CONFIRM:
        return line, keyboard([[btn(T.BTN_RECORD, _cb(d, "ok"))], cancel])
    if state == S_POSTED:
        bal = _balance(ctx, member)
        lines = [T.POSTED.format(txn_no=d["txn_no"]), line, flow_ack.delivery_line(d["delivery"], d["to_name"]),
                 T.WAITING_ACK,
                 T.BALANCE_HELD.format(amount=inr(bal)) if bal >= 0 else T.BALANCE_OWED.format(amount=inr(-bal))]
        return "\n".join(lines), keyboard([[btn(T.BTN_UNDO, "u:" + UUID(d["txn_id"]).hex)]])
    raise ValueError(state)


def _show(ctx, member, chat_id, d, state, **kw) -> str:
    cards.show(ctx, chat_id, FLOW, d, state, lambda dd: _render(ctx, member, dd, state), **kw)
    return state


def start(ctx: Ctx, member: dict[str, Any], chat_id: int, message_id: int, sess: Session | None) -> Outcome:
    if member["finance_scope"] == "none":
        edit_or_send(ctx, chat_id, message_id, T.NO_FINANCE_PERMISSION, None)
        return "no_permission", None
    from pulse.bot import flow_pin
    if flow_pin.locked_reply(ctx, member, chat_id):
        return "pin_locked", None
    givers = [] if member["is_principal"] else ledger.givers_of(ctx.conn, member["member_id"])
    if not givers:
        edit_or_send(ctx, chat_id, message_id, T.NO_GIVER, None)
        return "no_giver", None
    if _balance(ctx, member) <= 0:
        edit_or_send(ctx, chat_id, message_id, T.NOTHING_TO_RETURN, None)
        return "nothing_to_return", None
    if cards.is_open(sess, ctx):
        cards.abandon(ctx, chat_id, sess)
    d = cards.new_draft(None, givers=[[str(g["member_id"]), g["display_name"]] for g in givers],
                        to=None, to_name=None, amount=None, delivery=None, txn_id=None, txn_no=None)
    if len(givers) == 1:
        d["to"], d["to_name"] = d["givers"][0]
        return _show(ctx, member, chat_id, d, S_AMOUNT, cb_message_id=message_id), None
    return _show(ctx, member, chat_id, d, S_PICK, cb_message_id=message_id), None


def on_text(ctx: Ctx, member: dict[str, Any], chat_id: int, text: str, sess: Session) -> str | None:
    d, state = sess.payload, sess.state
    if text.strip().lower() in T.CANCEL_WORDS:
        return cards.cancel(ctx, chat_id, d, None)
    if state != S_AMOUNT:
        try:
            if parse_amount(text) is not None:
                return None
        except AmountError:
            return None
        return _show(ctx, member, chat_id, d, state, prefix=T.USE_BUTTONS)
    try:
        parsed = parse_amount(text, confirm_above=ctx.limits.confirm_amount_above)
    except AmountError as e:
        return _show(ctx, member, chat_id, d, state, prefix=T.AMOUNT_ERRORS[e.reason])
    if parsed is None:
        return _show(ctx, member, chat_id, d, state, prefix=T.AMOUNT_NOT_UNDERSTOOD)
    if pii.screen(parsed.description):
        return _show(ctx, member, chat_id, d, state, prefix=T.PII_REJECTED.format(
            what=T.PII_JOIN.join(T.PII_KINDS[k] for k in pii.screen(parsed.description))))
    balance = _balance(ctx, member)
    if parsed.amount > balance:
        return _show(ctx, member, chat_id, d, state, prefix=T.RETURN_TOO_MUCH.format(balance=inr(balance)))
    d["amount"] = str(parsed.amount)
    return _show(ctx, member, chat_id, d, S_CONFIRM)


def on_callback(ctx: Ctx, member: dict[str, Any], chat_id: int, message_id: int, data: str,
                sess: Session | None) -> Outcome:
    verdict, op, toast = cards.check(ctx, chat_id, message_id, FLOW, sess, data, posted_ops=("ok",))
    if verdict != "ok":
        return {"stale": "card_stale", "posted": "already_posted", "expired": "card_expired"}[verdict], toast
    d, state = sess.payload, sess.state
    kw = {"cb_message_id": message_id}
    name, _, arg = op.partition(":")
    if op == "x":
        return cards.cancel(ctx, chat_id, d, message_id), None
    if state == S_PICK and name == "p" and arg.isdigit() and int(arg) < len(d["givers"]):
        d["to"], d["to_name"] = d["givers"][int(arg)]
        return _show(ctx, member, chat_id, d, S_AMOUNT, **kw), None
    if state == S_CONFIRM and op == "ok":
        from pulse.bot import flow_pin
        if flow_pin.locked_reply(ctx, member, chat_id):     # locked since the draft opened: no money action
            return "pin_locked", None
        try:
            row = ledger.create_return(ctx.conn, ctx.core, member["member_id"], amount=Decimal(d["amount"]),
                                       to_member_id=UUID(d["to"]), created_via="telegram",
                                       limits=ctx.limits, now=ctx.now)
        except Invalid:          # e.g. the balance changed meanwhile
            _show(ctx, member, chat_id, d, S_AMOUNT, prefix=T.RETURN_TOO_MUCH.format(
                balance=inr(_balance(ctx, member))), **kw)
            return "return_too_much", None
        except LedgerError:
            log.warning("return refused by ledger", exc_info=True)
            return "not_saved", T.NOT_SAVED
        d["delivery"] = flow_ack.request_ack(ctx, row)
        d["txn_id"], d["txn_no"] = str(row["txn_id"]), row["txn_no"]
        _show(ctx, member, chat_id, d, S_POSTED, **kw)
        return "posted", None
    return "card_stale", T.CARD_STALE
