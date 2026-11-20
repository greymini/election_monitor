"""Impact follow-up in the bot (LLD §7): "was the work done?" (T.FOLLOWUP_ASK) with three buttons
(yes, partial, no), then an optional photo.

    f:<txn hex>:d | p | n      answer done | partial | not done (the first answer stands)
    f:<txn hex>:sk             no photo

After an answer, the next photo within 10 minutes is attached to the follow-up instead of starting a
report (session flow 'followup'). The question card itself is drawn by pulse/notify/render.py.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import UUID

from pulse.bot import session as sessions
from pulse.bot import texts_hi as T
from pulse.bot.session import Session
from pulse.bot.outgoing import Confirm
from pulse.bot.ui import Ctx, btn, edit_or_send, keyboard, send
from pulse.ledger import followup
from pulse.reports import media

FLOW = "followup"
PREFIX = "f"
PHOTO_WINDOW = timedelta(minutes=10)
_OPS = {"d": "done", "p": "partial", "n": "not_done"}


def buttons(txn_id: UUID) -> dict:
    key = UUID(str(txn_id)).hex
    return keyboard([[btn(T.BTN_FOLLOWUP_DONE, f"{PREFIX}:{key}:d"), btn(T.BTN_FOLLOWUP_PARTIAL, f"{PREFIX}:{key}:p"),
                      btn(T.BTN_FOLLOWUP_NOT_DONE, f"{PREFIX}:{key}:n")]])


def on_callback(ctx: Ctx, member: dict[str, Any], chat_id: int, message_id: int, data: str,
                sess: Session | None) -> tuple[str, str | None]:
    parts = data.split(":")
    if len(parts) != 3:
        return "card_stale", T.CARD_STALE
    try:
        txn_id = UUID(hex=parts[1])
    except ValueError:
        return "card_stale", T.CARD_STALE
    op = parts[2]
    if op == "sk":
        # only this answer's photo step: an older card's "no photo" must not end a newer answer's
        if sess is not None and sess.flow == FLOW and sess.payload.get("txn_id") == str(txn_id):
            sessions.clear(ctx.conn, chat_id)
        edit_or_send(ctx, chat_id, message_id, T.FOLLOWUP_NO_PHOTO, None)
        return "followup_no_photo", None
    if op not in _OPS:
        return "card_stale", T.CARD_STALE
    result = followup.answer(ctx.conn, txn_id, member["member_id"], _OPS[op], now=ctx.now)
    if result == "not_yours":
        return "not_allowed", T.NOT_ALLOWED
    if result == "closed":                               # voided or moved out of ASSIST / WORKS after asking
        edit_or_send(ctx, chat_id, message_id, T.FOLLOWUP_CLOSED, None)
        return "followup_closed", None
    if result == "already":
        edit_or_send(ctx, chat_id, message_id, T.FOLLOWUP_ALREADY, None)
        return "followup_already", None
    edit_or_send(ctx, chat_id, message_id, T.FOLLOWUP_THANKS.format(answer=T.FOLLOWUP_ANSWERS[_OPS[op]]), None,
                 confirms=Confirm("followup", txn_id=txn_id))
    sessions.save(ctx.conn, chat_id, FLOW, "photo", {"txn_id": str(txn_id)}, ttl=PHOTO_WINDOW, now=ctx.now)
    send(ctx, chat_id, T.FOLLOWUP_PHOTO_ASK, keyboard([[btn(T.BTN_SKIP, f"{PREFIX}:{txn_id.hex}:sk")]]))
    return "followup_answered", None


def on_media(ctx: Ctx, member: dict[str, Any], chat_id: int, message: dict[str, Any], sess: Session) -> str | None:
    """A photo right after answering: it belongs to the follow-up, not a new report."""
    found = media.describe(message, ctx.reports.max_download_bytes)
    if not isinstance(found, media.Item) or found.kind != "photo":
        return None
    asset = media.register(ctx.conn, member, found, now=ctx.now)
    sessions.clear(ctx.conn, chat_id)
    if not followup.attach_photo(ctx.conn, UUID(sess.payload["txn_id"]), member["member_id"], asset["asset_id"]):
        return None
    send(ctx, chat_id, T.FOLLOWUP_PHOTO_SAVED, confirms=Confirm("followup", txn_id=UUID(sess.payload["txn_id"])))
    return "followup_photo"
