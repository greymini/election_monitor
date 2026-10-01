"""Metric parity, the half that needs no database.

Three separate claims are checked here, and it is worth keeping them apart
because they fail for different reasons:

1. **The Python implementation matches the hand-computed answer.** Cases live in
   `tests/metric_cases.py` and are worked out from the master prompt's
   definitions rather than read off either implementation.

2. **The SQL in the migration is the SQL the generator produces.** The metric
   formulas live once, in `analytics/metric_sql.py`; `0015_metrics.sql` carries a
   generated block between two markers. Edit a formula and forget to regenerate,
   and this fails - which is the whole mechanism by which "implemented exactly
   once" is enforced rather than merely intended.

3. **The views call the functions instead of restating the formulas.** Checked by
   scanning the migration outside the generated block for the arithmetic that
   used to be inlined there. Without this, someone can add an eleventh view next
   year with its own copy of the margin quotient and nothing notices.

The half that actually evaluates both implementations and compares them is
`tests/e2e/test_metric_parity_sql.py`, which needs a PostgreSQL and is skipped
without one. That split is deliberate: everything checkable without a database
should fail fast in the default suite, so the only thing waiting on Postgres is
the thing that genuinely cannot run without it.
"""

from __future__ import annotations

import re

import pytest

from analytics import metric_sql, metrics
from tests import metric_cases

# ---------------------------------------------------------------------------
# 1. The Python side against the hand-computed answers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("item", metric_cases.flat(), ids=metric_cases.case_id)
def test_the_python_implementation_matches_the_hand_computed_answer(item):
    name, case = item
    actual = case.python()
    if case.expected is None:
        assert actual is None, (
            f"{name} / {case.name}: expected NULL, got {actual!r}. A metric that "
            "returns a number where the answer is unknown is the failure mode "
            "this whole file exists to catch."
        )
        return
    if isinstance(case.expected, bool):
        assert actual is case.expected, f"{name} / {case.name}"
        return
    assert actual == pytest.approx(case.expected), f"{name} / {case.name}"


def test_every_generated_function_has_at_least_one_case():
    """A metric with no cases is worse than an untested one: it looks covered.

    The registry in metric_cases is keyed by SQL function name precisely so this
    check is possible, and so adding a function without cases fails here rather
    than passing quietly with a smaller denominator.
    """
    declared = {fn.name for fn in metric_sql.METRIC_FUNCTIONS}
    covered = set(metric_cases.CASES)
    assert declared - covered == set(), (
        f"no hand-computed cases for: {sorted(declared - covered)}"
    )
    assert covered - declared == set(), (
        f"cases for functions that do not exist: {sorted(covered - declared)}"
    )


def test_every_declared_python_counterpart_exists():
    """Each function names the Python callable it must agree with.

    Renaming or deleting a function in `metrics.py` without touching
    `metric_sql.py` would otherwise leave the SQL side unpaired, and the parity
    test would go on passing over whatever was left.
    """
    for fn in metric_sql.METRIC_FUNCTIONS:
        assert hasattr(metrics, fn.python), (
            f"{fn.name} names analytics.metrics.{fn.python}, which does not exist"
        )
        assert callable(getattr(metrics, fn.python))


# ---------------------------------------------------------------------------
# 2. The generated block is current
# ---------------------------------------------------------------------------


def test_the_generated_block_in_the_migration_is_current():
    """The mechanism that makes one source of truth real rather than aspirational.

    If this fails, run:

        python -m analytics.metric_sql

    and commit the migration alongside the change to metric_sql.py.
    """
    path = metric_sql.migration_path()
    text = path.read_text(encoding="utf-8")

    assert metric_sql.BEGIN_MARKER in text, "the generated block's markers are gone"
    assert metric_sql.END_MARKER in text

    start = text.index(metric_sql.BEGIN_MARKER)
    end = text.index(metric_sql.END_MARKER) + len(metric_sql.END_MARKER)
    in_file = text[start:end]
    expected = metric_sql.generate_sql().rstrip()

    assert in_file == expected, (
        "0015_metrics.sql has drifted from analytics/metric_sql.py. The formulas "
        "live in the Python module; regenerate with `python -m analytics.metric_sql`."
    )


