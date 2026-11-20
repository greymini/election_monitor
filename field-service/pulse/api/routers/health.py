"""GET /health: is the service able to work? 503 when the database cannot be reached.

Reports the age of the last worker run and the queue depth so a monitor can tell "the API is up but
nothing is being sent". No secrets, no row data. GET /healthz stays as a plain liveness check.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from pulse.api.deps import get_state

router = APIRouter()

WORKER_STALE_SECONDS = 300


@router.get("/health")
def health(state=Depends(get_state)):
    try:
        with state.pool.connection(timeout=3) as conn, conn.transaction():
            conn.execute("SET LOCAL statement_timeout = 2000")
            age = conn.execute(
                "SELECT EXTRACT(EPOCH FROM now() - max(finished_at))::int AS s FROM field.job_run").fetchone()["s"]
            q = conn.execute(
                """SELECT count(*) FILTER (WHERE status = 'queued' AND run_after <= now()) AS ready,
                          count(*) FILTER (WHERE status = 'queued' AND run_after > now()) AS waiting,
                          count(*) FILTER (WHERE status = 'running') AS running,
                          count(*) FILTER (WHERE status = 'failed' AND finished_at > now() - interval '24 hours')
                              AS failed_24h
                   FROM field.job""").fetchone()
    except Exception:
        return JSONResponse({"status": "down", "database": "unreachable"}, status_code=503)
    worker_ok = age is not None and age <= WORKER_STALE_SECONDS
    return {"status": "ok" if worker_ok else "degraded", "database": "ok",
            "worker_last_run_seconds": age, "queue": dict(q)}
