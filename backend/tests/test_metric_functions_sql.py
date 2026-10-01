"""The generated metric functions, executed in a real PostgreSQL.

This closes the gap that mattered most about item 1: the generated SQL had never
been run. `lint_sql.py` is a static check, and the reasoning behind the
parameter types - `BIGINT` because `COUNT(*)` is bigint, `DOUBLE PRECISION`
because `PERCENT_RANK()` is float8, a cast on `array_remove(…, NULL)` because
the function is polymorphic - was reasoning, not evidence.

**Why this suite exists alongside `tests/e2e/`.** The e2e suite needs a database
with the full schema, which needs PostGIS, pgvector, `pg_trgm` and `unaccent`,
which in practice needs Docker or an administrator. This one starts PostgreSQL
in-process with `pgserver` - a pip-installable, self-contained server - creates
**only** the generated function block, and runs the arithmetic. No extensions, no
tables, no views.

**What it therefore proves, and what it does not.** It proves the generated SQL
parses, that every function resolves at the types the callers use, that
PostgreSQL marks them IMMUTABLE, and that all fifteen agree with
`analytics/metrics.py` and with the hand-computed answers in
`tests/metric_cases.py`. It proves nothing about the views: whether they apply,
whether they call these functions with the right columns, whether a refresh
produces the right rows. A view can call the right function with the wrong
arguments and every test here still passes. That is `tests/e2e/`'s job, it is
recorded as NOT RUN in UAT_READINESS.md, and this suite is not a substitute for
it - it is the part that could be verified without waiting for a stack.

Skipped, with the reason named, if `pgserver` is not installed.
"""

from __future__ import annotations

import logging

import pytest

from analytics import metric_sql
from tests import metric_cases
from tests.sql_eval import call_sql, comparable, create_functions

pgserver = pytest.importorskip(
    "pgserver",
    reason="pgserver is not installed; it provides a self-contained PostgreSQL "
           "needing no Docker and no administrator rights. Install it with "
           "`pip install -r requirements-dev.txt` to execute the generated "
           "metric functions rather than only lint them.",
)


@pytest.fixture(scope="module")
def functions_cursor(tmp_path_factory):
    """A throwaway PostgreSQL with the generated functions and nothing else.

    Module-scoped: starting a server takes a few seconds and the cases are
    read-only, so there is nothing to isolate between them. The data directory
    is a pytest temp path, so it is cleaned up with the rest of the run.
    """
    psycopg = pytest.importorskip("psycopg", reason="psycopg is not installed")

    data_dir = tmp_path_factory.mktemp("pgdata")
    server = pgserver.get_server(str(data_dir))
    try:
        conn = psycopg.connect(
            server.get_uri(), autocommit=True, row_factory=psycopg.rows.dict_row
        )
        try:
            with conn.cursor() as cur:
                created = create_functions(cur)
                assert created == len(metric_sql.METRIC_FUNCTIONS)
                yield cur
        finally:
            conn.close()
    finally:
        server.cleanup()
        # pgserver also registers an atexit hook that logs after the server is
        # gone. By then pytest has closed the stream its logging handler writes
        # to, so the hook raises `ValueError: I/O operation on closed file` into
        # the tail of the run output - nothing to do with the tests, but it
        # reads exactly like one of them broke. Silencing the library's logger
        # after its server is down is narrower than turning off
        # `logging.raiseExceptions` globally.
        logging.getLogger("pgserver").disabled = True
        logging.getLogger("pgserver.postgres_server").disabled = True


# ---------------------------------------------------------------------------
# The SQL is real SQL
# ---------------------------------------------------------------------------


def test_every_generated_function_exists_in_the_catalogue(functions_cursor):
    """The first thing worth knowing: the block executes at all.

    Before this suite, `scripts/lint_sql.py` was the only check on it, and its
    own output says it "does not prove the SQL applies".
    """
    functions_cursor.execute(
        "SELECT p.proname FROM pg_proc p "
        "JOIN pg_namespace n ON n.oid = p.pronamespace "
        "WHERE n.nspname = 'public' AND p.proname LIKE 'metric\\_%'"
    )
    found = {row["proname"] for row in functions_cursor.fetchall()}
    declared = {fn.name for fn in metric_sql.METRIC_FUNCTIONS}
    assert declared - found == set(), f"not created: {sorted(declared - found)}"


