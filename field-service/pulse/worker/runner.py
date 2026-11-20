"""The worker loop: due timed jobs, then up to a batch of queue jobs; sleep briefly when idle.

Safe to run two copies at once. Queue jobs are claimed with FOR UPDATE SKIP LOCKED (see
pulse/worker/queue.py). A timed job runs under pg_try_advisory_xact_lock for its name and only if
its last field.job_run is older than its interval, so one tick runs it once however many workers.
Every run writes field.job_run.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import Any, Callable

from psycopg.types.json import Jsonb

from pulse.common.clock import utcnow
from pulse.worker import jobs, queue
from pulse.worker.jobs import WorkerDeps

log = logging.getLogger(__name__)

TIMED_LOCK_CLASS = 1348
BATCH = 10


def _record(conn, deps: WorkerDeps, name: str, started: datetime, status: str, detail: dict[str, Any] | None,
            job_id: int | None = None) -> None:
    conn.execute(
        """INSERT INTO field.job_run (job_name, worker_id, job_id, started_at, finished_at, status, detail)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        (name, deps.worker_id, job_id, started, utcnow(), status, Jsonb(detail) if detail is not None else None),
    )


def run_timed(deps: WorkerDeps, now: datetime | None = None,
              timed=jobs.TIMED) -> dict[str, Any]:
    now = now or utcnow()
    results: dict[str, Any] = {}
    for name, interval, fn in timed:
        with deps.pool.connection() as guard, guard.transaction():
            if not guard.execute("SELECT pg_try_advisory_xact_lock(%s, hashtext(%s)) AS ok",
                                 (TIMED_LOCK_CLASS, name)).fetchone()["ok"]:
                continue                                  # another worker is running it right now
            last = guard.execute("SELECT max(started_at) AS t FROM field.job_run WHERE job_name = %s AND job_id IS NULL",
                                 (name,)).fetchone()["t"]
            if last is not None and now - last < timedelta(seconds=interval):
                continue
            try:
                detail = fn(deps, now)
                status = "ok"
            except Exception as e:
                log.exception("timed job %s failed", name)
                detail, status = {"error": type(e).__name__}, "error"
            _record(guard, deps, name, now, status, detail)
            results[name] = detail
    return results


def run_queue(deps: WorkerDeps, now: datetime | None = None, *, batch: int = BATCH,
              handlers: dict[str, Callable] | None = None) -> list[tuple[int, str]]:
    handlers = handlers or jobs.QUEUE
    done: list[tuple[int, str]] = []
    for _ in range(batch):
        at = now or utcnow()
        with deps.pool.connection() as conn:
            job = queue.claim(conn, deps.worker_id, now=at)
        if job is None:
            break
        handler = handlers.get(job["kind"])
        try:
            if handler is None:
                raise LookupError(f"no handler for job kind {job['kind']!r}")
            result = handler(deps, job, at)
            with deps.pool.connection() as conn:
                queue.complete(conn, job["job_id"], now=at)
                with conn.transaction():
                    _record(conn, deps, job["kind"], at, "ok", {"result": result}, job["job_id"])
            done.append((job["job_id"], result))
        except queue.RetryAfter as e:
            with deps.pool.connection() as conn:
                queue.defer(conn, job, e.seconds, now=at)
                with conn.transaction():
                    _record(conn, deps, job["kind"], at, "retry", {"retry_after": e.seconds}, job["job_id"])
            done.append((job["job_id"], "deferred"))
        except Exception as e:
            log.warning("job %s (%s) attempt %s failed: %s", job["job_id"], job["kind"], job["attempts"], e)
            with deps.pool.connection() as conn:
                retry = queue.fail(conn, job, f"{type(e).__name__}: {e}", now=at)
                with conn.transaction():
                    _record(conn, deps, job["kind"], at, "retry" if retry else "error",
                            {"error": type(e).__name__, "attempt": job["attempts"]}, job["job_id"])
            if not retry and job["kind"] in jobs.ON_FINAL_FAILURE:
                jobs.ON_FINAL_FAILURE[job["kind"]](deps, job, str(e), at)
            done.append((job["job_id"], "retry" if retry else "failed"))
    return done


def run_once(deps: WorkerDeps, now: datetime | None = None) -> dict[str, Any]:
    return {"timed": run_timed(deps, now), "queue": run_queue(deps, now)}


def run_forever(deps: WorkerDeps, *, should_stop: Callable[[], bool] = lambda: False, idle_sleep: float = 2.0) -> None:
    log.info("worker %s started", deps.worker_id)
    while not should_stop():
        try:
            result = run_once(deps)
        except Exception:
            log.exception("worker loop error; continuing")
            result = {"queue": []}
        if not result["queue"]:
            time.sleep(idle_sleep)
