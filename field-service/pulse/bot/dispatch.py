"""The one dispatcher for every Telegram update, whether it came by long polling or webhook.

Order: record update_id (duplicates stop here) → private chats only → /start linking →
unlinked users get one fixed reply → linked users are routed to a flow.

Recording the update and handling it share one transaction. Replies are sent only AFTER it commits
(pulse/bot/outgoing.py), so no Telegram failure can undo or hide what the user sent: a reply that
cannot be sent goes to the outbox and arrives later, saying the entry was already saved. The chat's
lock is held from before the transaction until the replies are out, so one chat's replies keep order.
If handling fails before the commit, the transaction rolls back, the update is recorded with outcome
'error' in a fresh one (handled at most once, never retried into a loop) and the user is told in Hindi
that it was NOT saved: the only case that says so. Every callback query from a private chat is answered
exactly once, after the commit.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from psycopg_pool import ConnectionPool

from pulse import settings_ac
from pulse.bot import (cards, flow_ack, flow_advance, flow_expense, flow_followup, flow_me, flow_menu, flow_pin,
                       flow_report, flow_return, flow_triage)
from pulse.bot import session as sessions, texts_hi
from pulse.bot.client import TelegramApiError
from pulse.bot.flow_link import handle_start, parse_start
from pulse.bot import outgoing
from pulse.bot.ui import BotDeps, Ctx, delete, send
from pulse.common.clock import utcnow
from pulse.reports import media
from pulse.team import pin as pins
from pulse.team.linking import active_member_for_telegram_user

log = logging.getLogger(__name__)

DUPLICATE = "duplicate"
INVALID = "invalid_update"
ERROR = "error"
IGNORED_NOT_PRIVATE = "ignored_not_private"
IGNORED_UNSUPPORTED = "ignored_unsupported"
UNLINKED = "unlinked"

Routed = tuple[str, str | None, bool]   # (outcome, callback toast, answer the callback?)


def dispatch(pool: ConnectionPool, bot: BotDeps, update: dict[str, Any], *, now: datetime | None = None) -> str:
    update_id = update.get("update_id") if isinstance(update, dict) else None
    if not isinstance(update_id, int) or isinstance(update_id, bool):
        return INVALID
    now = now or utcnow()
    chat_id = _private_chat_id(update)
    with pool.connection() as conn:
        if chat_id is not None:                   # held until the replies are out: one chat, one order
            conn.execute("SELECT pg_advisory_lock(%s, hashtext(%s))", (CHAT_LOCK_CLASS, str(chat_id)))
        try:
            return _dispatch(conn, bot, update, update_id, chat_id, now)
        finally:
            if chat_id is not None:
                conn.execute("SELECT pg_advisory_unlock(%s, hashtext(%s))", (CHAT_LOCK_CLASS, str(chat_id)))


def _dispatch(conn, bot: BotDeps, update: dict[str, Any], update_id: int, chat_id: int | None, now: datetime) -> str:
    ctx = Ctx(conn=conn, client=bot.client, core=bot.core, limits=bot.limits, now=now, reports=bot.reports, defer=True)
    try:
        with conn.transaction():
            claimed = conn.execute(
                "INSERT INTO field.tg_update (update_id) VALUES (%s) ON CONFLICT DO NOTHING RETURNING update_id",
                (update_id,),
            ).fetchone()
            if claimed is None:
                return DUPLICATE
            outcome, toast, answer = _route(ctx, update)
            conn.execute("UPDATE field.tg_update SET outcome = %s WHERE update_id = %s", (outcome, update_id))
    except Exception:
        log.exception("update %s failed", update_id)
        with conn.transaction():
            conn.execute(
                """INSERT INTO field.tg_update (update_id, outcome) VALUES (%s, %s)
                   ON CONFLICT (update_id) DO UPDATE SET outcome = EXCLUDED.outcome""",
                (update_id, ERROR),
            )
        _tell_failure(bot, update)
        return ERROR
    # committed: from here on nothing may undo it. Send the replies; what fails goes to the outbox.
    try:
        with conn.transaction():
            outgoing.flush(conn, bot.client, chat_id, ctx.outgoing, ctx.now)
    except Exception:
        log.exception("update %s: replies could not be sent or queued (the input is saved)", update_id)
    if answer:
        _answer(bot, update["callback_query"]["id"], toast)
    return outcome


CHAT_LOCK_CLASS = 1347


def _was_linked(ctx: Ctx, telegram_user_id: int) -> bool:
    return ctx.conn.execute("SELECT 1 FROM field.tg_link WHERE telegram_user_id = %s LIMIT 1",
                            (telegram_user_id,)).fetchone() is not None


def _seen(ctx: Ctx, member: dict[str, Any]) -> None:
    """For team activity (last seen): the time of the member's latest update to the bot."""
    ctx.conn.execute("UPDATE field.tg_link SET last_seen_at = %s WHERE member_id = %s AND revoked_at IS NULL",
                     (ctx.now, member["member_id"]))


