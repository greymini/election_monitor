"""Retention and cleanup jobs (LLD §8 chat_cleanup, media_retention; §11 bot hygiene).

chat_cleanup     deletes the bot's own money cards (expense, advance and return cards, acknowledgement
                 cards, balance screens, money notices, nudges, digests) older than the AC's
                 chat_cleanup_hours, so a borrowed phone does not show history. Report cards are
                 deleted only once their report is closed; cards on open reports and the card of a
                 draft still open stay. Telegram lets a bot delete messages only within 48 hours of
                 sending, so anything older is marked too old instead of tried.
media_retention  after the AC's media_retention_days (or an asset's own delete_after) removes the
                 file, marks the asset purged and keeps the row and the report.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from pulse import settings_ac
from pulse.bot.client import TelegramApiError
from pulse.db import transaction
from pulse.reports import lifecycle
from pulse.reports import service as reports

log = logging.getLogger(__name__)

TELEGRAM_DELETE_WINDOW = timedelta(hours=48)
MONEY_OUTBOX_KINDS = ("ack_request", "notice", "nudge", "digest", "digest_weekly")
REPORT_CARD_KINDS = ("alert", "escalation", "unsorted", "assignee")
_GONE = ("message to delete not found", "message can't be deleted")


def _defaults(deps) -> settings_ac.Defaults:
    return settings_ac.Defaults(ledger=deps.ledger_limits, reports=deps.report_limits,
                                retention=getattr(deps, "retention", None) or settings_ac.Defaults().retention)


def _open_cards(conn, now: datetime) -> set[tuple[int, int]]:
    """Cards of drafts still open in a session: never cleaned up from under the person."""
    rows = conn.execute("SELECT chat_id, payload->>'card' AS card FROM field.tg_session "
                        "WHERE payload ? 'card' AND expires_at > %s", (now,)).fetchall()
    return {(r["chat_id"], int(r["card"])) for r in rows if r["card"] and r["card"].isdigit()}


def _delete(deps, chat_id: int, message_id: int) -> str | None:
    """None when deleted (or already gone); otherwise the reason it could not be."""
    if deps.pace is not None:
        deps.pace()
    try:
        deps.client.delete_message(chat_id, message_id)
        return None
    except TelegramApiError as e:
        desc = (e.description or "").lower()
        if any(w in desc for w in _GONE) and "can't" not in desc:
            return None
        return f"telegram: {e.error_code or ''} {e.description or ''}".strip()[:200]


def chat_cleanup(deps, now: datetime) -> dict[str, int]:
    counts = {"deleted": 0, "too_old": 0, "failed": 0}
    with transaction(deps.pool) as conn:
        ac_ids = settings_ac.all_ac_ids(conn)
    for ac_id in ac_ids:
        with transaction(deps.pool) as conn:
            hours = settings_ac.effective(conn, ac_id, _defaults(deps)).retention.chat_cleanup_hours
            cutoff, too_old = now - timedelta(hours=hours), now - TELEGRAM_DELETE_WINDOW
            keep = _open_cards(conn, now)
            closed = list(lifecycle.CLOSED)
            direct = conn.execute(
                """SELECT b.* FROM field.bot_message b
                   JOIN field.tg_link l ON l.chat_id = b.chat_id
                   JOIN field.member m ON m.member_id = l.member_id AND m.ac_id = %(ac)s
                   LEFT JOIN field.report r ON r.report_id = b.report_id
                   WHERE b.deleted_at IS NULL AND b.delete_error IS NULL AND b.sent_at <= %(cutoff)s
                     AND b.message_id > 0                       -- never an unsent reply's placeholder
                     AND (b.purpose = 'money' OR (b.purpose = 'report_card' AND r.status = ANY (%(closed)s)))""",
                {"ac": ac_id, "cutoff": cutoff, "closed": closed},
            ).fetchall()
            queued = conn.execute(
                """SELECT a.* FROM field.alert_message a LEFT JOIN field.report r ON r.report_id = a.report_id
                   WHERE a.ac_id = %(ac)s AND a.status = 'sent' AND a.deleted_at IS NULL AND a.message_id IS NOT NULL
                     AND a.sent_at <= %(cutoff)s
                     AND (a.kind = ANY (%(money)s) OR (a.kind = ANY (%(cards)s) AND r.status = ANY (%(closed)s)))""",
                {"ac": ac_id, "cutoff": cutoff, "money": list(MONEY_OUTBOX_KINDS), "cards": list(REPORT_CARD_KINDS),
                 "closed": closed},
            ).fetchall()
            for b in direct:
                if (b["chat_id"], b["message_id"]) in keep:
                    continue
                if b["sent_at"] < too_old:
                    conn.execute("UPDATE field.bot_message SET delete_error = 'too_old' WHERE chat_id = %s AND message_id = %s",
                                 (b["chat_id"], b["message_id"]))
                    counts["too_old"] += 1
                    continue
                err = _delete(deps, b["chat_id"], b["message_id"])
                if err is None:
                    conn.execute("UPDATE field.bot_message SET deleted_at = %s WHERE chat_id = %s AND message_id = %s",
                                 (now, b["chat_id"], b["message_id"]))
                    counts["deleted"] += 1
                else:
                    conn.execute("UPDATE field.bot_message SET delete_error = %s WHERE chat_id = %s AND message_id = %s",
                                 (err, b["chat_id"], b["message_id"]))
                    counts["failed"] += 1
            for a in queued:
                if a["sent_at"] < too_old:
                    conn.execute("UPDATE field.alert_message SET deleted_at = %s, last_error = 'too_old_to_delete' "
                                 "WHERE alert_id = %s", (now, a["alert_id"]))
                    counts["too_old"] += 1
                    continue
                err = _delete(deps, a["chat_id"], a["message_id"])
                conn.execute("UPDATE field.alert_message SET deleted_at = %s, last_error = %s WHERE alert_id = %s",
                             (now, err, a["alert_id"]))
                counts["deleted" if err is None else "failed"] += 1
    return counts


def media_retention(deps, now: datetime) -> dict[str, int]:
    counts = {"purged": 0, "files_deleted": 0}
    with transaction(deps.pool) as conn:
        ac_ids = settings_ac.all_ac_ids(conn)
    for ac_id in ac_ids:
        with transaction(deps.pool) as conn:
            days = settings_ac.effective(conn, ac_id, _defaults(deps)).retention.media_retention_days
            due = conn.execute(
                """SELECT * FROM field.media_asset
                   WHERE ac_id = %(ac)s AND status = 'stored'
                     AND ((delete_after IS NOT NULL AND delete_after <= %(now)s)
                          OR (delete_after IS NULL AND %(days)s::int IS NOT NULL
                              AND created_at <= %(now)s - make_interval(days => %(days)s::int)))
                   ORDER BY created_at FOR UPDATE SKIP LOCKED""",
                {"ac": ac_id, "now": now, "days": days},
            ).fetchall()
            for a in due:
                _purge(deps, conn, a, now, counts)
    return counts


def _purge(deps, conn, a: dict[str, Any], now: datetime, counts: dict[str, int]) -> None:
    # storage is content-addressed: keep the file while another asset still needs the same bytes
    shared = conn.execute(
        """SELECT 1 FROM field.media_asset WHERE storage_key = %s AND asset_id <> %s AND status = 'stored'""",
        (a["storage_key"], a["asset_id"]),
    ).fetchone()
    if not shared:
        deps.storage.delete(a["storage_key"])
        counts["files_deleted"] += 1
    conn.execute("UPDATE field.media_asset SET status = 'purged', storage_key = NULL, purged_at = %s WHERE asset_id = %s",
                 (now, a["asset_id"]))
    for r in conn.execute("SELECT report_id FROM field.report_media WHERE asset_id = %s", (a["asset_id"],)):
        reports.event(conn, r["report_id"], "media_purged", actor=None, now=now, data={"asset_id": str(a["asset_id"])})
    counts["purged"] += 1
