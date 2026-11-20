"""Run the acknowledgement jobs once: remind receivers after 24 h, flag ack_overdue after 48 h.

    python -m scripts.run_jobs            # send reminders and write flags
    python -m scripts.run_jobs --dry-run  # only list what would happen

The worker (python -m scripts.run_worker) runs the same jobs hourly; this is for one-off runs.
"""
from __future__ import annotations

import argparse
import logging
import sys

from pulse.api.main import make_core_adapter
from pulse.bot.client import HttpxTelegramClient
from pulse.common.clock import utcnow
from pulse.common.storage import LocalDiskStorage
from pulse.config import ConfigError, ReportLimits, load_settings
from pulse.db import make_pool
from pulse.worker import jobs


def run(pool, client, core, limits, *, dry_run: bool, now=None, storage=None) -> dict[str, int]:
    deps = jobs.WorkerDeps(pool=pool, client=client, core=core, storage=storage, ledger_limits=limits,
                           report_limits=ReportLimits(), worker_id="run_jobs")
    now = now or utcnow()
    return {**jobs.ack_remind(deps, now, dry_run=dry_run), **jobs.ack_flag(deps, now, dry_run=dry_run)}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="run_jobs")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)   # request URLs contain the bot token
    try:
        settings = load_settings()
    except ConfigError as e:
        print(e, file=sys.stderr)
        return 2
    pool = make_pool(settings.database_url, max_size=2)
    client = HttpxTelegramClient(settings.telegram_bot_token)
    try:
        counts = run(pool, client, make_core_adapter(settings), settings.ledger, dry_run=args.dry_run,
                     storage=LocalDiskStorage(settings.media_dir))
    finally:
        client.close()
        pool.close()
    print(counts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
