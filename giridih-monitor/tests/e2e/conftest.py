"""Shared fixtures for the end-to-end tests, which need a real PostgreSQL.

These tests are the answer to the audit's structural gap: every metric the
product displays is computed in SQL, and nothing exercised it, so every Critical
and High finding in sections B and D lived in an untested layer.

They are **skipped rather than passed** when no database is reachable, and the
skip reason names the environment variable to set. A skipped test is honest; a
test that quietly passes because it found nothing to check is worse than no test
at all, which is why `test_the_fixture_actually_loaded` exists.

    export E2E_DATABASE_URL='postgresql://user:pass@localhost:5432/giridih_test'
    pytest tests/e2e -v

The database is **rewritten**: the schema is dropped and rebuilt from
db/migrations on every run. Never point this at anything you care about - the
fixture refuses a URL whose database name does not contain 'test'.
"""

from __future__ import annotations

import os

import pytest

E2E_URL_VAR = "E2E_DATABASE_URL"

SKIP_REASON = (
    f"no database: set {E2E_URL_VAR} to a throwaway PostgreSQL 16 with PostGIS and "
    "pgvector, e.g. postgresql://user:pass@localhost:5432/giridih_test. These tests "
    "drop and rebuild the schema."
)


def e2e_url() -> str | None:
    return os.environ.get(E2E_URL_VAR, "").strip() or None


requires_db = pytest.mark.skipif(e2e_url() is None, reason=SKIP_REASON)


@pytest.fixture(scope="session")
def db_url() -> str:
    url = e2e_url()
    if url is None:
        pytest.skip(SKIP_REASON)
    # A guard, not a convenience. These tests DROP SCHEMA public CASCADE.
    tail = url.rsplit("/", 1)[-1].split("?")[0]
    if "test" not in tail.lower():
        pytest.fail(
            f"{E2E_URL_VAR} points at database {tail!r}, which does not look like a "
            "throwaway. These tests drop and rebuild the schema; refusing to run."
        )
    return url


@pytest.fixture(scope="session")
def conn(db_url: str):
    """One connection for the session, with the schema rebuilt from migrations."""
    psycopg = pytest.importorskip("psycopg", reason="psycopg is not installed")

    connection = psycopg.connect(db_url, autocommit=True, row_factory=psycopg.rows.dict_row)
    try:
        with connection.cursor() as cur:
            cur.execute("DROP SCHEMA IF EXISTS public CASCADE")
            cur.execute("CREATE SCHEMA public")

        # Apply migrations through the real runner, so this exercises the same
        # path an operator runs rather than a test-only schema that could drift.
        os.environ["DATABASE_URL"] = db_url
        from common.config import get_settings

        get_settings.cache_clear()
        from db import apply_migrations

        applied = apply_migrations.main(["--seed"])
        if applied != 0:
            pytest.fail("db.apply_migrations --seed failed; see the log above")

        yield connection
    finally:
        connection.close()


@pytest.fixture
def cursor(conn):
    with conn.cursor() as cur:
        yield cur


def refresh_views(cur) -> None:
    """Refresh every materialized view in dependency order.

    Non-concurrently: CONCURRENTLY cannot run inside a transaction and needs the
    view to have been populated once already, neither of which holds for a
    freshly built test schema.
    """
    from analytics.refresh import VIEWS

    for view in VIEWS:
        cur.execute(f"REFRESH MATERIALIZED VIEW {view}")
