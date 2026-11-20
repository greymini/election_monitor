"""Queue jobs that talk to Telegram for the outbox: tg_send, tg_edit, tg_edit_raw.

429 Too Many Requests → RetryAfter(retry_after): the job waits exactly that long and the attempt is
not counted. No link, or the bot blocked (403) → the message is marked undelivered, a report_event
records it, and the job completes: one unreachable person never holds up anyone else. If Telegram
refuses the "talk to reporter" button (the reporter's privacy settings) or the photo, the card is
sent again without it.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from psycopg.types.json import Jsonb

from pulse.bot.client import TelegramApiError
from pulse.db import transaction
from pulse.notify import outbox, render
from pulse.reports import service as reports
from pulse.worker.queue import RetryAfter

log = logging.getLogger(__name__)

_BLOCKED = ("blocked", "deactivated", "chat not found", "user not found", "kicked")
_GONE = ("message to edit not found", "message can't be edited", "message to edit not found")


def _blocked(e: TelegramApiError) -> bool:
    return e.error_code == 403 or any(w in (e.description or "").lower() for w in _BLOCKED)


def _undelivered(conn, a: dict[str, Any], reason: str, now: datetime) -> None:
    conn.execute("UPDATE field.alert_message SET status = %s, last_error = %s WHERE alert_id = %s",
                 (reason, reason, a["alert_id"]))
    if a["report_id"] is not None:
        reports.event(conn, a["report_id"], "alert_not_delivered", actor=None, now=now,
                      data={"alert_id": a["alert_id"], "recipient": str(a["recipient_member_id"]), "reason": reason,
                            "kind": a["kind"]})
    if a["kind"] == "ack_request" and a["txn_id"] is not None:
        txn = conn.execute("SELECT * FROM field.money_txn WHERE txn_id = %s", (a["txn_id"],)).fetchone()
        tell = txn["from_member_id"] if txn["to_member_id"] == a["recipient_member_id"] else txn["to_member_id"]
        if tell is not None and tell != a["recipient_member_id"]:
            outbox.enqueue(conn, recipient=tell, kind="notice", ac_id=a["ac_id"], txn_id=a["txn_id"],
                           body=outbox.body("undelivered", recipient_member_id=a["recipient_member_id"]))


def _content(deps, conn, a: dict[str, Any], now: datetime):
    return render.render(conn, deps.core, a, now)


def tg_send(deps, job: dict[str, Any], now: datetime) -> str:
    with transaction(deps.pool) as conn:
        a = conn.execute("SELECT * FROM field.alert_message WHERE alert_id = %s FOR UPDATE",
                         (job["payload"]["alert_id"],)).fetchone()
        if a is None or a["status"] != "queued":
            return "skipped"
        chat_id = outbox.linked_chat(conn, a["recipient_member_id"])
        if chat_id is None:
            _undelivered(conn, a, "no_link", now)
            return "no_link"
        if a["kind"] == "reply" and conn.execute(
                """SELECT 1 FROM field.alert_message WHERE recipient_member_id = %s AND kind = 'reply'
                   AND status = 'queued' AND alert_id < %s LIMIT 1""", (a["recipient_member_id"], a["alert_id"])).fetchone():
            raise RetryAfter(2)                            # an earlier reply of this chat goes first
        for _ in range(3):                                 # at most: as is, without contact button, without photo
            drawn = _content(deps, conn, a, now)
            if drawn is None:                              # nothing to say any more (or a redacted old body)
                conn.execute("UPDATE field.alert_message SET status = 'skipped' WHERE alert_id = %s", (a["alert_id"],))
                return "skipped"
            text, markup, photo = drawn
            if deps.pace is not None:
                deps.pace()
            try:
                if photo:
                    result = deps.client.send_photo(chat_id, photo, caption=text, reply_markup=markup)
                else:
                    result = deps.client.send_message(chat_id, text, reply_markup=markup)
                break
            except TelegramApiError as e:
                desc = (e.description or "").lower()
                if e.error_code == 429:
                    raise RetryAfter(e.retry_after or 5) from None
                if _blocked(e):
                    _undelivered(conn, a, "blocked", now)
                    return "blocked"
                fix = ("no_contact" if "button_user" in desc or "privacy" in desc
                       else "no_photo" if photo and ("file" in desc or "photo" in desc) else None)
                if fix is None or (a["body"] or {}).get(fix):
                    raise
                a = dict(a, body={**(a["body"] or {}), fix: True})
                conn.execute("UPDATE field.alert_message SET body = %s WHERE alert_id = %s",
                             (Jsonb(a["body"]), a["alert_id"]))
        else:
            raise RuntimeError("could not send after removing contact button and photo")
        message_id = result.get("message_id") if isinstance(result, dict) else None
        if a["kind"] == "reply" and (a["body"] or {}).get("confirm") == "session" and message_id is not None:
            _adopt_card(conn, chat_id, a["body"], message_id, now)
        conn.execute(
            """UPDATE field.alert_message SET status = 'sent', chat_id = %s, message_id = %s, is_media = %s,
                   sent_at = %s, last_error = NULL WHERE alert_id = %s""",
            (chat_id, message_id, bool(photo), now, a["alert_id"]),
        )
        return "sent"


def _adopt_card(conn, chat_id: int, body: dict[str, Any], message_id: int, now: datetime) -> None:
    """A draft card delivered late is the draft's card from now on (its buttons edit it in place)."""
    s = conn.execute("SELECT flow, payload FROM field.tg_session WHERE chat_id = %s FOR UPDATE", (chat_id,)).fetchone()
    if s is None or s["flow"] != body.get("flow") or (s["payload"] or {}).get("sid") != body.get("sid"):
        return
    conn.execute("UPDATE field.tg_session SET payload = payload || jsonb_build_object('card', %s::bigint) "
                 "WHERE chat_id = %s", (message_id, chat_id))
    purpose = "report_card" if body.get("flow") == "report" else "money"
    conn.execute("""INSERT INTO field.bot_message (chat_id, message_id, purpose, sent_at) VALUES (%s, %s, %s, %s)
                    ON CONFLICT (chat_id, message_id) DO NOTHING""", (chat_id, message_id, purpose, now))


