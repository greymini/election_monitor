"""Apply numbered SQL migrations in order, once each.

    python -m db.apply_migrations              # apply pending
    python -m db.apply_migrations --status     # list applied / pending
    python -m db.apply_migrations --seed       # apply, then load db/seed

Plain numbered .sql files, tracked in schema_migration. No sqitch dependency -
there are a dozen migrations and one deployment target.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

import psycopg
from psycopg import sql as pgsql

from common.config import get_settings
from common.logging_setup import get_logger

log = get_logger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

BOOTSTRAP = """
CREATE TABLE IF NOT EXISTS schema_migration (
    filename   TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    checksum   TEXT
);
"""


def checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def migration_files() -> list[Path]:
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


def applied(conn: psycopg.Connection) -> dict[str, str]:
    with conn.cursor() as cur:
        cur.execute("SELECT filename, checksum FROM schema_migration")
        return {r[0]: r[1] for r in cur.fetchall()}


def apply_all(conn: psycopg.Connection, dry_run: bool = False) -> int:
    with conn.cursor() as cur:
        cur.execute(BOOTSTRAP)
    conn.commit()

    done = applied(conn)
    count = 0
    for path in migration_files():
        name = path.name
        digest = checksum(path)
        if name in done:
            if done[name] and done[name] != digest:
                log.warning(
                    "%s already applied but its contents changed (%s -> %s). "
                    "Write a new migration instead of editing this one.",
                    name, done[name], digest,
                )
            continue
        if dry_run:
            log.info("PENDING %s", name)
            count += 1
            continue

        sql = path.read_text(encoding="utf-8")
        log.info("applying %s", name)
        with conn.cursor() as cur:
            cur.execute(sql)
            cur.execute(
                "INSERT INTO schema_migration (filename, checksum) VALUES (%s, %s)",
                (name, digest),
            )
        conn.commit()
        count += 1
    return count


def set_readonly_password(conn: psycopg.Connection) -> None:
    """0012 creates giridih_ro with a placeholder password; set the real one."""
    pwd = os.environ.get("READONLY_DB_PASSWORD", "").strip()
    if not pwd:
        log.warning("READONLY_DB_PASSWORD not set - chatbot read-only role keeps its placeholder password")
        return
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = 'giridih_ro'")
        if cur.fetchone() is None:
            return
        cur.execute(pgsql.SQL("ALTER ROLE giridih_ro PASSWORD {}").format(pgsql.Literal(pwd)))
    conn.commit()
    log.info("read-only role password set")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Apply database migrations")
    ap.add_argument("--status", action="store_true", help="list pending migrations without applying")
    ap.add_argument("--seed", action="store_true", help="load db/seed after migrating")
    args = ap.parse_args(argv)

    settings = get_settings()
    if not settings.database_url:
        log.error("DATABASE_URL is not set")
        return 2

    with psycopg.connect(settings.database_url) as conn:
        if args.status:
            done = applied(conn) if _table_exists(conn, "schema_migration") else {}
            for path in migration_files():
                mark = "applied" if path.name in done else "PENDING"
                print(f"{mark:8} {path.name}")
            return 0

        n = apply_all(conn)
        set_readonly_password(conn)
        log.info("%d migration(s) applied", n)

    if args.seed:
        from db.seed.load_seed import main as seed_main

        seed_main([])
    return 0


def _table_exists(conn: psycopg.Connection, name: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass(%s) IS NOT NULL", (f"public.{name}",))
        row = cur.fetchone()
        return bool(row and row[0])


if __name__ == "__main__":
    sys.exit(main())
