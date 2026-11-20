"""Bot replies are sent after the user's input is committed, never inside its transaction.

While an update is handled (dispatch, Ctx.defer), every reply a flow makes (`ui.send`, `ui.edit_or_send`,
`ui.edit`, `ui.strip_buttons`, `ui.delete`) is only noted here. `dispatch` commits first, then calls
`flush`, still holding the chat's lock so replies of one chat go out in order:

- each reply is sent at once, so the bot stays fast;
- if Telegram fails (429, a network error, a 5xx), that reply and every later one of the chat that
  confirms something are handed to the outbox (alert_message kind 'reply', a tg_send job). The outbox
  honours retry_after on 429 and backs off otherwise, and holds a chat's later replies behind earlier ones;
- a reply drawn from the outbox later is rendered from the database at that moment and says the entry
  was already saved (pulse/notify/render.py: reply), so nobody sends it again;
- a reply that confirms nothing (a prompt, an error, the menu) is not queued; if it fails and the chat has
  an open draft, the draft card is queued instead, under "your message was received";
- message ids: `send` returns a placeholder (a negative number) that flows store as usual; after sending it
  is replaced by the real id in the chat's session and in bot_message, or cleared if the send failed;
- a PIN message whose delete fails is retried by a tg_delete job.

Outside dispatch (worker jobs, tests calling a flow directly) Ctx.defer is False and replies are sent
straight away, as the worker's own jobs are retried by the queue.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from pulse.bot.client import TelegramApiError

log = logging.getLogger(__name__)

RETRY_SOON = timedelta(seconds=2)


@dataclass(frozen=True)
class Confirm:
    """What a reply confirms, so a delayed copy can be drawn from the database: kind is
    txn | report | followup | pin (each "already saved"), or session (the open draft card)."""
    kind: str
    txn_id: UUID | None = None
    report_id: UUID | None = None
    flow: str | None = None
    sid: str | None = None

    def key(self) -> tuple:
        return (self.kind, self.txn_id, self.report_id, self.sid)


@dataclass
class Out:
    op: str                                   # send | edit_or_send | edit | strip | delete
    chat_id: int
    text: str | None = None
    markup: dict | None = None
    message_id: int | None = None             # target (edit, strip, delete); may be a placeholder
    placeholder: int | None = None            # what send / edit_or_send returned to the flow
    caption: bool = False
    confirms: Confirm | None = None
    on_fail: Callable[[psycopg.Connection], None] | None = None   # e.g. queue a card refresh job
    retry_delete: bool = False


@dataclass
class Outgoing:
    items: list[Out] = field(default_factory=list)
    _next: int = 0

    def placeholder(self) -> int:
        self._next -= 1
        return self._next

    def add(self, out: Out) -> Out:
        self.items.append(out)
        return out


def transient(e: Exception) -> bool:
    """Worth retrying: 429, a 5xx, or no answer at all (network). Not: blocked, bad request."""
    if isinstance(e, TelegramApiError):
        return e.error_code is None or e.error_code == 429 or e.error_code >= 500
    return True                               # anything else from the client: treat as the network


def _not_modified(e: Exception) -> bool:
    return isinstance(e, TelegramApiError) and "not modified" in (e.description or "").lower()


def _real(ids: dict[int, int | None], mid: int | None) -> int | None:
    if mid is None:
        return None
    return ids.get(mid) if mid < 0 else mid


def _send(client, chat_id: int, text: str, markup: dict | None) -> int | None:
    result = client.send_message(chat_id, text, reply_markup=markup)
    return result.get("message_id") if isinstance(result, dict) else None


def _edit(client, out: Out, mid: int) -> None:
    if out.caption:
        client.edit_message_caption(out.chat_id, mid, out.text, reply_markup=out.markup)
    else:
        client.edit_message_text(out.chat_id, mid, out.text, reply_markup=out.markup)


def _member_for_chat(conn: psycopg.Connection, chat_id: int) -> dict[str, Any] | None:
    return conn.execute(
        """SELECT m.member_id, m.ac_id FROM field.tg_link l JOIN field.member m ON m.member_id = l.member_id
           WHERE l.chat_id = %s AND l.revoked_at IS NULL AND m.active""", (chat_id,)).fetchone()


def _open_session(conn: psycopg.Connection, chat_id: int, now: datetime) -> Confirm | None:
    from pulse.bot.cards import FINISHED_STATES

    s = conn.execute("SELECT flow, state, payload, expires_at FROM field.tg_session WHERE chat_id = %s",
                     (chat_id,)).fetchone()
    if s is None or (s["expires_at"] is not None and s["expires_at"] <= now) or not (s["payload"] or {}).get("sid"):
        return None
    if s["state"] in FINISHED_STATES:                # a saved entry or filed report: its own reply covers it
        return None
    return Confirm("session", flow=s["flow"], sid=s["payload"]["sid"])


def has_queued_replies(conn: psycopg.Connection, member_id: UUID) -> bool:
    return conn.execute("SELECT 1 FROM field.alert_message WHERE recipient_member_id = %s AND kind = 'reply' "
                        "AND status = 'queued' LIMIT 1", (member_id,)).fetchone() is not None


def queue_reply(conn: psycopg.Connection, member: dict[str, Any], c: Confirm, now: datetime,
                run_after: datetime | None = None) -> int:
    from pulse.notify import outbox

    params = {"confirm": c.kind}
    if c.kind == "session":
        params.update(flow=c.flow, sid=c.sid)
    return outbox.enqueue(conn, recipient=member["member_id"], kind="reply", ac_id=member["ac_id"],
                          body=outbox.body("reply", **params), txn_id=c.txn_id, report_id=c.report_id,
                          run_after=run_after, now=now, max_attempts=12)


def queue_delete(conn: psycopg.Connection, chat_id: int, message_id: int, member: dict[str, Any] | None,
                 now: datetime) -> None:
    from pulse.worker import queue

    queue.enqueue(conn, "tg_delete", {"chat_id": chat_id, "message_id": message_id,
                                      "member_id": str(member["member_id"]) if member else None,
                                      "ac_id": member["ac_id"] if member else None},
                  run_after=now + RETRY_SOON, max_attempts=8)


def flush(conn: psycopg.Connection, client, chat_id: int | None, outgoing: Outgoing, now: datetime) -> dict[str, int]:
    """Send what the update produced, in order, inside a transaction holding the chat's lock. Never raises
    for a Telegram failure: what could not be sent is queued (see the module docstring)."""
    counts = {"sent": 0, "queued": 0, "dropped": 0}
    if not outgoing.items:
        return counts
    member = _member_for_chat(conn, chat_id) if chat_id is not None else None
    # replies of this chat already waiting in the outbox: new ones queue behind them, never overtake
    down = member is not None and has_queued_replies(conn, member["member_id"])
    retry_at: datetime | None = None
    ids: dict[int, int | None] = {}
    to_queue: list[Confirm] = []
    fallback_session = False

    def failed(out: Out, e: Exception | None) -> None:
        nonlocal down, retry_at, fallback_session
        if e is not None:
            log.warning("reply %s to chat %s not sent (%s)", out.op, out.chat_id, e)
            if transient(e):
                down = True
                if isinstance(e, TelegramApiError) and e.error_code == 429 and e.retry_after:
                    retry_at = max(retry_at or now, now + timedelta(seconds=e.retry_after))
        if out.placeholder is not None:
            ids[out.placeholder] = None
        if out.on_fail is not None:
            out.on_fail(conn)
        if out.op in ("send", "edit_or_send"):
            if out.confirms is not None:
                to_queue.append(out.confirms)
            else:
                fallback_session = True
        if out.op == "delete" and out.retry_delete:
            mid = _real(ids, out.message_id)
            if mid is not None:
                queue_delete(conn, out.chat_id, mid, member, now)

    for out in outgoing.items:
        if down and out.op != "delete":
            failed(out, None)                     # queued (if it confirms something) behind the failure
            continue
        try:
            if out.op == "send":
                ids[out.placeholder] = _send(client, out.chat_id, out.text, out.markup)
            elif out.op == "edit_or_send":
                mid = _real(ids, out.message_id)
                shown = None
                if mid is not None:
                    try:
                        _edit(client, out, mid)
                        shown = mid
                    except Exception as e:
                        if _not_modified(e):
                            shown = mid
                        elif transient(e):
                            raise
                        else:
                            log.info("edit failed (%s); sending a new message", e)
                if shown is None:
                    shown = _send(client, out.chat_id, out.text, out.markup)
                ids[out.placeholder] = shown
            elif out.op == "edit":
                mid = _real(ids, out.message_id)
                if mid is None:
                    failed(out, None)
                    continue
                try:
                    _edit(client, out, mid)
                except Exception as e:
                    if not _not_modified(e):
                        raise
            elif out.op == "strip":
                mid = _real(ids, out.message_id)
                if mid is not None:
                    try:
                        client.edit_message_reply_markup(out.chat_id, mid, None)
                    except Exception as e:                    # best effort, as before
                        if transient(e):
                            raise
                        log.info("could not remove old keyboard (%s)", e)
            elif out.op == "delete":
                mid = _real(ids, out.message_id)
                if mid is not None:
                    client.delete_message(out.chat_id, mid)
            counts["sent"] += 1
        except Exception as e:
            failed(out, e)

    # the queued copies: one per thing confirmed, in order; a failed prompt brings back the open draft card
    if fallback_session:
        s = _open_session(conn, chat_id, now) if chat_id is not None else None
        if s is not None:
            to_queue.append(s)
    seen: set[tuple] = set()
    for c in to_queue:
        if member is None or c.key() in seen:
            counts["dropped"] += 1
            continue
        seen.add(c.key())
        queue_reply(conn, member, c, now, run_after=retry_at)
        counts["queued"] += 1
    _fix_ids(conn, chat_id, ids)
    return counts


def _fix_ids(conn: psycopg.Connection, chat_id: int | None, ids: dict[int, int | None]) -> None:
    """Replace placeholders the flows stored with the real message ids (or clear them if not sent)."""
    placeholders = {k: v for k, v in ids.items() if k is not None and k < 0}
    if chat_id is None or not placeholders:
        return
    row = conn.execute("SELECT payload FROM field.tg_session WHERE chat_id = %s FOR UPDATE", (chat_id,)).fetchone()
    if row is not None and row["payload"]:
        p = dict(row["payload"])
        changed = False
        for k, v in p.items():
            if isinstance(v, int) and not isinstance(v, bool) and v in placeholders:
                p[k] = placeholders[v]
                changed = True
        if changed:
            conn.execute("UPDATE field.tg_session SET payload = %s WHERE chat_id = %s", (Jsonb(p), chat_id))
    for ph, real in placeholders.items():
        if real is not None:
            conn.execute(
                """INSERT INTO field.bot_message (chat_id, message_id, purpose, report_id, txn_id, sent_at)
                   SELECT chat_id, %s, purpose, report_id, txn_id, sent_at FROM field.bot_message
                   WHERE chat_id = %s AND message_id = %s ON CONFLICT (chat_id, message_id) DO NOTHING""",
                (real, chat_id, ph))
        conn.execute("DELETE FROM field.bot_message WHERE chat_id = %s AND message_id = %s", (chat_id, ph))