def tg_delete(deps, job: dict[str, Any], now: datetime) -> str:
    """Retry deleting a message the bot must not leave in the chat (a PIN). Only ids are kept."""
    p = job["payload"]
    if deps.pace is not None:
        deps.pace()
    try:
        deps.client.delete_message(p["chat_id"], p["message_id"])
    except TelegramApiError as e:
        if e.error_code == 429:
            raise RetryAfter(e.retry_after or 5) from None
        if "not found" in (e.description or "").lower():
            return "gone"
        raise
    return "deleted"


def tg_delete_failed(deps, job: dict[str, Any], error: str, now: datetime) -> None:
    """Every attempt failed: ask the member to delete it themselves (outbox, no text stored)."""
    p = job["payload"]
    if not p.get("member_id"):
        return
    with transaction(deps.pool) as conn:
        outbox.enqueue(conn, recipient=p["member_id"], kind="reply", ac_id=p["ac_id"],
                       body=outbox.body("reply", confirm="pin_not_deleted"), now=now, max_attempts=12)


def tg_send_failed(deps, job: dict[str, Any], error: str, now: datetime) -> None:
    with transaction(deps.pool) as conn:
        a = conn.execute("SELECT * FROM field.alert_message WHERE alert_id = %s FOR UPDATE",
                         (job["payload"]["alert_id"],)).fetchone()
        if a is not None and a["status"] == "queued":
            _undelivered(conn, a, "failed", now)


def tg_edit(deps, job: dict[str, Any], now: datetime) -> str:
    with transaction(deps.pool) as conn:
        a = conn.execute("SELECT * FROM field.alert_message WHERE alert_id = %s", (job["payload"]["alert_id"],)).fetchone()
        if a is None or a["status"] != "sent" or a["message_id"] is None or a["deleted_at"] is not None:
            return "skipped"
        drawn = _content(deps, conn, a, now)
        if drawn is None:
            return "skipped"
        text, markup, _ = drawn
        return _edit(deps, a["chat_id"], a["message_id"], text, markup, caption=a["is_media"])


def tg_edit_raw(deps, job: dict[str, Any], now: datetime) -> str:
    p = job["payload"]
    if "render" not in p:
        return "skipped"                                   # redacted by field_0009
    with transaction(deps.pool) as conn:
        drawn = render.TEMPLATES[p["render"]](conn, deps.core, {"report_id": p["report_id"],
                                                                "body": {"header": p.get("header")}}, now)
    if drawn is None:
        return "skipped"
    text, markup, _ = drawn
    return _edit(deps, p["chat_id"], p["message_id"], text, markup, caption=False)


def _edit(deps, chat_id: int, message_id: int, text: str, markup: dict | None, *, caption: bool) -> str:
    if deps.pace is not None:
        deps.pace()
    try:
        if caption:
            deps.client.edit_message_caption(chat_id, message_id, text, reply_markup=markup)
        else:
            deps.client.edit_message_text(chat_id, message_id, text, reply_markup=markup)
    except TelegramApiError as e:
        if e.error_code == 429:
            raise RetryAfter(e.retry_after or 5) from None
        if _blocked(e) or any(w in (e.description or "").lower() for w in _GONE):
            return "gone"
        raise
    return "edited"


class Pace:
    """At most `per_second` Telegram calls per worker (Telegram allows about 30/s overall)."""

    def __init__(self, per_second: float = 25.0, clock=None, sleep=None):
        import time

        self.gap = 1.0 / per_second
        self.clock = clock or time.monotonic
        self.sleep = sleep or time.sleep
        self.last = 0.0

    def __call__(self) -> None:
        wait = self.last + self.gap - self.clock()
        if wait > 0:
            self.sleep(wait)
        self.last = self.clock()