def test_every_function_is_immutable_and_sql_bodied():
    """IMMUTABLE is load-bearing, not decoration.

    It is what lets the planner inline these into the surrounding view, which is
    what makes a function call per row acceptable during a materialized view
    refresh over every booth in six constituencies. A function that lost it
    would still be correct and would quietly stop being inlinable.
    """
    sql = metric_sql.generate_sql()
    for fn in metric_sql.METRIC_FUNCTIONS:
        assert f"CREATE OR REPLACE FUNCTION {fn.name}(" in sql
        assert f"COMMENT ON FUNCTION {fn.name}(" in sql, (
            f"{fn.name} has no COMMENT, so the NULL rule it enforces is invisible "
            "to anyone reading the schema"
        )
    assert sql.count("LANGUAGE sql IMMUTABLE") == len(metric_sql.METRIC_FUNCTIONS)


def test_the_comment_literals_are_well_formed():
    """The COMMENT bodies are wrapped across adjacent string literals.

    Adjacent literals concatenate in SQL, but only if each is closed - and an
    apostrophe inside a metric's prose would end the literal early and turn the
    rest of the sentence into syntax. lint_sql.py would catch the resulting
    mess, but this says why.
    """
    sql = metric_sql.generate_sql()
    for line in sql.splitlines():
        stripped = line.strip()
        if not stripped.startswith("'"):
            continue
        body = stripped.rstrip(";")
        assert body.startswith("'") and body.endswith("'"), line
        # Every apostrophe inside must be doubled, so the count is even.
        assert body[1:-1].count("'") % 2 == 0, (
            f"unbalanced quote in a COMMENT literal: {line}"
        )


# ---------------------------------------------------------------------------
# 3. The views call the functions
# ---------------------------------------------------------------------------


def _migration_outside_generated_block() -> str:
    text = metric_sql.migration_path().read_text(encoding="utf-8")
    start = text.index(metric_sql.BEGIN_MARKER)
    end = text.index(metric_sql.END_MARKER) + len(metric_sql.END_MARKER)
    return text[:start] + text[end:]


def _strip_comments(sql: str) -> str:
    """Drop `--` comment text, which discusses these formulas at length."""
    return "\n".join(line.split("--", 1)[0] for line in sql.splitlines())


# The one percentage in this migration that is not a section 3.2 metric:
# crosswalk coverage, a data-health figure computed over ps_list_entry. C11 is
# about its denominator, not its formula, so it has no generated function.
_ALLOWED_INLINE_QUOTIENTS = 1


def test_no_view_restates_a_percentage_formula():
    """The check that keeps this from decaying.

    Seven copies of the margin quotient were spread across two views when this
    started - once for `margin_pct`, twice more inside the signed variant's CASE
    arms, and the same four again at AC grain. That is how D1 survived review:
    correcting the denominator in one arm and not the others is a one-character
    omission, and no test distinguishes it from correctness.
    """
    body = _strip_comments(_migration_outside_generated_block())
    quotients = re.findall(r"100\.0\s*\*", body)
    assert len(quotients) <= _ALLOWED_INLINE_QUOTIENTS, (
        f"{len(quotients)} inline percentage formula(s) outside the generated "
        f"block, expected at most {_ALLOWED_INLINE_QUOTIENTS} (crosswalk "
        "coverage). Call the generated metric_* function instead of writing the "
        "arithmetic out again."
    )