def test_every_function_is_immutable_in_the_catalogue(functions_cursor):
    """IMMUTABLE is load-bearing: it is what lets the planner inline these into
    a view refresh over every booth in six constituencies. Asserted against
    PostgreSQL's own catalogue, not against the text that claims it."""
    functions_cursor.execute(
        "SELECT p.proname, p.provolatile FROM pg_proc p "
        "JOIN pg_namespace n ON n.oid = p.pronamespace "
        "WHERE n.nspname = 'public' AND p.proname LIKE 'metric\\_%'"
    )
    mutable = [
        r["proname"] for r in functions_cursor.fetchall() if r["provolatile"] != "i"
    ]
    assert mutable == [], f"not IMMUTABLE: {sorted(mutable)}"


def test_every_function_carries_its_comment(functions_cursor):
    """The COMMENT is where each NULL rule and the finding it prevents are
    recorded, for anyone reading the schema rather than this repository."""
    functions_cursor.execute(
        "SELECT p.proname, obj_description(p.oid, 'pg_proc') AS comment "
        "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
        "WHERE n.nspname = 'public' AND p.proname LIKE 'metric\\_%'"
    )
    rows = functions_cursor.fetchall()
    bare = [r["proname"] for r in rows if not (r["comment"] or "").strip()]
    assert bare == [], f"no COMMENT on: {sorted(bare)}"


# ---------------------------------------------------------------------------
# Three-way agreement: hand-computed == Python == SQL
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("item", metric_cases.flat(), ids=metric_cases.case_id)
def test_sql_python_and_the_hand_computed_answer_all_agree(functions_cursor, item):
    name, case = item
    sql_value = comparable(call_sql(functions_cursor, name, case.sql_args))
    py_value = comparable(case.python())

    if case.expected is None:
        assert sql_value is None, (
            f"{name} / {case.name}: SQL returned {sql_value!r} where the answer "
            "is unknown. A number here is the failure this file exists to catch."
        )
        assert py_value is None, f"{name} / {case.name}: Python gave {py_value!r}"
        return

    if isinstance(case.expected, bool):
        assert sql_value is case.expected, f"{name} / {case.name}: SQL"
        assert py_value is case.expected, f"{name} / {case.name}: Python"
        return

    assert sql_value == pytest.approx(case.expected), (
        f"{name} / {case.name}: SQL gave {sql_value!r}, hand-computed "
        f"{case.expected!r}"
    )
    assert py_value == pytest.approx(case.expected), (
        f"{name} / {case.name}: Python gave {py_value!r}, hand-computed "
        f"{case.expected!r}"
    )
    assert sql_value == pytest.approx(py_value), (
        f"{name} / {case.name}: the two implementations disagree - "
        f"SQL {sql_value!r} against Python {py_value!r}"
    )


# ---------------------------------------------------------------------------
# The audit's wrong answers, asserted as wrong
# ---------------------------------------------------------------------------


def test_the_margin_denominator_includes_nota(functions_cursor):
    """D1 as a number. Giridih 2024: a margin of 3,838 over 207,459 valid votes
    is 1.85 percent, which the ECI published. Over the NOTA-excluding 205,455 it
    is 1.87 - small enough to read as rounding, large enough that no row in the
    old views reconciled against itself."""
    g = metric_cases.GIRIDIH_2024
    correct = comparable(call_sql(
        functions_cursor, "metric_margin_pct",
        (g["jmm"], g["bjp"], 3, g["valid_votes"]),
    ))
    assert correct == pytest.approx(g["margin_pct"])

    with_the_bug = comparable(call_sql(
        functions_cursor, "metric_margin_pct",
        (g["jmm"], g["bjp"], 3, g["valid_votes"] - g["nota"]),
    ))
    assert with_the_bug == pytest.approx(metric_cases.MARGIN_PCT_WITH_D1_DENOMINATOR)
    assert with_the_bug != pytest.approx(correct), (
        "the two denominators now give the same answer, so this test no longer "
        "distinguishes D1 from a correct implementation"
    )


def test_one_poll_type_does_not_give_a_fifty_percent_floating_vote(functions_cursor):
    """D4. With one poll every party's delta equals its whole share in that poll,
    the shares sum to 100, and half of 100 is 50 - so every booth in the
    constituency reported exactly 50.00, it reached the map and the priority
    score, and being uniform it flattened PERCENT_RANK so the score silently
    lost its floating-vote term."""
    assert call_sql(functions_cursor, "metric_floating_pct", (100.0, 1)) is None

    two = comparable(call_sql(functions_cursor, "metric_floating_pct", (100.0, 2)))
    assert two == pytest.approx(metric_cases.FLOATING_PCT_WITH_D4_BUG), (
        "the arithmetic that produced the 50.00 has changed, so this test no "
        "longer shows that the NULL rule is what prevents it"
    )


