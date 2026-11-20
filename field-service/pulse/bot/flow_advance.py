"""Giving money to a team member by bot (LLD §5.2): "give money" menu button or the typed shortcut
"diya 50000 <name>".

Flow 'advance' in tg_session; cards carry `g:<sid>:<v>:<op>`. Person → amount → (big-amount check)
→ confirm, with an optional purpose. Posting creates a pending advance and sends the receiver the
acknowledgement card; whether that message went out is told to the giver plainly.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any
from uuid import UUID

from pulse.bot import cards, flow_ack, flow_pin
from pulse.bot import session as sessions
from pulse.bot import texts_hi as T
from pulse.bot.format import day_label, inr
from pulse.bot.session import Session
from pulse.bot.ui import Ctx, btn, can_give, edit_or_send, keyboard, send, two_columns
from pulse.common import pii
from pulse.common.clock import today_ist
from pulse.common.text import normalise
from pulse.ledger import service as ledger
from pulse.ledger.errors import LedgerError
from pulse.ledger.parse_amount import AmountError, ParsedAmount, parse_amount
from pulse.metrics.ledger import member_balance_sql
from pulse.team import names

log = logging.getLogger(__name__)

FLOW = "advance"
PREFIX = "g"
S_PICK = "pick_person"
S_AMOUNT = "await_amount"
S_BIG = "confirm_amount"
S_CONFIRM = "confirm"
S_PURPOSE = "await_purpose"
S_POSTED = cards.POSTED
TEXT_STATES = (S_AMOUNT, S_PURPOSE)
PAGE = 8
PURPOSE_MAX = 200
_VERBS = {normalise(v) for v in T.ADVANCE_VERBS}

Outcome = tuple[str, str | None]


def _cb(d: dict[str, Any], op: str) -> str:
    return cards.cb(PREFIX, d, op)


def _people(ctx: Ctx, member: dict[str, Any], d: dict[str, Any]) -> list[dict[str, Any]]:
    everyone = ledger.eligible_receivers(ctx.conn, member["member_id"])
    if d.get("match_ids"):
        wanted = d["match_ids"]
        return [p for p in everyone if str(p["member_id"]) in wanted]
    return everyone


def _render(ctx: Ctx, member: dict[str, Any], d: dict[str, Any], state: str) -> tuple[str, dict | None]:
    cancel = [btn(T.BTN_CANCEL, _cb(d, "x"))]
    if state == S_PICK:
        people = _people(ctx, member, d)
        start = d["page"] * PAGE
        page = people[start:start + PAGE]
        d["page_ids"] = [str(p["member_id"]) for p in page]
        grid = two_columns([btn(p["display_name"], _cb(d, f"p:{i}")) for i, p in enumerate(page)])
        extra = []
        if start + PAGE < len(people):
            extra.append(btn(T.BTN_NEXT_PAGE, _cb(d, "nx")))
        if d.get("match_ids"):
            extra.append(btn(T.BTN_ALL_PEOPLE, _cb(d, "all")))
        return T.ASK_PERSON, keyboard(grid + [extra, cancel])
    if state == S_AMOUNT:
        return T.ASK_ADVANCE_AMOUNT.format(name=d["to_name"]), keyboard([cancel])
    amount = inr(Decimal(d["amount"]))
    if state == S_BIG:
        return T.CONFIRM_BIG_AMOUNT.format(amount=amount), keyboard([[btn(T.BTN_YES_CORRECT, _cb(d, "ya"))], cancel])
    line = T.ADVANCE_LINE.format(amount=amount, name=d["to_name"], day=day_label(today_ist(ctx.now), today_ist(ctx.now)),
                                 mode=T.PAY_MODES["cash"])
    lines = [line] + ([T.LINE_PURPOSE.format(purpose=d["remark"])] if d.get("remark") else [])
    if state == S_CONFIRM:
        rows = [[btn(T.BTN_RECORD, _cb(d, "ok"))], [btn(T.BTN_PURPOSE, _cb(d, "pp"))], cancel]
        return "\n".join(lines), keyboard(rows)
    if state == S_PURPOSE:
        return "\n".join(lines) + "\n\n" + T.ASK_PURPOSE, keyboard([cancel])
    if state == S_POSTED:
        out = [T.POSTED.format(txn_no=d["txn_no"])] + lines + [flow_ack.delivery_line(d["delivery"], d["to_name"]),
                                                               T.WAITING_ACK]
        if not member["is_principal"]:
            bal = ctx.conn.execute(member_balance_sql(), {"member_id": member["member_id"]}).fetchone()["balance"]
            out.append(T.BALANCE_HELD.format(amount=inr(bal)) if bal >= 0 else T.BALANCE_OWED.format(amount=inr(-bal)))
        return "\n".join(out), keyboard([[btn(T.BTN_UNDO, "u:" + UUID(d["txn_id"]).hex)]])
    raise ValueError(state)


def _show(ctx, member, chat_id, d, state, **kw) -> str:
    cards.show(ctx, chat_id, FLOW, d, state, lambda dd: _render(ctx, member, dd, state), **kw)
    return state


def _new(old: dict[str, Any] | None = None) -> dict[str, Any]:
    return cards.new_draft(old, to=None, to_name=None, amount=None, needs_confirm=False, confirmed=False,
                           remark=None, page=0, match_ids=None, page_ids=[], delivery=None, txn_id=None, txn_no=None)


def _after_person(ctx, member, chat_id, d, **kw) -> str:
    if d["amount"] is None:
        return _show(ctx, member, chat_id, d, S_AMOUNT, **kw)
    if d["needs_confirm"] and not d["confirmed"]:
        return _show(ctx, member, chat_id, d, S_BIG, **kw)
    return _show(ctx, member, chat_id, d, S_CONFIRM, **kw)


def _set_amount(d: dict[str, Any], parsed: ParsedAmount) -> None:
    d["amount"] = str(parsed.amount)
    d["needs_confirm"], d["confirmed"] = parsed.needs_confirm, False
    if parsed.description and not d.get("remark"):
        d["remark"] = parsed.description


# ---- entry points -----------------------------------------------------------------------------

def start_from_menu(ctx: Ctx, member: dict[str, Any], chat_id: int, message_id: int) -> Outcome:
    if flow_pin.locked_reply(ctx, member, chat_id):
        return "pin_locked", None
    if not can_give(member):
        edit_or_send(ctx, chat_id, message_id, T.NO_ADVANCE_PERMISSION, None)
        return "no_permission", None
    if not ledger.eligible_receivers(ctx.conn, member["member_id"]):
        edit_or_send(ctx, chat_id, message_id, T.NO_RECEIVERS, None)
        return "no_receivers", None
    return _show(ctx, member, chat_id, _new(), S_PICK, cb_message_id=message_id), None


def is_shortcut(text: str) -> bool:
    words = text.strip().split(maxsplit=1)
    return bool(words) and normalise(words[0]) in _VERBS


def try_shortcut(ctx: Ctx, member: dict[str, Any], chat_id: int, text: str, sess: Session | None) -> str | None:
    """"diya 50000 ramesh". Returns None when the text is not the shortcut. Never guesses a person:
    exactly one match goes to the confirm card (still one tap to post); zero or several show a list."""
    if not is_shortcut(text):
        return None
    if not can_give(member):
        send(ctx, chat_id, T.NO_ADVANCE_PERMISSION)
        return "no_permission"
    if flow_pin.locked_reply(ctx, member, chat_id):
        return "pin_locked"
    rest = text.strip().split(maxsplit=1)[1] if len(text.strip().split(maxsplit=1)) > 1 else ""
    try:
        parsed = parse_amount(rest, confirm_above=ctx.limits.confirm_amount_above)
    except AmountError as e:
        send(ctx, chat_id, T.AMOUNT_ERRORS[e.reason])
        return "amount_error"
    if parsed is not None and pii.screen(parsed.description):
        send(ctx, chat_id, T.PII_REJECTED.format(what=T.PII_JOIN.join(T.PII_KINDS[k] for k in pii.screen(parsed.description))))
        return "pii_rejected"
    everyone = ledger.eligible_receivers(ctx.conn, member["member_id"])
    if not everyone:
        send(ctx, chat_id, T.NO_RECEIVERS)
        return "no_receivers"
    if cards.is_open(sess, ctx):
        cards.abandon(ctx, chat_id, sess)

    d = _new()
    query = (parsed.description if parsed else rest).strip()
    if parsed:
        d["amount"], d["needs_confirm"] = str(parsed.amount), parsed.needs_confirm
    found = names.matches(query, everyone, ctx.core) if query else []
    if len(found) == 1:
        d["to"], d["to_name"] = str(found[0]["member_id"]), found[0]["display_name"]
        _after_person(ctx, member, chat_id, d)
        return "name_matched"
    prefix = None
    if query and not found:
        prefix = T.NAME_NOT_FOUND.format(name=query)
    elif len(found) > 1:
        d["match_ids"] = [str(p["member_id"]) for p in found]
        prefix = T.NAME_SEVERAL.format(name=query)
    _show(ctx, member, chat_id, d, S_PICK, prefix=prefix)
    return "name_several" if len(found) > 1 else ("name_not_found" if query else S_PICK)


def on_text(ctx: Ctx, member: dict[str, Any], chat_id: int, text: str, sess: Session) -> str | None:
    """Typed text while an advance draft is open. None means "not an answer here": the dispatcher
    then treats it as a new command and closes this draft."""
    d, state = sess.payload, sess.state
    if text.strip().lower() in T.CANCEL_WORDS:
        return cards.cancel(ctx, chat_id, d, None)
    if state == S_AMOUNT:
        try:
            parsed = parse_amount(text, confirm_above=ctx.limits.confirm_amount_above)
        except AmountError as e:
            return _show(ctx, member, chat_id, d, state, prefix=T.AMOUNT_ERRORS[e.reason])
        if parsed is None:
            return _show(ctx, member, chat_id, d, state, prefix=T.AMOUNT_NOT_UNDERSTOOD)
        if pii.screen(parsed.description):
            return _show(ctx, member, chat_id, d, state,
                         prefix=T.PII_REJECTED.format(what=T.PII_JOIN.join(T.PII_KINDS[k] for k in pii.screen(parsed.description))))
        _set_amount(d, parsed)
        return _after_person(ctx, member, chat_id, d)
    if state == S_PURPOSE:
        purpose = text.strip()
        kinds = pii.screen(purpose)
        if kinds:
            return _show(ctx, member, chat_id, d, state, prefix=T.PII_REJECTED.format(
                what=T.PII_JOIN.join(T.PII_KINDS[k] for k in kinds)))
        if len(purpose) > PURPOSE_MAX:
            return _show(ctx, member, chat_id, d, state, prefix=T.REMARK_TOO_LONG)
        d["remark"] = purpose or None
        return _show(ctx, member, chat_id, d, S_CONFIRM)
    if is_shortcut(text) or _is_amount(text, ctx):
        return None
    return _show(ctx, member, chat_id, d, state, prefix=T.USE_BUTTONS)


def _is_amount(text: str, ctx: Ctx) -> bool:
    try:
        return parse_amount(text, confirm_above=ctx.limits.confirm_amount_above) is not None
    except AmountError:
        return True


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
    if state == S_PICK:
        if name == "p" and arg.isdigit() and int(arg) < len(d["page_ids"]):
            chosen = d["page_ids"][int(arg)]
            person = next((p for p in ledger.eligible_receivers(ctx.conn, member["member_id"])
                           if str(p["member_id"]) == chosen), None)
            if person is None:
                return "card_stale", T.CARD_STALE
            d["to"], d["to_name"] = chosen, person["display_name"]
            return _after_person(ctx, member, chat_id, d, **kw), None
        if op == "nx":
            d["page"] += 1
            return _show(ctx, member, chat_id, d, S_PICK, **kw), None
        if op == "all":
            d["match_ids"], d["page"] = None, 0
            return _show(ctx, member, chat_id, d, S_PICK, **kw), None
    if state == S_BIG and op == "ya":
        d["confirmed"] = True
        return _show(ctx, member, chat_id, d, S_CONFIRM, **kw), None
    if state == S_CONFIRM:
        if op == "pp":
            return _show(ctx, member, chat_id, d, S_PURPOSE, **kw), None
        if op == "ok":
            return _post(ctx, member, chat_id, message_id, d)
    return "card_stale", T.CARD_STALE


def post_after_pin(ctx: Ctx, member: dict[str, Any], chat_id: int, d: dict[str, Any]) -> Outcome:
    return _post(ctx, member, chat_id, d.get("card"), d)


def _post(ctx: Ctx, member: dict[str, Any], chat_id: int, message_id: int, d: dict[str, Any]) -> Outcome:
    if flow_pin.locked_reply(ctx, member, chat_id):           # locked since the draft opened: no money action
        return "pin_locked", None
    if not d.pop("pin_ok", False):
        waiting = flow_pin.require(ctx, member, chat_id, Decimal(d["amount"]))
        if waiting is not None:                           # the draft waits; only a flag is kept
            d["pin_wait"] = True
            sessions.save(ctx.conn, chat_id, FLOW, S_CONFIRM, d, ttl=cards.ttl(ctx), now=ctx.now)
            return waiting, None
    d.pop("pin_wait", None)
    try:
        row = ledger.create_advance(ctx.conn, ctx.core, member["member_id"], to_member_id=UUID(d["to"]),
                                    amount=Decimal(d["amount"]), remark=d["remark"], created_via="telegram",
                                    limits=ctx.limits, now=ctx.now)
    except LedgerError:
        log.warning("advance refused by ledger", exc_info=True)
        return "not_saved", T.NOT_SAVED
    d["delivery"] = flow_ack.request_ack(ctx, row)
    d["txn_id"], d["txn_no"] = str(row["txn_id"]), row["txn_no"]
    _show(ctx, member, chat_id, d, S_POSTED, cb_message_id=message_id)
    return "posted", None
