"""Postgres access helpers (psycopg 3).

Two pools: the application pool (read/write) and the chatbot read-only pool that
authenticates as giridih_ro, which is restricted by GRANT to the allow-listed
tables in db/migrations/0012_roles_grants.sql.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator, Sequence
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.numeric import FloatLoader
from psycopg_pool import ConnectionPool

from common.config import get_settings
from common.logging_setup import get_logger

log = get_logger(__name__)

_pool: ConnectionPool | None = None
_ro_pool: ConnectionPool | None = None


def _configure(conn: psycopg.Connection) -> None:
    """Load NUMERIC as float on every pooled connection.

    psycopg's default is `decimal.Decimal`, and FastAPI (pydantic 2) serialises a
    Decimal as a JSON *string*, so margin_pct, turnout_pct, priority_score and
    every other NUMERIC column reached the browser as "25.29" and the frontend's
    `value.toFixed()` threw on it. The values are percentages, shares and scores
    already rounded in SQL, well inside float precision; no code here relies on
    Decimal semantics. tests/e2e/test_api_json_types.py pins this.
    """
    conn.adapters.register_loader("numeric", FloatLoader)


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        s = get_settings()
        if not s.database_url:
            raise RuntimeError("DATABASE_URL is not set")
        _pool = ConnectionPool(
            s.database_url,
            min_size=1,
            max_size=4,
            timeout=120,
            kwargs={"row_factory": dict_row, "connect_timeout": 30},
            configure=_configure,
        )
    return _pool


def get_readonly_pool() -> ConnectionPool:
    global _ro_pool
    if _ro_pool is None:
        s = get_settings()
        url = s.readonly_db_url or s.database_url
        if not url:
            raise RuntimeError("READONLY_DB_URL / DATABASE_URL is not set")
        _ro_pool = ConnectionPool(url, min_size=0, max_size=4,
                                  kwargs={"row_factory": dict_row}, configure=_configure)
    return _ro_pool


def close_pools() -> None:
    global _pool, _ro_pool
    for p in (_pool, _ro_pool):
        if p is not None:
            with contextlib.suppress(Exception):
                p.close()
    _pool = None
    _ro_pool = None


@contextlib.contextmanager
def connection(readonly: bool = False) -> Iterator[psycopg.Connection]:
    pool = get_readonly_pool() if readonly else get_pool()
    with pool.connection() as conn:
        yield conn


@contextlib.contextmanager
def cursor(readonly: bool = False) -> Iterator[psycopg.Cursor]:
    with connection(readonly=readonly) as conn, conn.cursor() as cur:
        yield cur


def query(sql: str, params: Sequence[Any] | dict[str, Any] | None = None,
          readonly: bool = False) -> list[dict[str, Any]]:
    with cursor(readonly=readonly) as cur:
        cur.execute(sql, params)
        if cur.description is None:
            return []
        return cur.fetchall()


def query_one(sql: str, params: Sequence[Any] | dict[str, Any] | None = None,
              readonly: bool = False) -> dict[str, Any] | None:
    rows = query(sql, params, readonly=readonly)
    return rows[0] if rows else None


def execute(sql: str, params: Sequence[Any] | dict[str, Any] | None = None) -> int:
    with cursor() as cur:
        cur.execute(sql, params)
        return cur.rowcount


def executemany(sql: str, rows: Sequence[Sequence[Any]]) -> int:
    if not rows:
        return 0
    with cursor() as cur:
        cur.executemany(sql, rows)
        return cur.rowcount


def copy_rows(table: str, columns: Sequence[str], rows: Sequence[Sequence[Any]]) -> int:
    """Bulk load via COPY. Used by the Form 20 and roll loaders."""
    if not rows:
        return 0
    cols = ", ".join(columns)
    with cursor() as cur:
        with cur.copy(f"COPY {table} ({cols}) FROM STDIN") as cp:
            for row in rows:
                cp.write_row(row)
        return len(rows)
