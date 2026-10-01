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

# ---------------------------------------------------------------------------
# One loaded dataset, shared
# ---------------------------------------------------------------------------

# Giridih, at close to its real station count, with a roll over two of them.
# The roll is what costs: pdfplumber's text extraction is linear in characters,
# and the published electorate divided over fewer stations means more electors
# per station, not fewer overall.
DATASET_AC = 32
DATASET_BOOTHS = 120
DATASET_ROLL_BOOTHS = 2
DATASET_ELECTIONS = ["VS-2024", "VS-2019"]
DATASET_SEED = 4242

# A constituency that is seeded and deliberately never loaded, so the empty
# state is a real state rather than a hypothetical one.
EMPTY_AC = 42


def run_loader(module: str, argv: list) -> int:
    """Call a loader's `main()` in-process, as its CLI would.

    `main()` rather than the internals, so argument parsing, scope resolution
    and exit codes are covered too - three of the defects this suite found
    lived in exactly those.
    """
    import importlib

    return importlib.import_module(module).main([str(a) for a in argv])


def load_dataset(mock_dir, manifest: dict) -> None:
    """The same order, and the same commands, as scripts/dev_stack.py."""
    docs = manifest["documents"]

    for d in [x for x in docs if x["kind"] == "ps_list"]:
        argv = [mock_dir / d["path"], "--ac", d["ac"], "--election", d["election"],
                "--load", "--block", d["block_id"]]
        if d["anchor"]:
            argv.append("--anchor")
        assert run_loader("ingest.parse_pslist", argv) == 0, d["path"]

    for d in [x for x in docs if x["kind"] == "form20"]:
        # No --skip-ac-check: the AC-total reconciliation gate has to pass.
        assert run_loader("ingest.parse_form20", [
            mock_dir / d["path"], "--ac", d["ac"], "--election", d["election"],
            "--load", "--replace",
        ]) == 0, d["path"]

    anchors = {d["election"] for d in docs if d["kind"] == "ps_list" and d["anchor"]}
    for label in [e for e in manifest["elections"] if e not in anchors]:
        assert run_loader("ingest.crosswalk", [
            "--ac", DATASET_AC, "--election", label, "--apply"]) == 0, label

    for kind in ("roll_mother", "roll_supplement"):
        for d in [x for x in docs if x["kind"] == kind]:
            argv = [mock_dir / d["path"], "--ac", d["ac"], "--election", d["election"],
                    "--revision", d["revision"], "--date", d["date"], "--load"]
            if d["supplement"]:
                argv.append("--supplement")
            else:
                argv += ["--link-election", d["link_election"]]
            assert run_loader("ingest.parse_roll", argv) == 0, d["path"]


@pytest.fixture(scope="session")
def loaded_dataset(conn, db_url, tmp_path_factory):
    """Generate synthetic source documents, load them, refresh the views.

    Session-scoped: the load takes half a minute and both `test_ingest_pipeline`
    and `test_api_contract` read the same state. The `conn` fixture has already
    rebuilt the schema from db/migrations and loaded db/seed.
    """
    import json
    import os

    work = tmp_path_factory.mktemp("dataset")
    mock_dir = work / "mock"
    # Keep the page cache and any retained PDF inside the temp tree: the roll
    # loader refuses to run when it finds roll text under OCR_DIR, and a test
    # must neither depend on nor pollute the repository's ocr/ and raw/.
    previous = {k: os.environ.get(k) for k in ("OCR_DIR", "RAW_DIR", "DATABASE_URL")}
    os.environ["OCR_DIR"] = str(work / "ocr")
    os.environ["RAW_DIR"] = str(work / "raw")
    os.environ["DATABASE_URL"] = db_url

    from common.config import get_settings
    from common.db import close_pools

    get_settings.cache_clear()
    close_pools()

    try:
        assert run_loader("ingest.mock_documents", [
            "--ac", DATASET_AC, "--out-dir", mock_dir, "--booths", DATASET_BOOTHS,
            "--roll-booths", DATASET_ROLL_BOOTHS, "--seed", DATASET_SEED,
            "--elections", ",".join(DATASET_ELECTIONS),
        ]) == 0

        manifest = json.loads((mock_dir / "manifest.json").read_text(encoding="utf-8"))
        load_dataset(mock_dir, manifest)

        from common.db import cursor

        with cursor() as cur:
            refresh_views(cur)
        yield {"manifest": manifest, "dir": mock_dir, "ac": DATASET_AC,
               "booths": DATASET_BOOTHS, "roll_booths": DATASET_ROLL_BOOTHS,
               "elections": DATASET_ELECTIONS, "empty_ac": EMPTY_AC}
    finally:
        close_pools()
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        get_settings.cache_clear()


