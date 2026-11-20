"""Draft cards shared by the expense, advance and return flows.

A draft lives in tg_session.payload with a random `sid` and a card version `v`. Its buttons carry
`<prefix>:<sid>:<v>:<op>`, so a tap on an old, replaced or already-used card is recognised. Button
taps edit the card in place; after a typed reply the card is sent again below and the old one loses
its buttons, so there is always exactly one live card.
"""
from __future__ import annotations

import secrets
from datetime import timedelta
from typing import Any, Callable

from pulse.bot import session as sessions
from pulse.bot import texts_hi as T
from pulse.bot.session import Session
from pulse.bot.outgoing import Confirm
from pulse.bot.ui import Ctx, edit_or_send, record, send, strip_buttons

POSTED = "posted"
FINISHED_STATES = (POSTED, "filed")     # a saved entry or a filed report: nothing left to lose

Render = Callable[[dict[str, Any]], tuple[str, dict | None]]


def ttl(ctx: Ctx) -> timedelta:
    return timedelta(minutes=ctx.limits.expense_session_minutes)


def new_draft(old: dict[str, Any] | None = None, **fields: Any) -> dict[str, Any]:
    """A fresh draft. Keeps the old draft's card id so that card loses its buttons when the new one is sent."""
    return {"sid": secrets.token_hex(3), "v": 0, "card": (old or {}).get("card"), **fields}


def cb(prefix: str, d: dict[str, Any], op: str) -> str:
    return f"{prefix}:{d['sid']}:{d['v']}:{op}"


def show(ctx: Ctx, chat_id: int, flow: str, d: dict[str, Any], state: str, render: Render, *,
         cb_message_id: int | None = None, prefix: str | None = None) -> None:
    """Render the card for `state` (after bumping its version), edit or re-send it, and save the draft."""
    d["v"] += 1
    text, markup = render(d)
    if prefix:
        text = prefix + "\n\n" + text
    from uuid import UUID

    confirms = (Confirm("txn", txn_id=UUID(str(d["txn_id"]))) if state in FINISHED_STATES and d.get("txn_id")
                else Confirm("session", flow=flow, sid=d["sid"]))
    if cb_message_id is not None:
        d["card"] = edit_or_send(ctx, chat_id, cb_message_id, text, markup, confirms=confirms)
    else:
        strip_buttons(ctx, chat_id, d.get("card"))
        d["card"] = send(ctx, chat_id, text, markup, confirms=confirms)
    record(ctx, chat_id, d["card"], "money", txn_id=d.get("txn_id"))     # expense, advance and return cards
    sessions.save(ctx.conn, chat_id, flow, state, d, ttl=ttl(ctx), now=ctx.now)


def parse(data: str) -> tuple[str, str, str] | None:
    """`<prefix>:<sid>:<v>:<op>` -> (sid, v, op)."""
    parts = data.split(":", 3)
    return (parts[1], parts[2], parts[3]) if len(parts) == 4 else None


def check(ctx: Ctx, chat_id: int, message_id: int, flow: str, sess: Session | None, data: str,
          *, posted_ops: tuple[str, ...] = ()) -> tuple[str, str | None, str | None]:
    """Validate a draft-card tap. Returns (verdict, op, toast):
    'ok'; 'stale' (another draft, older card); 'posted' (already saved; toast says so for posted_ops);
    'expired' (draft cleared and card closed)."""
    parsed = parse(data)
    if parsed is None:
        return "stale", None, T.CARD_STALE
    sid, v, op = parsed
    if sess is None or sess.flow != flow or sess.payload.get("sid") != sid:
        return "stale", op, T.CARD_STALE
    if sess.state == POSTED:
        if op in posted_ops:
            return "posted", op, T.ALREADY_POSTED.format(txn_no=sess.payload.get("txn_no"))
        return "stale", op, T.CARD_STALE
    if sess.expired(ctx.now):
        sessions.clear(ctx.conn, chat_id)
        edit_or_send(ctx, chat_id, message_id, T.CARD_EXPIRED, None)
        return "expired", op, T.CARD_EXPIRED
    if v != str(sess.payload["v"]):
        return "stale", op, T.CARD_STALE
    return "ok", op, None


def cancel(ctx: Ctx, chat_id: int, d: dict[str, Any], message_id: int | None) -> str:
    from pulse.bot import flow_report

    sessions.clear(ctx.conn, chat_id)
    if message_id is not None:
        edit_or_send(ctx, chat_id, message_id, T.CANCELLED, None)
    else:
        strip_buttons(ctx, chat_id, d.get("card"))
        send(ctx, chat_id, T.CANCELLED)
    flow_report.adopt_held_payload(ctx, chat_id, d)
    return "cancelled"


def is_open(sess: Session | None, ctx: Ctx) -> bool:
    """An unfinished draft of any flow. A report draft never expires here: it files itself."""
    if sess is None or sess.state in FINISHED_STATES:
        return False
    return sess.flow == "report" or not sess.expired(ctx.now)


def abandon(ctx: Ctx, chat_id: int, sess: Session, notice: str = T.DRAFT_ABANDONED) -> None:
    """Another flow is starting over an open draft. A report draft is filed rather than dropped;
    any other draft's card is closed with a notice, and media it was holding opens a report."""
    from pulse.bot import flow_report      # late import: flow_report uses this module

    if sess.flow == "report":
        flow_report.close_draft(ctx, chat_id, sess)
        return
    sessions.clear(ctx.conn, chat_id)
    strip_buttons(ctx, chat_id, sess.payload.get("card"))
    send(ctx, chat_id, notice)
    flow_report.adopt_held(ctx, chat_id, sess)


def expire(ctx: Ctx, chat_id: int, sess: Session, notice: str = T.DRAFT_EXPIRED_GENERIC) -> None:
    from pulse.bot import flow_report

    sessions.clear(ctx.conn, chat_id)
    strip_buttons(ctx, chat_id, sess.payload.get("card"))
    send(ctx, chat_id, notice)
    flow_report.adopt_held(ctx, chat_id, sess)
