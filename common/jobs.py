"""job_run bookkeeping (LLD 1: 'jobs write status to a job_run table').

Every scheduled or manual job wraps itself in job_context so the admin status
page and the nightly ops report have one place to look.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import logging
import traceback
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from common.db import query_one
from common.logging_setup import get_logger

log = get_logger(__name__)


@dataclass
class JobHandle:
    run_id: int | None
    job: str
    meta: dict[str, Any] = field(default_factory=dict)
    _buffer: io.StringIO = field(default_factory=io.StringIO)

    def log_line(self, msg: str) -> None:
        log.info("[%s] %s", self.job, msg)
        self._buffer.write(msg.rstrip() + "\n")

    def set(self, **kwargs: Any) -> None:
        self.meta.update(kwargs)

    @property
    def text(self) -> str:
        return self._buffer.getvalue()[-20000:]


@contextlib.contextmanager
def job_context(job: str, **meta: Any) -> Iterator[JobHandle]:
    row = query_one(
        "INSERT INTO job_run (job, status, meta) VALUES (%s, 'running', %s) RETURNING id",
        (job, json.dumps(meta)),
    )
    handle = JobHandle(run_id=row["id"] if row else None, job=job, meta=dict(meta))
    handler = _AttachLogHandler(handle)
    logging.getLogger().addHandler(handler)
    try:
        yield handle
    except Exception as exc:
        handle.log_line(f"FAILED: {exc}")
        handle.log_line(traceback.format_exc())
        _finish(handle, "failed")
        raise
    else:
        _finish(handle, "ok")
    finally:
        logging.getLogger().removeHandler(handler)


class _AttachLogHandler(logging.Handler):
    """Mirror WARNING+ records into the job log so failures are visible in the UI."""

    def __init__(self, handle: JobHandle) -> None:
        super().__init__(level=logging.WARNING)
        self._handle = handle

    def emit(self, record: logging.LogRecord) -> None:
        with contextlib.suppress(Exception):
            self._handle._buffer.write(self.format(record) + "\n")


def _finish(handle: JobHandle, status: str) -> None:
    if handle.run_id is None:
        return
    with contextlib.suppress(Exception):
        query_one(
            "UPDATE job_run SET finished = now(), status = %s, log = %s, meta = %s "
            "WHERE id = %s RETURNING id",
            (status, handle.text, json.dumps(handle.meta, default=str), handle.run_id),
        )


def already_done(job: str, key: str) -> bool:
    """Idempotency check (LLD 7): has this job already completed for this key?"""
    row = query_one(
        "SELECT 1 AS hit FROM job_run "
        "WHERE job = %s AND status = 'ok' AND meta ->> 'idempotency_key' = %s LIMIT 1",
        (job, key),
    )
    return row is not None


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
