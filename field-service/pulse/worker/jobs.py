"""Worker jobs (LLD §8).

Queue jobs (field.job):   media_fetch, transcribe, report_card, tg_send, tg_edit, tg_edit_raw
Timed jobs:               report_autofile (every minute), urgent_escalate, digest_daily and
                          digest_weekly (every 5 minutes; the digests send once a day from 20:00 IST
                          and once a week from Monday 09:00 IST), followup_ask (every 15 minutes,
                          10:00-20:00 IST), ack_remind, ack_flag, balance_nudge and chat_cleanup
                          (hourly), media_retention (daily), label_review (weekly)

Lock order everywhere: the chat first (dispatch.lock_chat), then report or entry rows. The
dispatcher takes the same order, so the worker and live updates never deadlock.
"""
from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any, Callable, Optional
from uuid import UUID

from psycopg_pool import ConnectionPool

from pulse import asr
from pulse.bot import flow_ack, flow_report
from pulse.bot.client import TelegramClient
from pulse.bot.dispatch import lock_chat
from pulse.bot.ui import Ctx
from pulse.common.storage import Storage
from pulse import settings_ac
from pulse.config import LedgerLimits, ReportLimits, Retention
from pulse.core_adapter import CoreAdapter
from pulse.db import transaction
from pulse.common.clock import IST
from pulse.ledger import followup, labels, reminders
from pulse.ledger import service as ledger
from pulse.notify import alerts, digest, nudge, outbox, sender
from pulse.notify import digest_weekly as weekly
from pulse.reports import media
from pulse.reports import service as reports
from pulse.reports import transcripts
from pulse.worker import maintenance, queue

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class WorkerDeps:
    pool: ConnectionPool
    client: TelegramClient
    core: CoreAdapter
    storage: Storage
    ledger_limits: LedgerLimits
    report_limits: ReportLimits
    worker_id: str
    pace: Optional[Callable[[], None]] = None     # Telegram rate pacing; None in tests
    retention: Retention = field(default_factory=Retention)
    asr_engine: str = "none"                       # pulse/asr/<name>.py


def effective(deps: "WorkerDeps", conn, ac_id: str) -> settings_ac.Effective:
    """This AC's limits: the worker's defaults overridden by field.ac_setting."""
    return settings_ac.effective(conn, ac_id, settings_ac.Defaults(ledger=deps.ledger_limits,
                                                                    reports=deps.report_limits,
                                                                    retention=deps.retention))


def ctx_for(deps: WorkerDeps, conn, now: datetime) -> Ctx:
    return Ctx(conn=conn, client=deps.client, core=deps.core, limits=deps.ledger_limits, now=now,
               reports=deps.report_limits)


# ---- queue jobs -------------------------------------------------------------------------------

def media_fetch(deps: WorkerDeps, job: dict[str, Any], now: datetime) -> str:
    asset_id = UUID(job["payload"]["asset_id"])
    try:
        with transaction(deps.pool) as conn:
            result = media.fetch(conn, deps.client, deps.storage, asset_id,
                                 max_bytes=deps.report_limits.max_download_bytes, now=now)
            if result in ("stored", "reused") and deps.asr_engine != "none":
                transcripts.queue_for_asset(conn, asset_id, now=now)       # filing never waits for it
            return result
    except media.PermanentFailure as e:
        media_fetch_failed(deps, job, str(e), now)
        return "failed"


def media_fetch_failed(deps: WorkerDeps, job: dict[str, Any], error: str, now: datetime) -> None:
    """Last attempt failed: mark the asset failed, keep the report, tell the reporter once."""
    asset_id = UUID(job["payload"]["asset_id"])
    with transaction(deps.pool) as conn:
        linked = media.mark_failed(conn, asset_id, error, now=now)
    for report_id in linked:
        with transaction(deps.pool) as conn:
            report = reports.get(conn, report_id)
            chat_id = flow_ack.member_chat(ctx_for(deps, conn, now), report["reporter_member_id"])
            if chat_id is not None:
                lock_chat(conn, chat_id)
            flow_report.tell_media_failed(ctx_for(deps, conn, now), report)


UNLINKED_WAIT = timedelta(hours=2)        # a held voice note may join a report a little later