def _apply_ac_settings(ctx: Ctx, member: dict[str, Any]) -> None:
    """Limits for this member's AC: environment defaults overridden by field.ac_setting."""
    eff = settings_ac.effective(ctx.conn, member["ac_id"], settings_ac.Defaults(ledger=ctx.limits, reports=ctx.reports))
    ctx.limits, ctx.reports = eff.ledger, eff.reports


def lock_chat(conn, chat_id: int) -> None:
    """Updates of one chat are handled one at a time. Telegram delivers an album as several updates
    in quick succession; without this two of them could each open a report draft."""
    conn.execute("SELECT pg_advisory_xact_lock(%s, hashtext(%s))", (CHAT_LOCK_CLASS, str(chat_id)))


def _answer(bot: BotDeps, callback_query_id: str, text: str | None) -> None:
    # after commit: a failed answer (e.g. query too old) must not undo what was saved
    try:
        bot.client.answer_callback_query(callback_query_id, text)
    except TelegramApiError as e:
        log.warning("answerCallbackQuery failed: %s", e)


def _private_chat_id(update: dict[str, Any]) -> int | None:
    msg = update.get("message") or (update.get("callback_query") or {}).get("message") or {}
    chat = msg.get("chat") or {}
    return chat.get("id") if chat.get("type") == "private" else None


def _tell_failure(bot: BotDeps, update: dict[str, Any]) -> None:
    """Never fail silently: tell the sender it was not saved. Only for a failure before the commit.
    Best effort; logged if that fails too."""
    chat_id = _private_chat_id(update)
    if chat_id is None:
        return
    cq = update.get("callback_query")
    if cq and cq.get("id"):
        _answer(bot, cq["id"], texts_hi.FAILED_NOT_SAVED)
    try:
        bot.client.send_message(chat_id, texts_hi.FAILED_NOT_SAVED)
    except Exception:
        log.exception("could not tell chat %s that its update failed", chat_id)


def _route(ctx: Ctx, update: dict[str, Any]) -> Routed:
    if "message" in update:
        return _on_message(ctx, update["message"]), None, False
    if "callback_query" in update:
        return _on_callback(ctx, update["callback_query"])
    if "channel_post" in update or "edited_channel_post" in update:
        return IGNORED_NOT_PRIVATE, None, False
    return IGNORED_UNSUPPORTED, None, False


def _private_sender(message: dict[str, Any]) -> tuple[int, int] | None:
    """(telegram_user_id, chat_id) for a message in a private chat with a human, else None."""
    chat = message.get("chat") or {}
    sender = message.get("from") or {}
    if chat.get("type") != "private" or not sender or sender.get("is_bot"):
        return None
    return sender["id"], chat["id"]


def _on_message(ctx: Ctx, message: dict[str, Any]) -> str:
    ids = _private_sender(message)
    if ids is None:
        return IGNORED_NOT_PRIVATE
    telegram_user_id, chat_id = ids
    text = message.get("text")

    token = parse_start(text or "")
    if token is not None:
        return handle_start(ctx, telegram_user_id, chat_id, token)

    member = active_member_for_telegram_user(ctx.conn, telegram_user_id)
    if member is None:
        if text and _was_linked(ctx, telegram_user_id) and pins.looks_like_pin(text):
            # deactivated or unlinked while a PIN step was open: never leave a PIN in the chat
            delete(ctx, chat_id, message.get("message_id"), retry=True)
        send(ctx, chat_id, texts_hi.UNREGISTERED)
        return UNLINKED
    _apply_ac_settings(ctx, member)
    _seen(ctx, member)

    if text:
        outcome = flow_pin.on_text(ctx, member, chat_id, message.get("message_id"), text)   # before anything else
        if outcome is not None:
            return outcome
    if media.describe(message, ctx.reports.max_download_bytes) is not None:
        outcome = _on_media(ctx, member, chat_id, message)
        if outcome is not None:
            return outcome
    if text:
        outcome = _on_text(ctx, member, chat_id, text)
        if outcome is not None:
            return outcome
    flow_menu.show_menu(ctx, chat_id, member=member)
    return "menu"


def _on_media(ctx: Ctx, member: dict[str, Any], chat_id: int, message: dict[str, Any]) -> str | None:
    """Photos, voice, video, files and locations. An open expense draft gets first say (receipt step,
    or hold and ask); otherwise the item opens or joins a report."""
    sess = sessions.load(ctx.conn, chat_id)
    if sess is not None and sess.flow == flow_followup.FLOW and not sess.expired(ctx.now):
        outcome = flow_followup.on_media(ctx, member, chat_id, message, sess)   # photo of the finished work
        if outcome is not None:
            return outcome
    if sess is not None and sess.flow == flow_triage.FLOW and cards.is_open(sess, ctx):
        outcome = flow_triage.on_media(ctx, member, chat_id, message, sess)     # closing photo
        if outcome is not None:
            return outcome
    if sess is not None and sess.flow == flow_expense.FLOW and cards.is_open(sess, ctx):
        outcome = flow_expense.on_media(ctx, member, chat_id, message, sess)
        if outcome is not None:
            return outcome
    return flow_report.on_media(ctx, member, chat_id, message, sess)


