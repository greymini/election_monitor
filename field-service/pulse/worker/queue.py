"""The job queue on field.job (LLD §8: a Postgres table polled with FOR UPDATE SKIP LOCKED).

claim() marks the job running in the same transaction that selects it, so two workers never take
the same job. A running job whose lease has expired (its worker died) is claimed again. A failed
attempt is retried with exponential backoff until max_attempts; then the job is marked failed.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from pulse.common.clock import utcnow

LEASE = timedelta(minutes=10)
BACKOFF_BASE = timedelta(seconds=30)
BACKOFF_MAX = timedelta(minutes=30)


def enqueue(conn: psycopg.Connection, kind: str, payload: dict[str, Any], *, run_after: datetime | None = None,
            dedupe_key: str | None = None, max_attempts: int = 5) -> int | None:
    """Add a job. With a dedupe_key, a job already waiting under that key absorbs this one (returns None)."""
    row = conn.execute(
        """INSERT INTO field.job (kind, payload, run_after, dedupe_key, max_attempts)
           VALUES (%s, %s, %s, %s, %s)
           ON CONFLICT (dedupe_key) WHERE status = 'queued' AND dedupe_key IS NOT NULL DO NOTHING
           RETURNING job_id""",
        (kind, Jsonb(payload), run_after or utcnow(), dedupe_key, max_attempts),
    ).fetchone()
    return row["job_id"] if row else None


def claim(conn: psycopg.Connection, worker_id: str, *, now: datetime | None = None,
          kinds: tuple[str, ...] | None = None) -> dict[str, Any] | None:
    now = now or utcnow()
    with conn.transaction():
        return conn.execute(
            """UPDATE field.job SET status = 'running', locked_by = %(w)s, locked_at = %(now)s, attempts = attempts + 1
               WHERE job_id = (
                   SELECT job_id FROM field.job
                   WHERE ((status = 'queued' AND run_after <= %(now)s)
                          OR (status = 'running' AND locked_at < %(stale)s))
                     AND (%(kinds)s::text[] IS NULL OR kind = ANY (%(kinds)s::text[]))
                   ORDER BY run_after, job_id
                   FOR UPDATE SKIP LOCKED
                   LIMIT 1)
               RETURNING *""",
            {"w": worker_id, "now": now, "stale": now - LEASE, "kinds": list(kinds) if kinds else None},
        ).fetchone()


def complete(conn: psycopg.Connection, job_id: int, *, now: datetime | None = None) -> None:
    with conn.transaction():
        conn.execute("UPDATE field.job SET status = 'done', finished_at = %s, last_error = NULL WHERE job_id = %s",
                     (now or utcnow(), job_id))


class RetryAfter(Exception):
    """Raised by a job that must wait (Telegram 429 retry_after). Not counted as a failed attempt."""

    def __init__(self, seconds: float):
        super().__init__(f"retry after {seconds}s")
        self.seconds = seconds


def defer(conn: psycopg.Connection, job: dict[str, Any], seconds: float, *, now: datetime | None = None) -> None:
    now = now or utcnow()
    with conn.transaction():
        if job.get("dedupe_key") and conn.execute(
                "SELECT 1 FROM field.job WHERE dedupe_key = %s AND status = 'queued' AND job_id <> %s",
                (job["dedupe_key"], job["job_id"])).fetchone():
            # a newer job for the same thing is already waiting; it will do this one's work
            conn.execute("UPDATE field.job SET status = 'done', finished_at = %s, last_error = 'superseded' "
                         "WHERE job_id = %s", (now, job["job_id"]))
            return
        conn.execute(
            """UPDATE field.job SET status = 'queued', run_after = %s, attempts = GREATEST(attempts - 1, 0),
                   locked_by = NULL, locked_at = NULL, last_error = %s WHERE job_id = %s""",
            (now + timedelta(seconds=seconds), f"deferred {seconds}s (rate limit)", job["job_id"]),
        )


def backoff(attempts: int) -> timedelta:
    return min(BACKOFF_BASE * (2 ** max(attempts - 1, 0)), BACKOFF_MAX)


def fail(conn: psycopg.Connection, job: dict[str, Any], error: str, *, now: datetime | None = None) -> bool:
    """Record a failed attempt. Returns True if the job will be retried, False if it is now failed."""
    now = now or utcnow()
    retry = job["attempts"] < job["max_attempts"]
    with conn.transaction():
        if retry and job.get("dedupe_key") and conn.execute(
                "SELECT 1 FROM field.job WHERE dedupe_key = %s AND status = 'queued' AND job_id <> %s",
                (job["dedupe_key"], job["job_id"])).fetchone():
            conn.execute("UPDATE field.job SET status = 'done', finished_at = %s, last_error = %s WHERE job_id = %s",
                         (now, ("superseded after: " + error)[:2000], job["job_id"]))
            return True                                      # the waiting job carries on
        if retry:
            conn.execute(
                """UPDATE field.job SET status = 'queued', run_after = %s, last_error = %s, locked_by = NULL,
                       locked_at = NULL WHERE job_id = %s""",
                (now + backoff(job["attempts"]), error[:2000], job["job_id"]),
            )
        else:
            conn.execute(
                "UPDATE field.job SET status = 'failed', finished_at = %s, last_error = %s WHERE job_id = %s",
                (now, error[:2000], job["job_id"]),
            )
    return retry
