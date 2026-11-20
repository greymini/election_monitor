"""Connection pool and transaction helper.

Connections are autocommit with UTC session time zone and dict rows; every unit of work runs
inside an explicit `transaction()` block.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

# Sessions run in GMT (offset 0, identical to UTC). The embedded Windows build of PostgreSQL
# (pgserver) ships without the timezone database, so the name "UTC" is rejected there; GMT is
# built into every server. Python resolves it through the `tzdata` package.
CONNECT_KWARGS = {"autocommit": True, "row_factory": dict_row, "options": "-c timezone=GMT"}


def make_pool(database_url: str, *, min_size: int = 1, max_size: int = 10) -> ConnectionPool:
    pool = ConnectionPool(database_url, min_size=min_size, max_size=max_size, kwargs=CONNECT_KWARGS, open=False)
    pool.open(wait=True)
    return pool


def connect(database_url: str) -> psycopg.Connection:
    return psycopg.connect(database_url, **CONNECT_KWARGS)


@contextmanager
def transaction(pool: ConnectionPool) -> Iterator[psycopg.Connection]:
    with pool.connection() as conn:
        with conn.transaction():
            yield conn