_TEXT_FLOWS = {flow_advance.FLOW: flow_advance, flow_return.FLOW: flow_return}


def _on_text(ctx: Ctx, member: dict[str, Any], chat_id: int, text: str) -> str | None:
    """An open advance or return draft gets first say; then the "diya" shortcut; then expenses
    (which also handle their own open draft). A draft that a new command replaces is closed with a notice."""
    sess = sessions.load(ctx.conn, chat_id)
    if sess is not None and sess.flow == flow_triage.FLOW and cards.is_open(sess, ctx):
        outcome = flow_triage.on_text(ctx, member, chat_id, text, sess)         # closing note
        if outcome is not None:
            return outcome
    if sess is not None and sess.flow == flow_report.FLOW:
        outcome = flow_report.on_text(ctx, member, chat_id, text, sess)
        if outcome is not None:
            return outcome
    if sess is not None and sess.flow in _TEXT_FLOWS and sess.state != cards.POSTED:
        if sess.expired(ctx.now):
            cards.expire(ctx, chat_id, sess)
        else:
            outcome = _TEXT_FLOWS[sess.flow].on_text(ctx, member, chat_id, text, sess)
            if outcome is not None:
                return outcome
            cards.abandon(ctx, chat_id, sess)
        sess = None
    outcome = flow_report.try_urgent_text(ctx, member, chat_id, text, sess)
    if outcome is not None:
        return outcome
    outcome = flow_advance.try_shortcut(ctx, member, chat_id, text, sess)
    if outcome is not None:
        return outcome
    return flow_expense.on_text(ctx, member, chat_id, text,
                                sess if sess is not None and sess.flow == flow_expense.FLOW else None)


def _on_callback(ctx: Ctx, cq: dict[str, Any]) -> Routed:
    message = cq.get("message")
    if not message:  # inline-mode callbacks carry no chat
        return IGNORED_UNSUPPORTED, None, False
    chat = message.get("chat") or {}
    sender = cq.get("from") or {}
    if chat.get("type") != "private" or not sender or sender.get("is_bot"):
        return IGNORED_NOT_PRIVATE, None, False
    chat_id, message_id = chat["id"], message.get("message_id")

    member = active_member_for_telegram_user(ctx.conn, sender["id"])
    if member is None:
        send(ctx, chat_id, texts_hi.UNREGISTERED)
        return UNLINKED, None, True
    _apply_ac_settings(ctx, member)
    _seen(ctx, member)

    data = cq.get("data") or ""
    if data.startswith("menu:"):
        outcome, toast = flow_menu.handle_callback(ctx, member, chat_id, message_id, data)
    elif data.startswith("e:"):
        outcome, toast = flow_expense.on_callback(ctx, member, chat_id, message_id, data,
                                                  sessions.load(ctx.conn, chat_id))
    elif data.startswith("g:"):
        outcome, toast = flow_advance.on_callback(ctx, member, chat_id, message_id, data,
                                                  sessions.load(ctx.conn, chat_id))
    elif data.startswith("r:"):
        outcome, toast = flow_return.on_callback(ctx, member, chat_id, message_id, data,
                                                 sessions.load(ctx.conn, chat_id))
    elif data.startswith("k:"):
        outcome, toast = flow_ack.on_callback(ctx, member, chat_id, message_id, data)
    elif data.startswith("m:"):
        outcome, toast = flow_me.on_callback(ctx, member, chat_id, message_id, data, sessions.load(ctx.conn, chat_id))
    elif data.startswith("p:"):
        outcome, toast = flow_report.on_callback(ctx, member, chat_id, message_id, data, sessions.load(ctx.conn, chat_id))
    elif data.startswith("q:"):
        outcome, toast = flow_report.on_report_callback(ctx, member, chat_id, message_id, data,
                                                        sessions.load(ctx.conn, chat_id))
    elif data.startswith("h:"):
        outcome, toast = flow_expense.on_hold_callback(ctx, member, chat_id, message_id, data,
                                                       sessions.load(ctx.conn, chat_id))
    elif data.startswith("t:"):
        outcome, toast = flow_triage.on_alert_callback(ctx, member, chat_id, message_id, data)
    elif data.startswith("w:"):
        outcome, toast = flow_triage.on_work_callback(ctx, member, chat_id, message_id, data,
                                                      sessions.load(ctx.conn, chat_id))
    elif data.startswith("z:"):
        outcome, toast = flow_triage.on_skip(ctx, member, chat_id, message_id, data, sessions.load(ctx.conn, chat_id))
    elif data.startswith("f:"):
        outcome, toast = flow_followup.on_callback(ctx, member, chat_id, message_id, data,
                                                   sessions.load(ctx.conn, chat_id))
    elif data.startswith("u:"):
        outcome, toast = flow_expense.on_undo(ctx, member, chat_id, message_id, data)
    else:
        outcome, toast = "card_stale", texts_hi.CARD_STALE
    return outcome, toast, True

