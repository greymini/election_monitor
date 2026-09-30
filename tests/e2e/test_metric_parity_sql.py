"""Metric parity: the generated SQL against the Python, on identical inputs.

The half of the parity check that needs a real PostgreSQL, because the only way
to know what a SQL expression returns is to ask PostgreSQL. Skipped, loudly and
by name, when no database is reachable - see tests/e2e/conftest.py.

Each case from `tests/metric_cases.py` is evaluated three ways and all three
must agree:

    hand-computed expectation  ==  analytics/metrics.py  ==  metric_*() in SQL

Two implementations agreeing only proves they are consistent. The hand-computed
column is what makes this a correctness test: the failure mode this project
keeps meeting is not two implementations disagreeing, it is both of them being
wrong in the same plausible way - a denominator that excludes NOTA, a COALESCE
to zero where the answer is unknown.

Placeholder casts are generated from the declared parameter types rather than
written per call. PostgreSQL resolves function arguments using implicit casts
only, and several of the natural argument types are not implicitly convertible:
`COUNT(*)` is bigint and int8 does not become int4, `PERCENT_RANK()` is double
precision and float8 does not become numeric, `confidence` is real and float8
does not become real either. Deriving the casts from the declaration means a
type change in metric_sql.py cannot leave this test calling the old signature.
"""

from __future__ import annotations

import decimal

import pytest

from analytics import metric_sql
from tests import metric_cases
from tests.e2e.conftest import requires_db

pytestmark = requires_db


def _call_sql(cur, name: str, args: tuple) -> object:
    """Evaluate one generated function, casting each placeholder to its
    declared parameter type."""
    fn = next(f for f in metric_sql.METRIC_FUNCTIONS if f.name == name)
    assert len(args) == len(fn.args), (
        f"{name} takes {len(fn.args)} argument(s), the case supplies {len(args)}"
    )
    placeholders = ", ".join(f"%s::{typ}" for _, typ in fn.args)
    cur.execute(f"SELECT {name}({placeholders}) AS value", list(args))
    return cur.fetchone()["value"]


def _comparable(value: object) -> object:
    """Decimal and float compare badly; booleans and None pass through."""
    if isinstance(value, decimal.Decimal):
        return float(value)
    return value


# ---------------------------------------------------------------------------
# The functions exist at all
# ---------------------------------------------------------------------------


def test_every_generated_function_was_actually_created(cursor):
    """Before comparing values, establish that the migration created these.

    Without this, a migration that silently failed to create the functions would
    surface as a confusing per-case error rather than one clear failure, and a
    typo in a function name would look like a metric disagreement.
    """
    cursor.execute(
        "SELECT p.proname, pg_get_function_identity_arguments(p.oid) AS args "
        "FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
        "WHERE n.nspname = 'public' AND p.proname LIKE 'metric\\_%' "
        "ORDER BY p.proname"
    )
    found = {row["proname"] for row in cursor.fetchall()}
    declared = {fn.name for fn in metric_sql.METRIC_FUNCTIONS}
    assert declared - found == set(), (
        f"the migration did not create: {sorted(declared - found)}"
    )


def test_every_function_is_marked_immutable_in_the_catalogue(cursor):
    """Checked in the catalogue, not in the file.

    tests/test_metric_parity.py asserts the generated text says IMMUTABLE; this
    asserts PostgreSQL agrees, which is the claim that matters for whether the
    planner will inline these into a view refresh over every booth in six
    constituencies.
    """
    cursor.execute(
        "SELECT p.proname, p.provolatile FROM pg_proc p "
        "JOIN pg_namespace n ON n.oid = p.pronamespace "
        "WHERE n.nspname = 'public' AND p.proname LIKE 'metric\\_%'"
    )
    mutable = [r["proname"] for r in cursor.fetchall() if r["provolatile"] != "i"]
    assert mutable == [], f"not IMMUTABLE in the catalogue: {sorted(mutable)}"


# ---------------------------------------------------------------------------
# The three-way comparison
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("item", metric_cases.flat(), ids=metric_cases.case_id)
def test_sql_python_and_the_hand_computed_answer_all_agree(cursor, item):
    name, case = item
    sql_value = _comparable(_call_sql(cursor, name, case.sql_args))
    py_value = _comparable(case.python())

    if case.expected is None:
        assert sql_value is None, (
            f"{name} / {case.name}: SQL returned {sql_value!r} where the answer "
            "is unknown. A number here is the failure this file exists to catch."
        )
        assert py_value is None, f"{name} / {case.name}: Python returned {py_value!r}"
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
# The specific wrong answers the audit found, asserted as wrong
# ---------------------------------------------------------------------------


