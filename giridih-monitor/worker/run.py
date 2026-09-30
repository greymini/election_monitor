"""Run any scheduled job by hand (LLD 7):

    python -m worker.run news.crawl
    python -m worker.run --list
"""

from __future__ import annotations

import argparse
import json
import sys

from common.logging_setup import get_logger, setup_logging
from worker.jobs import REGISTRY, run_job

log = get_logger(__name__)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run a scheduled job once")
    ap.add_argument("job", nargs="?", help="job name")
    ap.add_argument("--list", action="store_true", help="list known jobs")
    args = ap.parse_args(argv)

    setup_logging()
    if args.list or not args.job:
        for name in sorted(REGISTRY):
            print(name)
        return 0
    if args.job not in REGISTRY:
        log.error("unknown job %r. Known jobs: %s", args.job, ", ".join(sorted(REGISTRY)))
        return 2

    result = run_job(args.job)
    print(json.dumps(result, indent=2, default=str))
    return 1 if "error" in result else 0


if __name__ == "__main__":
    sys.exit(main())
