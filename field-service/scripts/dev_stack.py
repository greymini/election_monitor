"""Local stack on embedded PostgreSQL (pgserver), no Docker.

    python -m scripts.dev_stack up [--seed-team]   start (or reuse) the server, create DB, migrate
    python -m scripts.dev_stack status
    python -m scripts.dev_stack down

Data lives in .pgdata/ (git-ignored). The server keeps running after `up` returns. Its port can
change between restarts, so `up` rewrites the DATABASE_URL line in .env when .env exists.
"""
from __future__ import annotations

import argparse
import re
import sys

import pgserver
import psycopg
from pgserver.utils import PostmasterInfo

from pulse.config import REPO_ROOT
from pulse.db import connect
from pulse.migrate import apply_migrations
from pulse.seed import apply_seeds

PGDATA = REPO_ROOT / ".pgdata"
ENV_FILE = REPO_ROOT / ".env"
DB_NAME = "pulse"


def _write_env_url(url: str) -> bool:
    if not ENV_FILE.exists():
        return False
    text = ENV_FILE.read_text(encoding="utf-8")
    line = f"DATABASE_URL={url}"
    if re.search(r"^DATABASE_URL=.*$", text, flags=re.M):
        text = re.sub(r"^DATABASE_URL=.*$", lambda _: line, text, count=1, flags=re.M)
    else:
        text = text.rstrip("\n") + "\n" + line + "\n"
    ENV_FILE.write_text(text, encoding="utf-8")
    return True


def up(seed_team: bool) -> int:
    server = pgserver.get_server(PGDATA, cleanup_mode=None)  # None: leave it running when we exit
    with psycopg.connect(server.get_uri(), autocommit=True) as admin:
        if not admin.execute("SELECT 1 FROM pg_database WHERE datname = %s", (DB_NAME,)).fetchone():
            admin.execute(f'CREATE DATABASE "{DB_NAME}"')
    url = server.get_uri(DB_NAME)
    with connect(url) as conn:
        done = apply_migrations(conn)
        print("Migrations applied: " + ", ".join(done) if done else "Migrations up to date.")
        apply_seeds(conn)
        print("Reference seeds applied (buckets, report categories, keywords).")
        if seed_team:
            from pulse.core_adapter.stub import StubCoreAdapter
            from pulse.team.dev_seed import seed_team as _seed

            with conn.transaction():
                ids = _seed(conn, StubCoreAdapter())
            print(f"Dev team seeded: {len(ids)} members.")
    print(f"PostgreSQL running. DATABASE_URL={url}")
    if _write_env_url(url):
        print("Updated DATABASE_URL in .env")
    else:
        print("No .env yet: copy .env.example to .env and set DATABASE_URL to the value above.")
    return 0


def status() -> int:
    info = PostmasterInfo.read_from_pgdata(PGDATA) if PGDATA.exists() else None
    if info is None or not info.is_running():
        print("Not running.")
        return 1
    print(f"Running (pid {info.pid}).")
    return 0


def down() -> int:
    info = PostmasterInfo.read_from_pgdata(PGDATA) if PGDATA.exists() else None
    if info is None or not info.is_running():
        print("Not running.")
        return 0
    from pgserver._commands import pg_ctl

    pg_ctl(["-w", "stop"], pgdata=PGDATA)
    print("Stopped.")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="dev_stack")
    sub = p.add_subparsers(dest="cmd", required=True)
    u = sub.add_parser("up")
    u.add_argument("--seed-team", action="store_true", help="create the dev team tree from db/seeds/dev_team.json")
    sub.add_parser("status")
    sub.add_parser("down")
    args = p.parse_args(argv)
    if args.cmd == "up":
        return up(args.seed_team)
    if args.cmd == "status":
        return status()
    return down()


if __name__ == "__main__":
    sys.exit(main())