def test_the_margin_denominator_includes_nota(cursor):
    """D1, as a number rather than a description.

    Giridih 2024: a margin of 3,838 over 207,459 valid votes is 1.85 percent,
    which is what the ECI published. Over the NOTA-excluding 205,455 it is 1.87.
    Two hundredths of a point - small enough to read as rounding, large enough
    that no row in the old views reconciled against itself.
    """
    g = metric_cases.GIRIDIH_2024
    correct = _comparable(_call_sql(
        cursor, "metric_margin_pct",
        (g["jmm"], g["bjp"], 3, g["valid_votes"]),
    ))
    assert correct == pytest.approx(g["margin_pct"])

    cursor.execute(
        "SELECT metric_margin_pct(%s::BIGINT, %s::BIGINT, %s::BIGINT, "
        "%s::BIGINT) AS value",
        [g["jmm"], g["bjp"], 3, g["valid_votes"] - g["nota"]],
    )
    with_the_bug = float(cursor.fetchone()["value"])
    assert with_the_bug == pytest.approx(metric_cases.MARGIN_PCT_WITH_D1_DENOMINATOR)
    assert with_the_bug != pytest.approx(correct), (
        "the two denominators now give the same answer, so this test no longer "
        "distinguishes D1 from a correct implementation"
    )


def test_a_single_poll_type_does_not_produce_a_fifty_percent_floating_vote(cursor):
    """D4. With one poll every party's delta equals its whole share in that
    poll, the shares sum to 100, and half of 100 is 50 - so every booth in the
    constituency reported exactly 50.00, it reached the map and the priority
    score, and being uniform it flattened PERCENT_RANK so the score silently
    lost its floating-vote term."""
    one_poll = _call_sql(cursor, "metric_floating_pct", (100.0, 1))
    assert one_poll is None, (
        f"a single poll type gave {one_poll!r}; it must be NULL"
    )

    two_polls = _comparable(_call_sql(cursor, "metric_floating_pct", (100.0, 2)))
    assert two_polls == pytest.approx(metric_cases.FLOATING_PCT_WITH_D4_BUG), (
        "the arithmetic that produced the 50.00 has changed, so this test no "
        "longer demonstrates that the NULL rule is what prevents it"
    )


def test_an_uncontested_booth_has_no_margin_rather_than_a_margin_of_zero(cursor):
    """Zero would sort it as the tightest contest in the constituency, which is
    the top of the priority list."""
    assert _call_sql(cursor, "metric_margin_votes", (94_042, None, 1)) is None
    assert _call_sql(cursor, "metric_margin_pct", (94_042, None, 1, 207_459)) is None


def test_a_third_party_win_is_null_rather_than_the_ramp_midpoint(cursor):
    """F1. Zero sits at the diverging ramp's neutral midpoint, so a third-party
    win would render identically to a knife-edge contest between the pair."""
    value = _call_sql(
        cursor, "metric_signed_margin_pct",
        ("JLKM", 80_000, 79_000, 3, 207_459, "JMM", "BJP"),
    )
    assert value is None


# ---------------------------------------------------------------------------
# Parity at the view level, not only the function level
# ---------------------------------------------------------------------------


def test_the_wide_view_agrees_with_python_row_by_row(cursor):
    """The functions agreeing is necessary but not sufficient.

    A view can call the right function with the wrong arguments - the wrong
    denominator column, the runner-up from the wrong join - and every function
    test above still passes. So this reads `mv_result_booth_wide` back and
    recomputes each metric in Python from the same row's own inputs.

    It is the check that would have caught D1 in the old code, because the row
    carried both the NOTA-including total it displayed and the NOTA-excluding
    total it divided by.
    """
    from analytics import metrics
    from tests.e2e.conftest import refresh_views

    refresh_views(cursor)

    cursor.execute(
        "SELECT booth_uid, election_label, valid_votes, rejected, electors, "
        "       contestants, winner_party, winner_votes, runner_party, "
        "       runner_votes, contest_party_a, contest_party_b, votes_polled, "
        "       turnout_pct, margin_votes, margin_pct, signed_margin_pct "
        "FROM mv_result_booth_wide ORDER BY ac_id, election_id, booth_uid"
    )
    rows = cursor.fetchall()
    if not rows:
        pytest.skip(
            "mv_result_booth_wide is empty: the seed loads no results, so there "
            "is nothing to compare. This is not a pass - see item 9, the full "
            "per-AC seeds, and item 8, the mock Form 20 end to end."
        )

    for row in rows:
        where = f"{row['booth_uid']} {row['election_label']}"
        ranking = metrics.Ranking(
            winner=row["winner_party"],
            winner_votes=row["winner_votes"],
            runner_up=row["runner_party"],
            runner_up_votes=row["runner_votes"],
            contestants=row["contestants"] or 0,
        )

        assert _comparable(row["votes_polled"]) == metrics.votes_polled(
            row["valid_votes"], row["rejected"]), where

        assert _comparable(row["turnout_pct"]) == _approx_or_none(
            metrics.turnout_pct(row["votes_polled"], row["electors"])), where

        assert _comparable(row["margin_votes"]) == metrics.margin_votes(ranking), where

        assert _comparable(row["margin_pct"]) == _approx_or_none(
            metrics.margin_pct(ranking, row["valid_votes"])), where

        assert _comparable(row["signed_margin_pct"]) == _approx_or_none(
            metrics.signed_margin_pct(
                ranking, row["valid_votes"],
                row["contest_party_a"], row["contest_party_b"])), where


