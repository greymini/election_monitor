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

import logging
import os

import pytest

E2E_URL_VAR = "E2E_DATABASE_URL"


def e2e_url() -> str | None:
    return os.environ.get(E2E_URL_VAR, "").strip() or None


def pgserver_available() -> bool:
    """Whether a PostgreSQL can be started in-process.

    Since N4 the schema needs only pgvector - no PostGIS, no pg_trgm, no
    unaccent - which is exactly what `pgserver` bundles. So these tests no
    longer require Docker, an administrator, or a database somebody set up in
    advance: if the package is installed, they run.
    """
    try:
        import pgserver  # noqa: F401
    except ImportError:
        return False
    return True


SKIP_REASON = (
    "no database and no pgserver. Either `pip install -r requirements-dev.txt`, "
    "which brings a self-contained PostgreSQL needing no Docker, or set "
    f"{E2E_URL_VAR} to a throwaway PostgreSQL 16 with pgvector. These tests drop "
    "and rebuild the schema."
)

requires_db = pytest.mark.skipif(
    e2e_url() is None and not pgserver_available(), reason=SKIP_REASON
)


@pytest.fixture(scope="session")
def db_url() -> str:
    """A database to rebuild from scratch.

    An explicitly supplied `E2E_DATABASE_URL` wins, because an operator naming a
    database is making a deliberate choice - most importantly the choice to test
    against the real image, which is the only way to exercise anything PostGIS
    would be needed for. Otherwise an in-process server is started, so the suite
    runs by default rather than skipping.
    """
    url = e2e_url()
    if url is not None:
        # A guard, not a convenience. These tests DROP SCHEMA public CASCADE.
        tail = url.rsplit("/", 1)[-1].split("?")[0]
        if "test" not in tail.lower():
            pytest.fail(
                f"{E2E_URL_VAR} points at database {tail!r}, which does not look "
                "like a throwaway. These tests drop and rebuild the schema; "
                "refusing to run."
            )
        return url

    if not pgserver_available():
        pytest.skip(SKIP_REASON)

    import pgserver

    # Not named with "test" because the guard above does not apply: this server
    # exists only for this session and its data directory is a pytest temp path.
    data_dir = _session_tmp()
    server = pgserver.get_server(str(data_dir))
    try:
        yield server.get_uri()
    finally:
        server.cleanup()
        # pgserver's atexit hook logs after pytest has closed the stream its
        # handler writes to, which surfaces as `ValueError: I/O operation on
        # closed file` in the tail of the run and reads like a test failure.
        logging.getLogger("pgserver").disabled = True
        logging.getLogger("pgserver.postgres_server").disabled = True


def _session_tmp():
    """A temp directory for the in-process server's data files."""
    import tempfile
    from pathlib import Path

    return Path(tempfile.mkdtemp(prefix="giridih-e2e-"))


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