@pytest.mark.parametrize("token", [
    # The weights, which must appear only in metric_priority_score/_weight.
    "0.35",
    "0.25",
    # The crosswalk auto-accept threshold, which belongs to
    # metric_comparison_allowed alone.
    "0.85",
    # The sample-stdev call, which belongs to metric_volatility.
    "stddev_samp",
    # The Pedersen halving.
    "/ 2.0",
])
def test_no_view_restates_a_constant_that_belongs_to_a_function(token):
    body = _strip_comments(_migration_outside_generated_block())
    assert token not in body, (
        f"{token!r} appears in a view. It is defined inside the generated "
        "functions; a second copy is a second place for it to be changed."
    )


def test_the_ac_summary_ranks_candidates_not_party_totals():
    """N2, as a structural guard.

    `mv_ac_summary` must rank over `candidate_totals`, which is per candidate on
    the same `contestant` key as `mv_result_booth_candidate`. It used to rank
    over `party_totals`, where every independent shares a NULL `party_id` and
    collapses into one row - so the constituency headline could name a winner
    that no booth in the constituency had elected. D3 was fixed at booth grain
    and left standing here.

    The real proof is `test_the_ac_winner_agrees_with_the_booth_table` in the
    e2e suite, which sums the booth table and compares. This one is cheap, runs
    without a database, and would catch the ranking being quietly moved back.
    """
    body = _migration_outside_generated_block()
    ac_summary = body[body.index("CREATE MATERIALIZED VIEW mv_ac_summary"):]

    assert "), candidate_totals AS (" in ac_summary, (
        "mv_ac_summary has no candidate_totals CTE"
    )
    ranked = ac_summary[ac_summary.index("), ranked AS ("):]
    ranked = ranked[:ranked.index("), crosswalk AS (")]
    assert "FROM candidate_totals" in ranked, (
        "mv_ac_summary's ranked CTE does not read candidate_totals; if it reads "
        "party_totals again, independents are bucketed and the AC headline can "
        "disagree with the booth table (N2)"
    )
    assert "FROM party_totals" not in ranked, (
        "mv_ac_summary ranks over party_totals, which buckets every independent "
        "into a single NULL-party_id row (N2)"
    )


def test_the_views_actually_call_the_generated_functions():
    """The inverse of the checks above: absence of the formulas would also be
    satisfied by a migration that computed nothing at all."""
    body = _migration_outside_generated_block()
    for fn in metric_sql.METRIC_FUNCTIONS:
        assert f"{fn.name}(" in body, (
            f"{fn.name} is generated but never called by any view, so it is "
            "dead schema and whatever the views do compute is unchecked"
        )


# ---------------------------------------------------------------------------
# N1: absence now means the same thing on both sides
# ---------------------------------------------------------------------------


def test_absent_crosswalk_means_cannot_compare_on_both_sides():
    """N1, closed.

    In SQL a NULL confidence comes from a LEFT JOIN that found no
    `booth_crosswalk` row, so the booth cannot be shown to be the station it was
    and no comparison is carried. Python used to read the same absence as
    "nothing to gate on", because `link` defaulted to `None` and `None` meant
    anchor - so the two sides gave opposite answers for the input most likely to
    arise, and a caller who merely forgot the argument got ungated swings.

    Both refuse now, and the anchor case has a name instead of being the
    default.
    """
    assert metrics.comparison_allowed(None) is False
    assert metrics.comparison_allowed(metrics.ANCHOR) is True
    assert metrics.comparison_allowed(metrics.CrosswalkLink(0.0, False)) is False


def test_the_crosswalk_link_cannot_be_omitted():
    """The half of N1 that does the work.

    Refusing to compare when the link is `None` only helps if the caller is made
    to supply one. A default - of any value - lets the question go unasked,
    which is how it went unasked.
    """
    for call in (
        lambda: metrics.comparison_allowed(),
        lambda: metrics.swing_pct(45.0, 40.0),
        lambda: metrics.alliance_swing_pct({}, {}, {}, {}, "NDA"),
    ):
        with pytest.raises(TypeError):
            call()
