"""APScheduler process (LLD 0, 7).

One container, ~10 scheduled functions, no orchestrator. An Airflow or Prefect
deployment would use more RAM than the rest of the application put together,
and there is nothing here that needs a DAG.

Schedule (Asia/Kolkata):

    news.crawl                every 4 hours
    news.label_batch          01:00
    news.label_collect        07:00  (then news.embed)
    ceo.check_new_supplement  06:00
    analytics.refresh         07:30
    analytics.caste_estimate  07:45
    ops.backup                03:00
    ops.usage_report          08:00
    ops.purge_prompts         03:30

Jobs are idempotent and each writes a job_run row; the admin status page reads
that table. Misfires inside an hour still run (`misfire_grace_time`), so a
restart does not silently skip the night's work.
"""

from __future__ import annotations

import signal
import sys
import time

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from common.config import get_settings
from common.logging_setup import get_logger, setup_logging
from worker.jobs import run_job

log = get_logger(__name__)

SCHEDULE = [
    # (job name, cron kwargs)
    ("news.crawl", {"hour": "*/4", "minute": 0}),
    ("news.label_batch", {"hour": 1, "minute": 0}),
    ("news.label_collect", {"hour": 7, "minute": 0}),
    ("news.embed", {"hour": 7, "minute": 15}),
    ("ceo.check_new_supplement", {"hour": 6, "minute": 0}),
    ("analytics.refresh", {"hour": 7, "minute": 30}),
    ("analytics.caste_estimate", {"hour": 7, "minute": 45}),
    ("ops.backup", {"hour": 3, "minute": 0}),
    ("ops.purge_prompts", {"hour": 3, "minute": 30}),
    ("ops.usage_report", {"hour": 8, "minute": 0}),
]

MISFIRE_GRACE_SECONDS = 3600


def build_scheduler() -> BackgroundScheduler:
    settings = get_settings()
    scheduler = BackgroundScheduler(
        timezone=settings.tz,
        job_defaults={
            "coalesce": True,          # one catch-up run, not a burst
            "max_instances": 1,        # never run the same job twice at once
            "misfire_grace_time": MISFIRE_GRACE_SECONDS,
        },
    )
    for name, cron in SCHEDULE:
        scheduler.add_job(
            run_job, trigger=CronTrigger(**cron, timezone=settings.tz),
            args=[name], id=name, name=name, replace_existing=True,
        )
        log.info("scheduled %-26s %s", name, cron)
    return scheduler


def main() -> int:
    setup_logging()
    settings = get_settings()
    log.info("worker starting (timezone %s)", settings.tz)

    scheduler = build_scheduler()
    scheduler.start()

    stopping = False

    def _shutdown(signum, _frame):
        nonlocal stopping
        log.info("signal %s received - finishing the running job, then exiting", signum)
        stopping = True

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    try:
        while not stopping:
            time.sleep(1)
    finally:
        scheduler.shutdown(wait=True)
        log.info("worker stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