def transcribe(deps: WorkerDeps, job: dict[str, Any], now: datetime) -> str:
    """Run the configured engine on one stored voice note and apply it to every report it is in."""
    asset_id = UUID(job["payload"]["asset_id"])
    engine = asr.get_engine(deps.asr_engine)
    with transaction(deps.pool) as conn:
        asset = conn.execute("SELECT * FROM field.media_asset WHERE asset_id = %s", (asset_id,)).fetchone()
        if asset is None or asset["status"] != "stored":
            return "no_file"
        rids = transcripts.linked_reports(conn, asset_id)
        if engine is None:
            for rid in rids:
                transcripts.fail(conn, rid, asset_id, "no engine configured", now=now)
            return "no_engine"
        if not rids:
            if now - asset["created_at"] < UNLINKED_WAIT:
                raise queue.RetryAfter(300)
            return "no_report"
        done = {r["report_id"] for r in conn.execute(
            "SELECT report_id FROM field.report_transcript WHERE asset_id = %s AND status = 'done'", (asset_id,))}
        if set(rids) <= done:
            return "already"
        data = deps.storage.get(asset["storage_key"])
    with tempfile.TemporaryDirectory(prefix="pulse-asr-") as tmp:
        src = Path(tmp) / ("original" + _AUDIO_EXT.get(asset["mime"] or "", ".bin"))
        src.write_bytes(data)
        path, converted = asr.convert(src, Path(tmp))
        result = asr.transcribe(path, "hi", engine=engine)          # an exception retries the job
    with transaction(deps.pool) as conn:
        for rid in transcripts.linked_reports(conn, asset_id):
            if rid not in done:
                transcripts.apply(conn, rid, asset_id, result, converted=converted, now=now)
    return "done" if converted else "done_unconverted"


_AUDIO_EXT = {"audio/ogg": ".ogg", "audio/mpeg": ".mp3", "audio/mp4": ".m4a", "audio/x-m4a": ".m4a",
              "audio/wav": ".wav", "audio/x-wav": ".wav", "audio/aac": ".aac", "audio/amr": ".amr"}


def transcribe_failed(deps: WorkerDeps, job: dict[str, Any], error: str, now: datetime) -> None:
    asset_id = UUID(job["payload"]["asset_id"])
    with transaction(deps.pool) as conn:
        for rid in transcripts.linked_reports(conn, asset_id):
            transcripts.fail(conn, rid, asset_id, error, now=now)


def report_card(deps: WorkerDeps, job: dict[str, Any], now: datetime) -> str:
    chat_id, report_id = job["payload"]["chat_id"], UUID(job["payload"]["report_id"])
    with transaction(deps.pool) as conn:
        lock_chat(conn, chat_id)
        return flow_report.refresh_card(ctx_for(deps, conn, now), chat_id, report_id)


QUEUE: dict[str, Callable[[WorkerDeps, dict[str, Any], datetime], str]] = {
    "media_fetch": media_fetch,
    "transcribe": transcribe,
    "report_card": report_card,
    "tg_send": sender.tg_send,
    "tg_edit": sender.tg_edit,
    "tg_edit_raw": sender.tg_edit_raw,
    "tg_delete": sender.tg_delete,
}
ON_FINAL_FAILURE: dict[str, Callable[[WorkerDeps, dict[str, Any], str, datetime], None]] = {
    "media_fetch": media_fetch_failed,
    "transcribe": transcribe_failed,
    "tg_send": sender.tg_send_failed,
    "tg_delete": sender.tg_delete_failed,
}


# ---- timed jobs -------------------------------------------------------------------------------

def report_autofile(deps: WorkerDeps, now: datetime) -> dict[str, int]:
    """File drafts untouched for autofile_minutes. Reads the database only, so drafts left by a
    stopped or restarted process are filed too."""
    with transaction(deps.pool) as conn:
        candidates = []
        for ac_id in settings_ac.all_ac_ids(conn):
            limits = effective(deps, conn, ac_id).reports
            candidates += [(r["report_id"], limits.autofile_minutes) for r in reports.drafts_due(conn, now, limits)
                           if r["ac_id"] == ac_id]
    filed = 0
    for report_id, minutes in candidates:
        with transaction(deps.pool) as conn:
            ctx = ctx_for(deps, conn, now)
            row = conn.execute("SELECT reporter_member_id FROM field.report WHERE report_id = %s", (report_id,)).fetchone()
            chat_id = flow_ack.member_chat(ctx, row["reporter_member_id"])
            if chat_id is not None:
                lock_chat(conn, chat_id)                     # chat first, then the report
            still_due = conn.execute(
                """SELECT 1 FROM field.report WHERE report_id = %s AND status = 'draft'
                   AND last_activity_at <= %s FOR UPDATE SKIP LOCKED""",
                (report_id, now - timedelta(minutes=minutes)),
            ).fetchone()
            if not still_due:
                continue
            report = reports.autofile(conn, deps.core, report_id, now=now)
            flow_report.tell_autofiled(ctx, report)
            filed += 1
    return {"filed": filed}


def ack_remind(deps: WorkerDeps, now: datetime, *, dry_run: bool = False) -> dict[str, int]:
    """Remind once whoever has not acknowledged an advance or return within ack_remind_hours."""
    counts = {"reminded": 0, "not_delivered": 0}
    with transaction(deps.pool) as conn:
        due = [r for ac_id in settings_ac.all_ac_ids(conn)
               for r in reminders.advances_needing_reminder(conn, now, effective(deps, conn, ac_id).ledger, ac_id)]
    for r in due:
        log.info("remind %s (pending since %s)", r["txn_no"], r["created_at"])
        if dry_run:
            continue
        with transaction(deps.pool) as conn:
            txn = conn.execute("SELECT * FROM field.money_txn WHERE txn_id = %s FOR UPDATE", (r["txn_id"],)).fetchone()
            if txn["status"] != "pending_ack":
                continue
            result = flow_ack.request_ack(ctx_for(deps, conn, now), txn, reminder=True)   # through the queue
            ledger.record_reminder(conn, txn["txn_id"], now=now)    # once, even if it could not be delivered
            counts["reminded" if result == flow_ack.QUEUED else "not_delivered"] += 1
    return counts


