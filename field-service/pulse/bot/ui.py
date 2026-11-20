"""Shared bot plumbing: dependencies, per-update context, keyboards, and the reply helpers.

Inside dispatch (Ctx.defer) the reply helpers only note what to send; dispatch sends it after the user's
input is committed (pulse/bot/outgoing.py). Elsewhere they send straight away."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

import psycopg

from pulse.bot import texts_hi
from pulse.bot.client import TelegramApiError, TelegramClient
from pulse.bot.outgoing import Confirm, Out, Outgoing
from pulse.config import LedgerLimits, ReportLimits
from pulse.core_adapter import CoreAdapter

log = logging.getLogger(__name__)

CALLBACK_DATA_MAX_BYTES = 64     # Telegram Bot API limit

CB_MENU_EXPENSE = "menu:expense"
CB_MENU_REPORT = "menu:report"
CB_MENU_ME = "menu:me"
CB_MENU_GIVE = "menu:give"
MENU_CALLBACKS = (CB_MENU_EXPENSE, CB_MENU_REPORT, CB_MENU_ME, CB_MENU_GIVE)


@dataclass(frozen=True)
class BotDeps:
    client: TelegramClient
    core: CoreAdapter
    limits: LedgerLimits
    reports: ReportLimits = field(default_factory=ReportLimits)


@dataclass
class Ctx:
    """Everything a flow needs while handling one update, inside that update's transaction."""
    conn: psycopg.Connection
    client: TelegramClient
    core: CoreAdapter
    limits: LedgerLimits
    now: datetime
    reports: ReportLimits = field(default_factory=ReportLimits)
    defer: bool = False                      # dispatch: replies go out after the commit
    outgoing: Outgoing = field(default_factory=Outgoing)


def btn(text: str, data: str) -> dict:
    if len(data.encode("utf-8")) > CALLBACK_DATA_MAX_BYTES:
        raise ValueError(f"callback_data longer than {CALLBACK_DATA_MAX_BYTES} bytes: {data!r}")
    return {"text": text, "callback_data": data}


def keyboard(rows: list[list[dict]]) -> dict:
    return {"inline_keyboard": [r for r in rows if r]}


def two_columns(buttons: list[dict]) -> list[list[dict]]:
    return [buttons[i:i + 2] for i in range(0, len(buttons), 2)]


def can_give(member: dict | None) -> bool:
    return bool(member and member.get("can_give_advance") and member.get("finance_scope") != "none")


def menu_markup(member: dict | None = None) -> dict:
    """Expense, report, my account; plus give-money for members who may give advances."""
    expense, report, me = (btn(texts_hi.BTN_EXPENSE, CB_MENU_EXPENSE), btn(texts_hi.BTN_REPORT, CB_MENU_REPORT),
                           btn(texts_hi.BTN_ME, CB_MENU_ME))
    if can_give(member):
        return keyboard([[expense, btn(texts_hi.BTN_GIVE, CB_MENU_GIVE)], [report, me]])
    return keyboard([[expense, report, me]])


def send(ctx: Ctx, chat_id: int, text: str, markup: dict | None = None, *, confirms: Confirm | None = None) -> int | None:
    """Send a message. `confirms`: what it tells the user was saved, so a delayed copy can say so."""
    if ctx.defer:
        out = ctx.outgoing.add(Out("send", chat_id, text, markup, confirms=confirms))
        out.placeholder = ctx.outgoing.placeholder()
        return out.placeholder
    result = ctx.client.send_message(chat_id, text, reply_markup=markup)
    return result.get("message_id") if isinstance(result, dict) else None


def edit_or_send(ctx: Ctx, chat_id: int, message_id: int | None, text: str, markup: dict | None = None, *,
                 confirms: Confirm | None = None) -> int | None:
    """Edit the card in place; if it cannot be edited (deleted, too old), send it again instead."""
    if ctx.defer:
        out = ctx.outgoing.add(Out("edit_or_send", chat_id, text, markup, message_id=message_id, confirms=confirms))
        out.placeholder = ctx.outgoing.placeholder()
        return out.placeholder
    if message_id is not None:
        try:
            ctx.client.edit_message_text(chat_id, message_id, text, reply_markup=markup)
            return message_id
        except TelegramApiError as e:
            log.info("edit failed (%s); sending a new message", e)
    return send(ctx, chat_id, text, markup)


def edit(ctx: Ctx, chat_id: int, message_id: int, text: str, markup: dict | None = None, *, caption: bool = False,
         on_fail=None) -> None:
    """Edit a message in place only (no new message if it fails). `on_fail(conn)` runs when it cannot be
    edited, e.g. to queue a refresh job. Outside dispatch a failure is logged and on_fail runs at once."""
    if ctx.defer:
        ctx.outgoing.add(Out("edit", chat_id, text, markup, message_id=message_id, caption=caption, on_fail=on_fail))
        return
    try:
        if caption:
            ctx.client.edit_message_caption(chat_id, message_id, text, reply_markup=markup)
        else:
            ctx.client.edit_message_text(chat_id, message_id, text, reply_markup=markup)
    except TelegramApiError as e:
        if "not modified" in (e.description or "").lower():
            return
        log.info("edit failed (%s)", e)
        if on_fail is not None:
            on_fail(ctx.conn)


def delete(ctx: Ctx, chat_id: int, message_id: int | None, *, retry: bool = False) -> None:
    """Delete a message (a PIN the user typed). With retry, a failed delete is retried by a tg_delete job."""
    if message_id is None:
        return
    if ctx.defer:
        ctx.outgoing.add(Out("delete", chat_id, message_id=message_id, retry_delete=retry))
        return
    try:
        ctx.client.delete_message(chat_id, message_id)
    except Exception as e:                         # never the text: only that it failed
        log.warning("could not delete a message in chat %s (%s)", chat_id, type(e).__name__)
        if retry:
            from pulse.bot import outgoing

            member = outgoing._member_for_chat(ctx.conn, chat_id)
            outgoing.queue_delete(ctx.conn, chat_id, message_id, member, ctx.now)


def record(ctx: Ctx, chat_id: int, message_id: int | None, purpose: str, *, report_id=None, txn_id=None) -> None:
    """Note a message the bot sent directly (ids and purpose only, never text) for chat_cleanup."""
    if message_id is None:
        return
    ctx.conn.execute(
        """INSERT INTO field.bot_message (chat_id, message_id, purpose, report_id, txn_id, sent_at)
           VALUES (%s, %s, %s, %s, %s, %s)
           ON CONFLICT (chat_id, message_id) DO UPDATE SET purpose = EXCLUDED.purpose,
               report_id = COALESCE(EXCLUDED.report_id, field.bot_message.report_id),
               txn_id = COALESCE(EXCLUDED.txn_id, field.bot_message.txn_id)""",
        (chat_id, message_id, purpose, report_id, txn_id, ctx.now),
    )


def strip_buttons(ctx: Ctx, chat_id: int, message_id: int | None) -> None:
    """Best effort: remove the keyboard from an old card so only one live card remains."""
    if message_id is None:
        return
    if ctx.defer:
        ctx.outgoing.add(Out("strip", chat_id, message_id=message_id))
        return
    try:
        ctx.client.edit_message_reply_markup(chat_id, message_id, None)
    except TelegramApiError as e:
        log.info("could not remove old keyboard (%s)", e)