def test_an_uncontested_booth_has_no_margin_rather_than_zero(functions_cursor):
    """Zero would sort it as the tightest contest in the constituency, which is
    the top of the priority list."""
    assert call_sql(functions_cursor, "metric_margin_votes", (94_042, None, 1)) is None
    assert call_sql(
        functions_cursor, "metric_margin_pct", (94_042, None, 1, 207_459)
    ) is None


def test_a_third_party_win_is_null_rather_than_the_ramp_midpoint(functions_cursor):
    """F1. Zero sits at the diverging ramp's neutral midpoint, so a third-party
    win would render identically to a knife-edge contest between the pair."""
    assert call_sql(
        functions_cursor, "metric_signed_margin_pct",
        ("JLKM", 80_000, 79_000, 3, 207_459, "JMM", "BJP"),
    ) is None


def test_a_booth_with_no_crosswalk_row_carries_no_comparison(functions_cursor):
    """N1, on the SQL side. A NULL confidence is a LEFT JOIN that found no
    `booth_crosswalk` row; the booth cannot be shown to be the station it was."""
    assert call_sql(
        functions_cursor, "metric_comparison_allowed", (None, None, None, False)
    ) is False
    assert call_sql(
        functions_cursor, "metric_swing_pct", (45.0, 40.0, None, None, None, False)
    ) is None


# ---------------------------------------------------------------------------
# The type reasoning, checked rather than argued
# ---------------------------------------------------------------------------


def test_the_functions_resolve_at_the_types_the_views_pass(functions_cursor):
    """The parameter widths were chosen by reasoning about implicit casts. This
    checks the reasoning against PostgreSQL.

    `COUNT(*)` is bigint, `PERCENT_RANK()` is double precision, `SUM` over
    numeric is numeric, `booth_crosswalk.confidence` is real. Each is passed
    here as the type a view would actually produce, with no cast, so a
    resolution failure is a test failure rather than a migration failure months
    from now.
    """
    functions_cursor.execute("""
        SELECT metric_margin_votes(w, r, c)               AS margin_votes,
               metric_margin_pct(w, r, c, v)              AS margin_pct,
               metric_turnout_pct(p, e)                   AS turnout_pct,
               metric_floating_pct(s, c)                  AS floating_pct,
               metric_priority_score(pr, pr, pr, pr)      AS priority_score,
               metric_priority_weight(pr, pr, pr, pr)     AS priority_weight,
               metric_comparison_allowed(conf, true, NULL, false) AS allowed
          FROM (SELECT COUNT(*)                 AS c,
                       SUM(x)::INT              AS w,
                       SUM(x)::INT - 1000       AS r,
                       SUM(x)::INT * 2          AS v,
                       SUM(x)::INT              AS p,
                       SUM(x)::INT * 3          AS e,
                       SUM(x)::NUMERIC          AS s,
                       PERCENT_RANK() OVER (ORDER BY SUM(x)) AS pr,
                       0.9::REAL                AS conf
                  FROM (VALUES (5000), (4000)) AS t(x)) AS shaped
    """)
    row = functions_cursor.fetchone()
    # The values matter less than the call resolving, but a NULL everywhere
    # would mean it resolved and computed nothing.
    assert row["margin_votes"] is not None
    assert row["margin_pct"] is not None
    assert row["turnout_pct"] is not None
    assert row["allowed"] is True


def test_volatility_strips_nulls_from_its_array(functions_cursor):
    """`array_remove(arr, NULL)` does remove NULLs - it takes an explicit
    null-search branch rather than comparing, which equality against NULL could
    never satisfy. That was reasoned about when the function was written and is
    the kind of claim that should be executed rather than believed."""
    assert comparable(call_sql(
        functions_cursor, "metric_volatility", ([10.0, None, 20.0],)
    )) == pytest.approx(7.07)
    assert call_sql(functions_cursor, "metric_volatility", ([10.0, None],)) is None
    assert call_sql(functions_cursor, "metric_volatility", ([],)) is None
    assert call_sql(functions_cursor, "metric_volatility", (None,)) is None