def ack_flag(deps: WorkerDeps, now: datetime, *, dry_run: bool = False) -> dict[str, int]:
    """Flag ack_overdue on advances and returns still unacknowledged after ack_flag_hours."""
    counts = {"flagged": 0}
    with transaction(deps.pool) as conn:
        overdue = [r for ac_id in settings_ac.all_ac_ids(conn)
                   for r in reminders.advances_to_flag(conn, now, effective(deps, conn, ac_id).ledger, ac_id)]
    for r in overdue:
        log.info("flag %s ack_overdue", r["txn_no"])
        if dry_run:
            continue
        with transaction(deps.pool) as conn:
            if ledger.flag_ack_overdue(conn, r["txn_id"], now=now):
                counts["flagged"] += 1
    return counts


def urgent_escalate(deps: WorkerDeps, now: datetime) -> dict[str, int]:
    with transaction(deps.pool) as conn:
        return {"escalated": alerts.escalate(conn, now)}


def digest_daily(deps: WorkerDeps, now: datetime) -> dict[str, int]:
    with transaction(deps.pool) as conn:
        return {"queued": digest.send_daily(conn, deps.core, now)}


FOLLOWUP_FROM, FOLLOWUP_UNTIL = time(10, 0), time(20, 0)     # Asia/Kolkata: never at night


def followup_ask(deps: WorkerDeps, now: datetime) -> dict[str, int]:
    """Impact follow-ups due (LLD §7): ask once, remind once three days later, then leave them."""
    counts = {"asked": 0, "reminded": 0}
    if not (FOLLOWUP_FROM <= now.astimezone(IST).time() < FOLLOWUP_UNTIL):
        return counts
    with transaction(deps.pool) as conn:
        for ac_id in settings_ac.all_ac_ids(conn):
            for f in followup.due_to_ask(conn, ac_id, now):
                outbox.enqueue(conn, recipient=f["member_id"], kind="followup", body=outbox.body("followup"),
                               ac_id=ac_id, txn_id=f["txn_id"], now=now, run_after=now)
                followup.mark_asked(conn, f["txn_id"], now=now)
                counts["asked"] += 1
            for f in followup.due_to_remind(conn, ac_id, now):
                outbox.enqueue(conn, recipient=f["member_id"], kind="followup",
                               body=outbox.body("followup", reminder=True), ac_id=ac_id, txn_id=f["txn_id"], now=now,
                               run_after=now)
                followup.mark_reminded(conn, f["txn_id"], now=now)
                counts["reminded"] += 1
    return counts


def digest_weekly(deps: WorkerDeps, now: datetime) -> dict[str, int]:
    with transaction(deps.pool) as conn:
        return {"queued": weekly.send_weekly(conn, deps.core, now)}


def _idempotency_purge(deps: WorkerDeps, now: datetime) -> dict[str, int]:
    """Drop stored web responses older than 48 hours (pulse/api/idempotency.py)."""
    from pulse.api import idempotency

    with transaction(deps.pool) as conn:
        return {"purged": idempotency.purge(conn, now)}


def label_review(deps: WorkerDeps, now: datetime) -> dict[str, int]:
    """Weekly: list custom labels used three or more times for promotion (LLD §8)."""
    with transaction(deps.pool) as conn:
        return labels.review(conn, now)


def balance_nudge(deps: WorkerDeps, now: datetime) -> dict[str, int]:
    with transaction(deps.pool) as conn:
        return {"queued": nudge.send(conn, now)}


# name, interval in seconds, function
TIMED: tuple[tuple[str, int, Callable[[WorkerDeps, datetime], dict[str, int]]], ...] = (
    ("report_autofile", 60, report_autofile),
    ("urgent_escalate", 300, urgent_escalate),
    ("digest_daily", 300, digest_daily),
    ("digest_weekly", 300, digest_weekly),
    ("ack_remind", 3600, ack_remind),
    ("ack_flag", 3600, ack_flag),
    ("balance_nudge", 3600, balance_nudge),
    ("followup_ask", 900, followup_ask),
    ("chat_cleanup", 3600, lambda deps, now: maintenance.chat_cleanup(deps, now)),
    ("media_retention", 86400, lambda deps, now: maintenance.media_retention(deps, now)),
    ("label_review", 7 * 86400, label_review),
    ("idempotency_purge", 86400, lambda deps, now: _idempotency_purge(deps, now)),
)
