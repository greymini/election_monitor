"""Refresh the materialized views after any load (LLD 3, 7).

Order matters - mv_swing and mv_result_booth_wide read mv_result_booth_party,
mv_booth_priority reads three others - so they are refreshed in dependency
order. CONCURRENTLY is used where a unique index exists (all of them), so the
dashboard keeps serving during a refresh.

    python -m analytics.refresh
    python -m analytics.refresh --view mv_swing
"""

from __future__ import annotations

import argparse
import sys
import time

from common.logging_setup import get_logger

log = get_logger(__name__)

# Dependency order. Do not reorder without checking db/migrations/0009-0011.
VIEWS = [
    "mv_result_booth_party",
    "mv_booth_party_share",
    "mv_result_booth_wide",
    "mv_swing",
    "mv_transfer_ls_vs",
    "mv_volatility",
    "mv_floating_vote",
    "mv_new_voter_share",
    "mv_booth_priority",
    "mv_area_rollup",
]


def refresh_view(conn, view: str, concurrently: bool = True) -> float:
    started = time.perf_counter()
    with conn.cursor() as cur:
        mode = "CONCURRENTLY " if concurrently else ""
        try:
            cur.execute(f"REFRESH MATERIALIZED VIEW {mode}{view}")
        except Exception as exc:
            # A view that has never been populated cannot be refreshed
            # concurrently; fall back to a blocking refresh once.
            conn.rollback()
            if not concurrently:
                raise
            log.info("%s: concurrent refresh unavailable (%s) - doing a blocking refresh",
                     view, str(exc).splitlines()[0][:120])
            with conn.cursor() as cur2:
                cur2.execute(f"REFRESH MATERIALIZED VIEW {view}")
    conn.commit()
    return time.perf_counter() - started


def refresh_all(views: list[str] | None = None, concurrently: bool = True) -> dict[str, float]:
    from common.db import connection

    timings: dict[str, float] = {}
    targets = views or VIEWS
    with connection() as conn:
        conn.autocommit = False
        for view in targets:
            timings[view] = refresh_view(conn, view, concurrently)
            log.info("refreshed %-24s %.2fs", view, timings[view])
    return timings


def main(argv: list[str] | None = None) -> int:
    from common.jobs import job_context

    ap = argparse.ArgumentParser(description="Refresh analytics materialized views")
    ap.add_argument("--view", action="append", help="refresh only this view (repeatable)")
    ap.add_argument("--blocking", action="store_true", help="do not use CONCURRENTLY")
    args = ap.parse_args(argv)

    with job_context("analytics.refresh") as job:
        timings = refresh_all(args.view, concurrently=not args.blocking)
        job.set(**{k: round(v, 3) for k, v in timings.items()})
        job.log_line(f"refreshed {len(timings)} view(s) in {sum(timings.values()):.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