def test_the_ac_winner_agrees_with_the_booth_table(cursor):
    """N2. The constituency headline must name the candidate the booths elected.

    Summing `mv_result_booth_candidate` over every booth is what the AC winner
    *means*, so that sum is the reference. `mv_ac_summary` used to rank over
    party totals instead, where every independent shares a NULL `party_id` and
    collapses into one row - the same bucketing D3 removed at booth grain and
    left standing here, so the two grains could name different winners.
    """
    from tests.e2e.conftest import refresh_views

    refresh_views(cursor)

    cursor.execute(
        "SELECT ac_id, election_id, winner_party, winner_candidate, "
        "       winner_votes, runner_party, runner_candidate, runner_votes "
        "FROM mv_ac_summary ORDER BY ac_id, election_id"
    )
    summary = cursor.fetchall()
    if not summary:
        pytest.skip(
            "mv_ac_summary is empty: the seed loads no booth results, so there "
            "is no winner to check. Not a pass - see items 8 and 9."
        )

    for row in summary:
        cursor.execute(
            "SELECT contestant, candidate_name, SUM(votes)::INT AS votes "
            "FROM mv_result_booth_candidate "
            "WHERE ac_id = %s AND election_id = %s "
            "  AND party IS DISTINCT FROM 'NOTA' "
            "GROUP BY contestant, candidate_name "
            "ORDER BY votes DESC, candidate_name",
            (row["ac_id"], row["election_id"]),
        )
        from_booths = cursor.fetchall()
        if not from_booths:
            continue

        where = f"ac_id={row['ac_id']} election_id={row['election_id']}"
        assert row["winner_party"] == from_booths[0]["contestant"], (
            f"{where}: the AC summary names {row['winner_party']!r} as winner, "
            f"but summing the booth table gives "
            f"{from_booths[0]['contestant']!r} (N2)"
        )
        assert row["winner_candidate"] == from_booths[0]["candidate_name"], where
        assert row["winner_votes"] == from_booths[0]["votes"], where

        if len(from_booths) >= 2:
            assert row["runner_party"] == from_booths[1]["contestant"], where
            assert row["runner_candidate"] == from_booths[1]["candidate_name"], where
            assert row["runner_votes"] == from_booths[1]["votes"], where
        else:
            assert row["runner_party"] is None, (
                f"{where}: only one contestant, so there is no runner-up"
            )


def test_independents_are_not_bucketed_at_ac_grain(cursor):
    """The specific shape of N2 and D3, constructed rather than hoped for.

    The audit's example was eight independents on 500 votes each becoming a
    single 4,000-vote pseudo-party that outranked a real winner on 3,000.
    Ranked per candidate the real winner wins; bucketed, a seat is awarded to a
    composite nobody voted for.
    """
    cursor.execute(
        "SELECT contestant, candidate_name FROM mv_result_booth_candidate "
        "WHERE party = 'IND' OR party IS NULL "
        "GROUP BY contestant, candidate_name"
    )
    independents = cursor.fetchall()
    if len(independents) < 2:
        pytest.skip(
            f"the fixture has {len(independents)} independent(s); bucketing "
            "cannot be demonstrated with fewer than two. Item 8's mock Form 20 "
            "is specified to include independents, which is what will make this "
            "test meaningful."
        )

    # Each must be its own contestant. Were the key collapsing them, there
    # would be one row here however many candidates stood.
    keys = {row["contestant"] for row in independents}
    assert len(keys) == len(independents), (
        f"{len(independents)} independents share {len(keys)} contestant "
        "key(s); they are being bucketed"
    )


def _approx_or_none(value):
    """`pytest.approx(None)` compares by identity, which is not what is wanted
    when the expected value may legitimately be NULL."""
    return value if value is None else pytest.approx(value)
