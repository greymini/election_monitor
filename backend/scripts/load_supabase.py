#!/usr/bin/env python3
"""Load real + modelled data into a remote Supabase database (see plan step 3).

    cd backend && python scripts/load_supabase.py

Requires DATABASE_URL (session pooler, sslmode=require) and repo data paths.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent


def log(msg: str) -> None:
    print(f"[load_supabase] {msg}", flush=True)


def fail(msg: str) -> None:
    print(f"[load_supabase] ERROR {msg}", file=sys.stderr, flush=True)
    raise SystemExit(1)


def run(cmd: list[str], what: str) -> None:
    log("$ " + " ".join(cmd[1:]))
    proc = subprocess.run(
        cmd,
        cwd=APP_ROOT,
        env=dict(os.environ),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stdout or "")
        sys.stderr.write(proc.stderr or "")
        fail(f"{what} failed (exit {proc.returncode})")
    for line in (proc.stdout or "").splitlines()[-5:]:
        if line.strip():
            log(f"  {line}")


def copy_local_news(py: str) -> None:
    """Copy news_item from local .devstack if reachable."""
    import psycopg

    local_url = os.environ.get("LOCAL_NEWS_DATABASE_URL", "")
    if not local_url:
        pgdata = APP_ROOT / ".devstack" / "pgdata"
        if (pgdata / ".s.PGSQL.5432").exists():
            local_url = f"postgresql://postgres:@/postgres?host={pgdata}"
        else:
            log("LOCAL_NEWS_DATABASE_URL not set and no local devstack; skip news copy")
            return
    remote = os.environ["DATABASE_URL"]
    with psycopg.connect(local_url) as src, psycopg.connect(remote) as dst:
        with src.cursor() as sc, dst.cursor() as dc:
            sc.execute(
                "SELECT to_regclass('public.news_item') IS NOT NULL AS has_news_item"
            )
            if not sc.fetchone()[0]:
                log("local devstack has no news_item table; skip news copy")
                return
            sc.execute(
                "SELECT url, title, summary, published, source, feed, scope, relevance, "
                "matched_terms, label_method, ac_ids, labelled_at FROM news_item"
            )
            rows = sc.fetchall()
            for row in rows:
                dc.execute(
                    "INSERT INTO news_item (url, title, summary, published, source, feed, scope, "
                    "relevance, matched_terms, label_method, ac_ids, labelled_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (url) DO NOTHING",
                    row,
                )
        dst.commit()
    log(f"copied up to {len(rows)} news rows from local devstack")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Load Supabase election_monitor dataset")
    ap.add_argument("--ac", type=int, default=32)
    ap.add_argument("--skip-seed", action="store_true")
    ap.add_argument("--skip-news-crawl", action="store_true")
    ap.add_argument("--skip-overlay", action="store_true")
    args = ap.parse_args(argv)

    db_url = (os.environ.get("DATABASE_URL") or "").strip()
    if not db_url:
        fail("DATABASE_URL is not set (copy .env.supabase.example → .env at repo root)")
    if "[dev_stack]" in db_url or db_url.startswith("["):
        fail("DATABASE_URL looks like a placeholder; set a real postgres URL in .env")
    os.environ["DATABASE_URL"] = db_url

    py = sys.executable
    if not args.skip_seed:
        run([py, "-m", "db.seed.load_seed"], "seed")
    else:
        run([py, "-m", "db.seed.load_seed", "--only", "gp_officials"], "gp officials")
    folder = APP_ROOT / "db" / "seed" / "form20"
    for label, name, anchor in [
        ("VS-2024", "giridih_vs2024_form20.xlsx", True),
        ("VS-2019", "giridih_vs2019_form20.xlsx", False),
    ]:
        argv_f = [py, "-m", "ingest.load_form20_tables", str(folder / name),
                  "--ac", str(args.ac), "--election", label]
        if anchor:
            argv_f.append("--create-booths")
        run(argv_f, f"form20 {label}")

    gazette = REPO_ROOT / "data_giridih" / "gazette_results_ac32.csv"
    if gazette.is_file():
        run([py, "-m", "ingest.fetch_sec", "--load-csv", str(gazette),
             "--election", "PANCHAYAT-2022", "--ac", str(args.ac)], "gazette")

    holders = REPO_ROOT / "data_giridih" / "panchayat_members_load.csv"
    if holders.is_file():
        run([py, "-m", "ingest.load_office_holders", "--csv", str(holders),
             "--ac", str(args.ac)], "office holders")

    if not args.skip_news_crawl:
        run([py, "-m", "news.crawl_rss", "--limit", "1500"], "news crawl")
        run([py, "-m", "news.label_rules"], "news label")
    copy_local_news(py)

    if not args.skip_overlay:
        run([py, "-m", "ingest.modelled_overlay", "--ac", str(args.ac)], "modelled overlay")

    run([py, "-m", "analytics.refresh", "--blocking"], "refresh mviews")
    run([py, "-m", "scripts.ensure_single_user"], "bootstrap single user")
    log("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
