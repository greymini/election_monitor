"""Run the worker: media downloads, card refreshes, report auto-file, acknowledgement reminders.

    python -m scripts.run_worker

Safe to run more than one copy. Needs the bot token (downloads and notices go through Telegram).
"""
from __future__ import annotations

import logging
import os
import socket
import sys

from pulse.api.main import make_core_adapter
from pulse.bot.client import HttpxTelegramClient
from pulse.common.storage import LocalDiskStorage
from pulse.config import ConfigError, load_settings
from pulse.db import make_pool
from pulse.notify.sender import Pace
from pulse.worker.jobs import WorkerDeps
from pulse.worker.runner import run_forever


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)   # request URLs contain the bot token
    try:
        settings = load_settings()
    except ConfigError as e:
        print(e, file=sys.stderr)
        return 2
    pool = make_pool(settings.database_url, min_size=2, max_size=4)
    client = HttpxTelegramClient(settings.telegram_bot_token)
    deps = WorkerDeps(pool=pool, client=client, core=make_core_adapter(settings),
                      storage=LocalDiskStorage(settings.media_dir), ledger_limits=settings.ledger,
                      report_limits=settings.reports, worker_id=f"{socket.gethostname()}:{os.getpid()}",
                      pace=Pace(25), retention=settings.retention,
                      asr_engine=settings.asr_engine)
    print(f"Worker {deps.worker_id} running; media in {settings.media_dir}. Ctrl+C to stop.")
    try:
        run_forever(deps)
    except KeyboardInterrupt:
        pass
    finally:
        client.close()
        pool.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
