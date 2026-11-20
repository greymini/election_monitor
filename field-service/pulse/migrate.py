"""Migration runner for the `field` schema.

Applies `db/migrations/field_NNNN_*.sql` in order, each in its own transaction, and records them in
`field.schema_migration`. Refuses to run if an already-applied file has changed. The CLI then
applies the idempotent reference seeds (pulse/seed.py).

    python -m pulse.migrate
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

import psycopg

from pulse.config import REPO_ROOT, ConfigError, load_database_url
from pulse.db import connect

MIGRATIONS_DIR = REPO_ROOT / "db" / "migrations"
_NAME_RE = re.compile(r"^field_(\d{4})_[a-z0-9_]+\.sql$")
_LOCK_KEY = 0x5055_4C53  # advisory lock id, "PULS"


class MigrationError(RuntimeError):
    pass


def discover(directory: Path = MIGRATIONS_DIR) -> list[Path]:
    files = sorted(p for p in directory.glob("*.sql"))
    bad = [p.name for p in files if not _NAME_RE.match(p.name)]
    if bad:
        raise MigrationError(f"Unexpected migration file names: {bad}")
    numbers = [int(_NAME_RE.match(p.name).group(1)) for p in files]
    if numbers != list(range(1, len(numbers) + 1)):
        raise MigrationError(f"Migration numbers must run 0001, 0002, ... without gaps: {numbers}")
    return files


def digest(path: Path) -> str:
    # hash with LF line endings so a CRLF checkout does not look like an edited migration
    return hashlib.sha256(path.read_text(encoding="utf-8").replace("\r\n", "\n").encode("utf-8")).hexdigest()


def status(conn: psycopg.Connection, directory: Path = MIGRATIONS_DIR) -> dict[str, list[str]]:
    """Compare the files with field.schema_migration without changing anything (for preflight)."""
    files = discover(directory)
    exists = conn.execute("SELECT to_regclass('field.schema_migration') IS NOT NULL AS e").fetchone()["e"]
    applied = ({r["name"]: r["sha256"] for r in conn.execute("SELECT name, sha256 FROM field.schema_migration")}
               if exists else {})
    names = {p.name for p in files}
    return {"pending": [p.name for p in files if p.name not in applied],
            "changed": [p.name for p in files if p.name in applied and applied[p.name] != digest(p)],
            "unknown": sorted(n for n in applied if n not in names)}


def apply_migrations(conn: psycopg.Connection, directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply pending migrations; return the names applied."""
    files = discover(directory)
    conn.execute("SELECT pg_advisory_lock(%s)", (_LOCK_KEY,))
    try:
        with conn.transaction():
            conn.execute("CREATE SCHEMA IF NOT EXISTS field")
            conn.execute(
                """CREATE TABLE IF NOT EXISTS field.schema_migration(
                       name       text PRIMARY KEY,
                       sha256     text NOT NULL,
                       applied_at timestamptz NOT NULL DEFAULT now())"""
            )
        applied = {r["name"]: r["sha256"] for r in conn.execute("SELECT name, sha256 FROM field.schema_migration")}
        done: list[str] = []
        for path in files:
            sql = path.read_text(encoding="utf-8")
            sha = digest(path)
            if path.name in applied:
                if applied[path.name] != sha:
                    raise MigrationError(f"{path.name} changed after it was applied; add a new migration instead")
                continue
            with conn.transaction():
                conn.execute(sql)
                conn.execute("INSERT INTO field.schema_migration(name, sha256) VALUES (%s, %s)", (path.name, sha))
            done.append(path.name)
        return done
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s)", (_LOCK_KEY,))


def main() -> int:
    try:
        url = load_database_url()
    except ConfigError as e:
        print(e, file=sys.stderr)
        return 2
    from pulse.seed import apply_seeds

    with connect(url) as conn:
        done = apply_migrations(conn)
        apply_seeds(conn)
    print("Applied: " + ", ".join(done) if done else "Up to date.")
    print("Reference seeds applied (buckets, report categories, keywords).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