# ---------------------------------------------------------------------------
# API fixtures: real identifiers, one user per role, a TestClient and tokens.
# Moved here from test_api_contract.py so every API test module can use them.
# ---------------------------------------------------------------------------

API_ROLES = ("admin", "strategist", "block")


@pytest.fixture(scope="module")
def ids(loaded_dataset, db_url):
    """Real identifiers from the loaded data, so a 404 means a bug not a typo."""
    import psycopg

    with psycopg.connect(db_url, row_factory=psycopg.rows.dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT ac_number FROM ac WHERE ac_number = 32")
            ac_number = cur.fetchone()["ac_number"]
            # `review_queue.id`, not `item_id`: the POST path parameter is
            # named `item_id` and the listing returns `id`. Worth a note rather
            # than a rename, since the route is already in use.
            cur.execute("SELECT id FROM review_queue WHERE status = 'open' "
                        "ORDER BY id LIMIT 1")
            row = cur.fetchone()
            review_item_id = row["id"] if row else 1
            cur.execute("SELECT block_id FROM block b JOIN ac a USING (ac_id) "
                        "WHERE a.ac_number = 32 ORDER BY block_id")
            blocks = [r["block_id"] for r in cur.fetchall()]
            # A booth inside the block the `users` fixture gives the block role
            # (blocks[1] when there are two), so every role is admitted to it
            # and the sweep tests role access, not block scoping - which
            # tests/e2e/test_booth_card.py covers on its own.
            block_user_block = blocks[1] if len(blocks) > 1 else blocks[0]
            cur.execute("SELECT b.booth_uid FROM booth b JOIN area a ON a.area_id = b.area_id "
                        "WHERE a.block_id = %s ORDER BY b.booth_uid LIMIT 1",
                        (block_user_block,))
            booth_uid = cur.fetchone()["booth_uid"]
            # The booth PS 1 already maps to, so the POST /admin/crosswalk the
            # sweep sends is provably a no-op. The sweep has to call every
            # route, and this one rewrites a crosswalk binding - pointing it at
            # the current value is how it stays a contract test rather than a
            # mutation that later assertions have to tolerate.
            cur.execute("SELECT booth_uid FROM booth_crosswalk x "
                        "JOIN election e ON e.election_id = x.election_id "
                        "WHERE e.label = 'VS-2024' AND x.ps_number = 1 "
                        "AND x.ac_id = (SELECT ac_id FROM ac WHERE ac_number = 32)")
            row = cur.fetchone()
            ps1_booth = row["booth_uid"] if row else booth_uid
            # A second AC with no booth data, for the empty-state and scoping
            # checks. 42-Tundi is seeded and deliberately never loaded.
            cur.execute("SELECT ac_number FROM ac WHERE ac_number = %s",
                        (loaded_dataset["empty_ac"],))
            empty_ac = cur.fetchone()["ac_number"]
    return {
        "ac_number": ac_number, "booth_uid": booth_uid,
        "election_label": "VS-2024", "review_item_id": review_item_id,
        "blocks": blocks, "empty_ac": empty_ac, "ps1_booth": ps1_booth,
    }


@pytest.fixture(scope="module")
def users(loaded_dataset, ids, db_url):
    """One user per role, created through the real path, with a known password."""
    import os

    os.environ["DATABASE_URL"] = db_url
    os.environ.setdefault("JWT_SECRET", "contract-test-secret-at-least-32-bytes-long")
    from common.config import get_settings
    from common.db import close_pools

    get_settings.cache_clear()
    close_pools()

    from api.deps import hash_password
    from common.db import query_one

    made = {}
    for index, role in enumerate(API_ROLES):
        phone = f"95000000{index:02d}"
        password = f"contract-{role}-pw"
        block = ids["blocks"][1] if role == "block" and len(ids["blocks"]) > 1 else (
            ids["blocks"][0] if role == "block" else None)
        row = query_one(
            "INSERT INTO app_user (phone, name, role, block_id, password_hash, "
            "daily_token_budget) VALUES (%s, %s, %s, %s, %s, 150000) "
            "ON CONFLICT (phone) DO UPDATE SET role = EXCLUDED.role, "
            "block_id = EXCLUDED.block_id, password_hash = EXCLUDED.password_hash, "
            "is_active = true RETURNING user_id",
            (phone, f"Contract {role}", role, block, hash_password(password)),
        )
        made[role] = {"phone": phone, "password": password,
                      "user_id": row["user_id"], "block_id": block}
    return made


@pytest.fixture(scope="module")
def client(users, db_url):
    from fastapi.testclient import TestClient

    from api.main import app

    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(scope="module")
def tokens(client, users):
    out = {}
    for role, spec in users.items():
        response = client.post("/auth/login",
                               json={"phone": spec["phone"], "password": spec["password"]})
        assert response.status_code == 200, (role, response.status_code, response.text)
        out[role] = response.json()["access_token"]
    return out
