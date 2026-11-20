"""The outbox: every notification goes through the job queue, never inline (slice 2b rule).

Direct replies to the person who acted (their own card, a toast) are sent right after their update
commits (pulse/bot/outgoing.py); one Telegram refused for now comes here as kind 'reply'. Anything
sent to someone else, and anything the worker sends, is written here as an alert_message row plus a
tg_send job, inside the caller's transaction: if that transaction rolls back, nothing is sent.

body is {"render": <template>, ...ids and codes} and nothing else: no text, names, amounts or notes
are stored (slice 2c). pulse/notify/render.py draws each message when the job runs, so it also
shows things as they are then (e.g. a report already acknowledged).
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from pulse.worker import queue

CARD_KINDS = ("alert", "escalation", "unsorted", "assignee")


def body(template: str, **params: Any) -> dict[str, Any]:
    from pulse.notify.render import check_params

    b = {"render": template, **{k: (str(v) if isinstance(v, UUID) else v) for k, v in params.items()}}
    check_params(b)
    return b


def linked_chat(conn: psycopg.Connection, member_id: UUID) -> int | None:
    row = conn.execute(
        """SELECT l.chat_id FROM field.tg_link l JOIN field.member m ON m.member_id = l.member_id
           WHERE l.member_id = %s AND l.revoked_at IS NULL AND m.active""",
        (member_id,),
    ).fetchone()
    return row["chat_id"] if row else None


def enqueue(conn: psycopg.Connection, *, recipient: UUID, kind: str, body: dict[str, Any], ac_id: str,
            report_id: UUID | None = None, txn_id: UUID | None = None, rule_code: str | None = None,
            local_date: date | None = None, run_after: datetime | None = None, now: datetime | None = None,
            max_attempts: int = 5) -> int:
    """now: the caller's clock (jobs run on it), so "at most once per 5 days" checks agree with it."""
    row = conn.execute(
        """INSERT INTO field.alert_message (ac_id, recipient_member_id, kind, rule_code, report_id, txn_id, body,
               local_date, created_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, COALESCE(%s, now())) RETURNING alert_id""",
        (ac_id, recipient, kind, rule_code, report_id, txn_id, Jsonb(body), local_date, now),
    ).fetchone()
    job_id = queue.enqueue(conn, "tg_send", {"alert_id": row["alert_id"]}, run_after=run_after,
                           max_attempts=max_attempts)
    conn.execute("UPDATE field.alert_message SET job_id = %s WHERE alert_id = %s", (job_id, row["alert_id"]))
    return row["alert_id"]


def enqueue_edit(conn: psycopg.Connection, alert_id: int) -> None:
    """Redraw a sent card. Coalesced: one waiting edit per card."""
    queue.enqueue(conn, "tg_edit", {"alert_id": alert_id}, dedupe_key=f"tg_edit:{alert_id}")


def enqueue_raw_edit(conn: psycopg.Connection, chat_id: int, message_id: int, *, template: str,
                     report_id: UUID, header: str | None = None) -> None:
    """Redraw a message that was not sent through the outbox (e.g. the reporter's own draft card).
    The payload names a template and the report; the text is drawn when the job runs."""
    queue.enqueue(conn, "tg_edit_raw", {"chat_id": chat_id, "message_id": message_id, "render": template,
                                        "report_id": str(report_id), "header": header},
                  dedupe_key=f"tg_edit_raw:{chat_id}:{message_id}")


def refresh_report_cards(conn: psycopg.Connection, report_id: UUID, *, except_alert: int | None = None) -> int:
    """After any change to a report: redraw every card about it that has been sent."""
    rows = conn.execute(
        """SELECT alert_id FROM field.alert_message
           WHERE report_id = %s AND kind = ANY (%s) AND status = 'sent' AND alert_id IS DISTINCT FROM %s""",
        (report_id, list(CARD_KINDS), except_alert),
    ).fetchall()
    for r in rows:
        enqueue_edit(conn, r["alert_id"])
    return len(rows)


def card_for_message(conn: psycopg.Connection, chat_id: int, message_id: int) -> dict[str, Any] | None:
    return conn.execute("SELECT * FROM field.alert_message WHERE chat_id = %s AND message_id = %s",
                        (chat_id, message_id)).fetchone()
