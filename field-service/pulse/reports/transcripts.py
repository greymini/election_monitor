"""Transcripts of report voice notes (LLD §6): queue, apply, fail, human correction.

Filing and alerts never wait for a transcript. When one arrives it is PII-masked before it is stored,
it may *suggest* a category and urgency for a report still unsorted (never changing either), and every
open alert card about the report is redrawn with its first 300 characters, labelled as machine
written. A human correction is kept apart from the machine text and preferred for display.

field.report_transcript has one row per (report, voice note); field.report.transcript_hi holds the
done ones joined in order, transcript_conf the lowest confidence among them.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import psycopg

from pulse.asr.base import Result
from pulse.common import pii
from pulse.reports import access, classify
from pulse.reports import service as reports
from pulse.reports import title
from pulse.worker import queue

JOB = "transcribe"
MAX_ATTEMPTS = 3
CARD_CHARS = 300
HUMAN_MAX = 10_000
AUDIO_KINDS = ("voice",)                 # media_asset.kind: Telegram voice notes and audio files


class Denied(reports.ReportError):
    pass


def linked_reports(conn: psycopg.Connection, asset_id: UUID) -> list[UUID]:
    return [r["report_id"] for r in conn.execute(
        "SELECT report_id FROM field.report_media WHERE asset_id = %s AND role = 'report'", (asset_id,))]


def queue_for_asset(conn: psycopg.Connection, asset_id: UUID, *, now: datetime) -> bool:
    """After a voice note is stored (engine configured): one job per asset. Reports it belongs to are
    marked queued now; the job finds them again when it runs."""
    asset = conn.execute("SELECT kind, status FROM field.media_asset WHERE asset_id = %s", (asset_id,)).fetchone()
    if asset is None or asset["kind"] not in AUDIO_KINDS or asset["status"] != "stored":
        return False
    for rid in linked_reports(conn, asset_id):
        _row_queued(conn, rid, asset_id, now)
    queue.enqueue(conn, JOB, {"asset_id": str(asset_id)}, run_after=now, dedupe_key=f"{JOB}:{asset_id}",
                  max_attempts=MAX_ATTEMPTS)
    return True


def _row_queued(conn, report_id: UUID, asset_id: UUID, now: datetime) -> None:
    conn.execute(
        """INSERT INTO field.report_transcript (report_id, asset_id, status, created_at) VALUES (%s, %s, 'queued', %s)
           ON CONFLICT (report_id, asset_id) DO NOTHING""", (report_id, asset_id, now))
    _recompute(conn, report_id)


def _recompute(conn, report_id: UUID) -> None:
    """report.transcript_* from its rows: done texts joined in report_media order."""
    rows = conn.execute(
        """SELECT t.* FROM field.report_transcript t
           JOIN field.report_media m ON m.report_id = t.report_id AND m.asset_id = t.asset_id
           WHERE t.report_id = %s ORDER BY m.seq""", (report_id,)).fetchall()
    done = [r for r in rows if r["status"] == "done" and r["text"]]
    if any(r["status"] == "queued" for r in rows):
        status = "queued"
    elif done:
        status = "done"
    elif rows:
        status = "failed"
    else:
        status = "none"
    confs = [r["confidence"] for r in done if r["confidence"] is not None]
    conn.execute(
        """UPDATE field.report SET transcript_status = %s, transcript_hi = %s, transcript_engine = %s,
               transcript_conf = %s WHERE report_id = %s""",
        (status, "\n".join(r["text"] for r in done) or None, done[-1]["engine"] if done else None,
         min(confs) if confs else None, report_id))
    title.refresh(conn, report_id)


def apply(conn: psycopg.Connection, report_id: UUID, asset_id: UUID, result: Result, *, converted: bool,
          now: datetime) -> dict[str, Any]:
    """Store a machine transcript for one voice note of a report, then suggest and redraw cards."""
    text = (result.text or "").strip()
    masked = pii.mask(text) if text else None
    conn.execute(
        """INSERT INTO field.report_transcript (report_id, asset_id, status, text, confidence, engine, converted,
               created_at, done_at)
           VALUES (%s, %s, 'done', %s, %s, %s, %s, %s, %s)
           ON CONFLICT (report_id, asset_id) DO UPDATE SET status = 'done', text = EXCLUDED.text,
               confidence = EXCLUDED.confidence, engine = EXCLUDED.engine, converted = EXCLUDED.converted,
               error = NULL, done_at = EXCLUDED.done_at""",
        (report_id, asset_id, masked, result.confidence, result.engine, converted, now, now))
    _recompute(conn, report_id)
    reports.event(conn, report_id, "transcribed", actor=None, now=now,
                  data={"asset_id": str(asset_id), "engine": result.engine, "converted": converted,
                        "confidence": str(result.confidence) if result.confidence is not None else None,
                        "masked": masked != text, "chars": len(masked or "")})
    _suggest(conn, report_id, now)
    report = reports.get(conn, report_id)
    if report["status"] != "draft":
        from pulse.notify import outbox
        outbox.refresh_report_cards(conn, report_id)
    return report


def fail(conn: psycopg.Connection, report_id: UUID, asset_id: UUID, error: str, *, now: datetime) -> None:
    """The engine gave up: the report keeps everything else; its audio stays playable."""
    conn.execute(
        """INSERT INTO field.report_transcript (report_id, asset_id, status, error, created_at, done_at)
           VALUES (%s, %s, 'failed', %s, %s, %s)
           ON CONFLICT (report_id, asset_id) DO UPDATE SET status = 'failed', error = EXCLUDED.error,
               done_at = EXCLUDED.done_at""",
        (report_id, asset_id, error[:500], now, now))
    _recompute(conn, report_id)
    reports.event(conn, report_id, "transcript_failed", actor=None, now=now, data={"asset_id": str(asset_id)})


def _person_chose(conn, report_id: UUID, kind: str) -> bool:
    """A person set this (category_set by a member, or urgency_set with source user)."""
    if kind == "category":
        q = "SELECT 1 FROM field.report_event WHERE report_id = %s AND kind = 'category_set' AND actor_member_id IS NOT NULL"
    else:
        q = "SELECT 1 FROM field.report_event WHERE report_id = %s AND kind = 'urgency_set' AND data->>'source' = 'user'"
    return conn.execute(q, (report_id,)).fetchone() is not None


def _suggest(conn, report_id: UUID, now: datetime) -> None:
    """Keyword rules on the transcript, for a report still unsorted. Stored as a labelled suggestion
    (suggested_*); the category and urgency themselves are never changed here."""
    r = reports.get(conn, report_id, lock=True)
    text = r["transcript_hi"]
    if not text:
        return
    cat = None
    if r["category_id"] is None and not _person_chose(conn, report_id, "category"):
        s = classify.suggest_category(conn, text)
        cat = s.bucket_id if s is not None else None
    urgent = (r["urgency"] != "urgent" and not _person_chose(conn, report_id, "urgency")
              and classify.urgency_hint(conn, text))
    if cat is None and not urgent:
        return
    conn.execute(
        """UPDATE field.report SET suggested_category_id = COALESCE(%s, suggested_category_id),
               suggested_urgency = CASE WHEN %s THEN 'urgent' ELSE suggested_urgency END,
               suggested_source = 'transcript_rule' WHERE report_id = %s""", (cat, urgent, report_id))
    reports.event(conn, report_id, "suggested", actor=None, now=now,
                  data={"source": "transcript_rule", "category_id": cat, "urgency": "urgent" if urgent else None})


# ---- human correction -------------------------------------------------------------------------

def may_correct(v: access.Viewer, report: dict[str, Any]) -> bool:
    return report["ac_id"] == v.ac_id and (v.sees_all or report["assigned_to"] == v.member_id)


def correct(conn: psycopg.Connection, v: access.Viewer, report_id: UUID, text: str, *, now: datetime) -> dict[str, Any]:
    report = reports.get(conn, report_id, lock=True)
    if report is None or not access.can_see(conn, v, report_id):
        raise reports.NotFound("report not found")
    if not may_correct(v, report):
        raise Denied("correct transcript")
    text = (text or "").strip()
    if not text:
        raise reports.ReportError("transcript text is empty")
    masked = pii.mask(text[:HUMAN_MAX])
    row = conn.execute(
        """UPDATE field.report SET transcript_human = %s, transcript_human_by = %s, transcript_human_at = %s
           WHERE report_id = %s RETURNING *""", (masked, v.member_id, now, report_id)).fetchone()
    reports.event(conn, report_id, "transcript_corrected", actor=v.member_id, now=now,
                  data={"masked": masked != text[:HUMAN_MAX], "chars": len(masked)})
    title.refresh(conn, report_id)
    row = reports.get(conn, report_id)
    if row["status"] != "draft":
        from pulse.notify import outbox
        outbox.refresh_report_cards(conn, report_id)
    return row


@dataclass(frozen=True)
class Display:
    text: str | None
    source: str | None               # human | machine | None
    engine: str | None
    confidence: Any


def display(report: dict[str, Any]) -> Display:
    """What to show: the human correction when there is one, else the machine text."""
    if report.get("transcript_human"):
        return Display(report["transcript_human"], "human", None, None)
    if report.get("transcript_hi"):
        return Display(report["transcript_hi"], "machine", report["transcript_engine"], report["transcript_conf"])
    return Display(None, None, None, None)
