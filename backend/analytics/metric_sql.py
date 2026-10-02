"""One canonical SQL expression per metric, and the generator that emits them.

Master prompt 3.2 says each formula must be implemented exactly once. It is
implemented twice - `analytics/metrics.py` in Python for the loaders and the
validator, `db/migrations/0015_metrics.sql` in SQL for everything the API reads -
because neither can do the other's job: the parsers must compute before a
transaction commits, and the views must aggregate over whole tables.

So "once" has to mean something weaker but still real: **the formula text lives
in one place**, here, and the SQL side is generated from it rather than written
alongside it.

Why not the arrangements the instruction literally asks for:

  * **SQL calling Python** needs `plpython3u`, an untrusted procedural language
    that a superuser must install and that no managed Postgres offers. It would
    also make every row of every view an interpreter call.
  * **Python calling SQL** would make `analytics/metrics.py` require a database,
    so the 60 unit tests that pin every NULL rule could not run, and the Form 20
    loader could not compute a metric mid-transaction without re-entering the
    connection it is already inside.
  * **Generating the whole migration** produces an unreviewable diff, which
    matters more for schema than for almost anything else.

What is here instead: the scalar expressions, and `generate_sql()`, which emits a
`CREATE FUNCTION` for each. That block lives inside `0015_metrics.sql` between
two markers, the views call the functions, and the parity test regenerates it and
fails if the migration has drifted. Change a formula here and the migration is
wrong until it is regenerated - which is the property that actually matters.

This is worth doing beyond tidiness. In the audited views the margin expression
`100.0 * (w.votes - ru.votes) / NULLIF(t.valid_votes, 0)` was written out three
times inside a single view - once for `margin_pct` and twice more in the signed
variant's CASE arms. Three copies of one formula is how D1 survived: fixing the
denominator in one arm and not the others is a one-character mistake that no test
distinguishes from correctness.

The aggregate metrics - swing's LAG, volatility's stddev_samp, the priority
percentile ranks - stay as view SQL, because a window function over a partition
has no scalar form. Those are covered by the parity harness rather than by
generation, and `AGGREGATE_METRICS` names them so the test can assert that every
3.2 metric is accounted for by one mechanism or the other.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# The marker pair delimiting the generated block inside the migration.
BEGIN_MARKER = "-- >>> GENERATED FROM analytics/metric_sql.py - DO NOT EDIT BY HAND"
END_MARKER = "-- <<< END GENERATED BLOCK"

# How wide a wrapped COMMENT literal may run before it is broken.
_COMMENT_WIDTH = 66


@dataclass(frozen=True)
class MetricFunction:
    """One scalar metric, as an immutable SQL function.

    `body` is a single SQL expression over the named arguments, and it is the
    canonical text. `metrics.py` implements the same rule in Python; the parity
    harness proves they agree on fixtures.
    """

    name: str
    args: tuple[tuple[str, str], ...]
    returns: str
    body: str
    doc: str
    python: str

    def signature(self) -> str:
        return ", ".join(f"{n} {t}" for n, t in self.args)

    def arg_types(self) -> str:
        return ", ".join(t for _, t in self.args)

    def to_sql(self) -> str:
        return (
            f"CREATE OR REPLACE FUNCTION {self.name}({self.signature()})\n"
            f"RETURNS {self.returns} AS $fn$\n"
            f"{self.body.strip()}\n"
            f"$fn$ LANGUAGE sql IMMUTABLE;\n"
            f"\n"
            f"COMMENT ON FUNCTION {self.name}({self.arg_types()}) IS\n"
            f"{_sql_literal(self.doc)};\n"
        )


def _sql_literal(text: str) -> str:
    """A wrapped, single-quoted SQL literal.

    Adjacent string literals concatenate in SQL, so a long comment can be broken
    across lines with no concatenation operator. Wrapped rather than emitted as
    one enormous line because the migration is meant to be read in a diff.
    """
    words = " ".join(text.split()).replace("'", "''").split(" ")
    lines: list[str] = []
    current = ""
    for word in words:
        if current and len(current) + len(word) + 1 > _COMMENT_WIDTH:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)

    out = []
    for i, line in enumerate(lines):
        # Every line but the last keeps a trailing space, so the concatenated
        # result reads as prose rather than running words together.
        trailing = "" if i == len(lines) - 1 else " "
        quoted = "'" + line + trailing + "'"
        out.append("    " + quoted)
    return "\n".join(out)


# ---------------------------------------------------------------------------
# The metrics, in the order of the 3.2 table.
#
# `python` names the callable in analytics/metrics.py implementing the same rule.
# The parity test uses it to pair the two sides up and fails if a name here does
# not resolve - so renaming or deleting a Python function without touching this
# file is caught rather than silently halving the coverage.
# ---------------------------------------------------------------------------

METRIC_FUNCTIONS: tuple[MetricFunction, ...] = (
    MetricFunction(
        name="metric_votes_polled",
        args=(("valid_votes", "BIGINT"), ("rejected", "BIGINT")),
        returns="BIGINT",
        body="SELECT CASE WHEN valid_votes IS NULL THEN NULL\n"
             "            ELSE valid_votes + COALESCE(rejected, 0) END",
        doc="valid_votes + rejected. Tendered votes are excluded: a tendered "
            "ballot is recorded separately and is not in the count. NULL when "
            "there is no result, but a missing rejected count is treated as "
            "zero, because Form 20 omits that column when it is zero.",
        python="votes_polled",
    ),
    MetricFunction(
        name="metric_turnout_pct",
        args=(("votes_polled", "BIGINT"), ("electors", "BIGINT")),
        returns="NUMERIC",
        body="SELECT ROUND((100.0 * votes_polled / NULLIF(electors, 0))::NUMERIC, 2)",
        doc="votes_polled / electors * 100. Electors come from the linked roll "
            "snapshot, falling back to the polling-station list, never from "
            "Form 20, which does not print them. NULL when electors are "
            "unknown - audit B4, where the column had no writer at all, so "
            "turnout was NULL for every booth while the map still offered it "
            "as a choosable metric.",
        python="turnout_pct",
    ),
    MetricFunction(
        name="metric_share_pct",
        args=(("candidate_votes", "BIGINT"), ("valid_votes", "BIGINT")),
        returns="NUMERIC",
        body="SELECT ROUND((100.0 * candidate_votes "
             "/ NULLIF(valid_votes, 0))::NUMERIC, 2)",
        doc="candidate_votes / valid_votes * 100, with NOTA inside the "
            "denominator, because that is what Form 20 prints as total valid "
            "votes and what the published margin percentage divides by.",
        python="share_pct",
    ),
    MetricFunction(
        name="metric_margin_votes",
        args=(("winner_votes", "BIGINT"), ("runner_votes", "BIGINT"), ("contestants", "BIGINT")),
        returns="BIGINT",
        body="SELECT CASE WHEN contestants >= 2\n"
             "            THEN winner_votes - runner_votes END",
        doc="Winner minus runner-up, both real candidates - NOTA never ranks. "
            "NULL with fewer than two contestants, and never zero: an "
            "uncontested booth has no margin, and zero would sort it as the "
            "tightest contest in the constituency.",
        python="margin_votes",
    ),
    MetricFunction(
        name="metric_margin_pct",
        args=(("winner_votes", "BIGINT"), ("runner_votes", "BIGINT"),
              ("contestants", "BIGINT"), ("valid_votes", "BIGINT")),
        returns="NUMERIC",
        body="SELECT ROUND((100.0 * metric_margin_votes(winner_votes, runner_votes,\n"
             "                                          contestants)\n"
             "              / NULLIF(valid_votes, 0))::NUMERIC, 2)",
        doc="margin_votes / valid_votes * 100. Audit D1: the old views divided "
            "by a NOTA-excluding total while displaying the NOTA-including one "
            "in the same row, giving 1.87 percent for Giridih 2024 against the "
            "published 1.85, and making the row impossible to reconcile "
            "against itself. Calls metric_margin_votes rather than repeating "
            "the subtraction, so the fewer-than-two rule cannot be fixed in "
            "one place and missed in another.",
        python="margin_pct",
    ),
    MetricFunction(
        name="metric_signed_margin_pct",
        args=(("winner_party", "TEXT"), ("winner_votes", "BIGINT"), ("runner_votes", "BIGINT"),
              ("contestants", "BIGINT"), ("valid_votes", "BIGINT"),
              ("party_a", "TEXT"), ("party_b", "TEXT")),
        returns="NUMERIC",
        body="SELECT CASE\n"
             "    WHEN winner_party IS NULL THEN NULL\n"
             "    WHEN winner_party = party_a\n"
             "        THEN metric_margin_pct(winner_votes, runner_votes,\n"
             "                               contestants, valid_votes)\n"
             "    WHEN winner_party = party_b\n"
             "        THEN -metric_margin_pct(winner_votes, runner_votes,\n"
             "                                contestants, valid_votes)\n"
             "END",
        doc="Plus margin_pct if the contest pair first party won, minus if the "
            "second, NULL if neither did. Drives the diverging colour ramp on "
            "the map. NULL rather than zero for a third-party win, because "
            "zero sits at the ramp neutral midpoint - visually identical to a "
            "knife-edge contest between the pair, which is the opposite of "
            "what happened (F1). The pair comes from ac_contest, per AC per "
            "event, so no view hardcodes JMM against BJP.",
        python="signed_margin_pct",
    ),
    MetricFunction(
        name="metric_comparison_allowed",
        args=(("crosswalk_confidence", "REAL"), ("crosswalk_reviewed", "BOOLEAN"),
              ("lineage_kind", "TEXT"), ("lineage_aggregated", "BOOLEAN")),
        returns="BOOLEAN",
        body="SELECT (COALESCE(lineage_kind, '') NOT IN ('split', 'merge')\n"
             "        OR COALESCE(lineage_aggregated, false))\n"
             "       AND (COALESCE(crosswalk_reviewed, false)\n"
             "            OR COALESCE(crosswalk_confidence, 0) >= 0.85)",
        doc="Whether this booth may be compared against its own past at all. "
            "Two conditions, each of which makes a difference rather than a "
            "number: a station matched in the 0.65-0.85 confidence band has a "
            "crosswalk row so the booth still appears in rollups (B2 - it used "
            "to have none, and every view inner-joined that table, so its "
            "votes vanished silently), but it carries no comparison until a "
            "human reviews the match; and a booth that split or merged cannot "
            "be compared station to station, because half an electorate "
            "against the whole of last time is not a swing - unless the "
            "lineage group has been aggregated back together, which is what "
            "lineage_aggregated records. Every view currently passes false "
            "there, because nothing in the schema aggregates lineage groups "
            "yet; the argument exists so that the SQL signature matches "
            "metrics.swing_pct rather than quietly being stricter than it. "
            "Extracted as its own function because swing share, swing votes "
            "and the vanished-party rows all need the identical gate, and "
            "three copies of a two-clause rule is how one copy ends up with a "
            "different threshold from the others.",
        python="comparison_allowed",
    ),
    MetricFunction(
        name="metric_swing_pct",
        args=(("share_now", "NUMERIC"), ("share_prev", "NUMERIC"),
              ("crosswalk_confidence", "REAL"), ("crosswalk_reviewed", "BOOLEAN"),
              ("lineage_kind", "TEXT"), ("lineage_aggregated", "BOOLEAN")),
        returns="NUMERIC",
        body="SELECT CASE\n"
             "    WHEN share_now IS NULL OR share_prev IS NULL THEN NULL\n"
             "    WHEN NOT metric_comparison_allowed(crosswalk_confidence,\n"
             "                                       crosswalk_reviewed,\n"
             "                                       lineage_kind,\n"
             "                                       lineage_aggregated) THEN NULL\n"
             "    ELSE ROUND((share_now - share_prev)::NUMERIC, 2)\n"
             "END",
        doc="share_now - share_prev for the same election type and the same "
            "booth. NULL when there is no prior election - audit D2, where a "
            "COALESCE to zero made the earliest loaded election report every "
            "party entire vote share as its swing, a fabricated plus 38.3 "
            "points - or when the crosswalk is weak and unreviewed, or when "
            "the booth split or merged and the lineage group has not been "
            "aggregated. A party that contested and polled nothing has "
            "share_prev of zero and a real swing; the distinction between "
            "that and absence is the whole point.",
        python="swing_pct",
    ),
    MetricFunction(
        name="metric_new_voter_pct",
        args=(("additions", "BIGINT"), ("electors_at_end", "BIGINT")),
        returns="NUMERIC",
        body="SELECT CASE WHEN additions IS NOT NULL AND electors_at_end > 0\n"
             "            THEN ROUND((100.0 * additions\n"
             "                        / electors_at_end)::NUMERIC, 2) END",
        doc="Additions in the window over electors at window end, times 100. "
            "The window runs from the roll linked to the previous general "
            "election to the roll linked to the target one. NULL when either "
            "roll link is missing - audit B1, where an empty baseline CTE gave "
            "every booth zero additions while a different screen read "
            "roll_change directly and showed the real numbers, so the system "
            "disagreed with itself on the same page load.",
        python="new_voter_pct",
    ),
    MetricFunction(
        name="metric_net_roll_change_pct",
        args=(("additions", "BIGINT"), ("deletions", "BIGINT"), ("electors_at_start", "BIGINT")),
        returns="NUMERIC",
        body="SELECT CASE WHEN additions IS NOT NULL AND deletions IS NOT NULL\n"
             "                 AND electors_at_start > 0\n"
             "            THEN ROUND((100.0 * (additions - deletions)\n"
             "                        / electors_at_start)::NUMERIC, 2) END",
        doc="(additions - deletions) over electors at window start, times 100. "
            "Can be negative, which is the post-revision case and a real "
            "finding rather than an error to clamp away. NULL when either roll "
            "is missing.",
        python="net_roll_change_pct",
    ),
    MetricFunction(
        name="metric_transfer_delta",
        args=(("share_vs", "NUMERIC"), ("share_ls", "NUMERIC")),
        returns="NUMERIC",
        body="SELECT CASE WHEN share_vs IS NOT NULL AND share_ls IS NOT NULL\n"
             "            THEN ROUND((share_vs - share_ls)::NUMERIC, 2) END",
        doc="share in the assembly poll minus share in the parliamentary poll, "
            "one party, same year, same booth. NULL when either leg is "
            "missing, which is most booths in most years.",
        python="transfer_delta",
    ),
    MetricFunction(
        name="metric_floating_pct",
        args=(("abs_delta_sum", "NUMERIC"), ("poll_types", "BIGINT")),
        returns="NUMERIC",
        body="SELECT CASE WHEN poll_types = 2\n"
             "            THEN ROUND((abs_delta_sum / 2.0)::NUMERIC, 2) END",
        doc="The Pedersen index: half the sum of absolute share changes "
            "between the parliamentary and assembly polls of the same year. "
            "The caller supplies the summed absolute deltas because that sum "
            "is an aggregate; this function owns the halving and the NULL "
            "rule, which is where the bug was. NULL when only one poll type "
            "exists - audit D4, and the most consequential NULL rule here. A "
            "Pedersen index over a single poll is exactly 100 over 2, so every "
            "booth in the constituency reported 50.00 percent, it appeared on "
            "the map and inside the priority score, it looked like a finding, "
            "and because it was uniform PERCENT_RANK flattened it to a "
            "constant and the priority score silently lost that term "
            "altogether.",
        python="floating_pct",
    ),
    MetricFunction(
        name="metric_volatility",
        args=(("signed_margins", "NUMERIC[]"),),
        returns="NUMERIC",
        # A bare NULL has unknown type and array_remove is polymorphic
        # (anyarray, anyelement), so the cast is what lets the call resolve.
        # array_remove does strip NULLs when the search value is NULL - it
        # takes that branch explicitly rather than comparing, which equality
        # against NULL could never satisfy.
        body="SELECT CASE WHEN cardinality(\n"
             "                 array_remove(signed_margins, NULL::NUMERIC)) >= 2\n"
             "            THEN ROUND(stddev_samp(v)::NUMERIC, 2) END\n"
             "  FROM unnest(array_remove(signed_margins, NULL::NUMERIC)) AS v",
        doc="Sample standard deviation of the signed margin across the "
            "elections of one type in one booth. Sample, not population: "
            "these are the elections that happened, treated as a sample of "
            "the booth behaviour, and with two observations the population "
            "form understates the spread by a factor of root two. NULL with "
            "fewer than two elections, because a single election has no "
            "variability and zero would rank that booth as the most stable "
            "in the constituency (D6).",
        python="volatility",
    ),
    MetricFunction(
        name="metric_priority_weight",
        args=(("closeness", "DOUBLE PRECISION"), ("new_voter", "DOUBLE PRECISION"),
              ("floating", "DOUBLE PRECISION"), ("volatility", "DOUBLE PRECISION")),
        returns="NUMERIC",
        body="SELECT (CASE WHEN closeness  IS NULL THEN 0 ELSE 0.35 END\n"
             "        + CASE WHEN new_voter  IS NULL THEN 0 ELSE 0.25 END\n"
             "        + CASE WHEN floating   IS NULL THEN 0 ELSE 0.20 END\n"
             "        + CASE WHEN volatility IS NULL THEN 0 ELSE 0.20 END)::NUMERIC",
        doc="How much of the priority weight was actually available. Reported "
            "beside the score so that a score renormalised over part of the "
            "weight is identifiable, rather than looking like the full "
            "four-factor ranking. Two of the four inputs were constant in the "
            "audited system, so a number presented as a four-factor priority "
            "was really a two-factor one with nothing on the page to say so.",
        # No separate Python function: `priority_score` already returns the
        # available weight as `PriorityScore.weight_used`, computed once for
        # both purposes. Splitting it in Python to mirror the SQL split would
        # add a second place for the weights to live, which is the opposite of
        # the point.
        python="priority_score",
    ),
    MetricFunction(
        name="metric_priority_score",
        args=(("closeness", "DOUBLE PRECISION"), ("new_voter", "DOUBLE PRECISION"),
              ("floating", "DOUBLE PRECISION"), ("volatility", "DOUBLE PRECISION")),
        returns="NUMERIC",
        body="SELECT CASE\n"
             "    WHEN metric_priority_weight(closeness, new_voter, floating,\n"
             "                                volatility) > 0\n"
             "    THEN ROUND(((COALESCE(0.35 * closeness, 0)\n"
             "                 + COALESCE(0.25 * new_voter, 0)\n"
             "                 + COALESCE(0.20 * floating, 0)\n"
             "                 + COALESCE(0.20 * volatility, 0))\n"
             "                / metric_priority_weight(closeness, new_voter,\n"
             "                                         floating, volatility)\n"
             "               )::NUMERIC, 4)\n"
             "END",
        doc="Weighted percentile ranks: 0.35 closeness, 0.25 new voters, 0.20 "
            "floating vote, 0.20 volatility, each rank computed within one AC "
            "so that constituencies of different competitiveness do not "
            "dominate a shared ranking. Missing inputs are dropped and the "
            "remaining weights renormalised by metric_priority_weight. NULL "
            "when every input is missing, because averaging nothing is not a "
            "priority of zero.",
        python="priority_score",
    ),
)

# The 3.2 metrics with no scalar form, and the view that owns each. Named here so
# the parity test can assert that every metric in the table is covered either by
# generation or by the aggregate harness, and that none has fallen between them.
AGGREGATE_METRICS: dict[str, str] = {
    "swing_pct_window": "mv_swing - LAG over (booth, party, election type) by year",
    "floating_pct_sum": "mv_floating_vote - SUM of absolute deltas before halving",
    "volatility_group": "mv_volatility - stddev_samp over a booth election history",
    "closeness_rank": "mv_booth_priority - 1 - PERCENT_RANK of margin_pct within ac_id",
    "new_voter_rank": "mv_booth_priority - PERCENT_RANK of new_voter_pct within ac_id",
    "floating_rank": "mv_booth_priority - PERCENT_RANK of floating_pct within ac_id",
    "volatility_rank": "mv_booth_priority - PERCENT_RANK of volatility within ac_id",
    "percentile_ranks": "PERCENT_RANK semantics: the lowest value ranks 0.0, n-1 divisor",
}


def generate_sql() -> str:
    """The generated block, byte for byte as it appears in the migration."""
    parts = [
        BEGIN_MARKER,
        "--",
        "-- Regenerate with:  python -m analytics.metric_sql",
        "--",
        "-- These functions are the canonical SQL form of the master prompt 3.2",
        "-- metrics. The views below call them instead of restating the formulas,",
        "-- and analytics/metrics.py implements the same rules in Python for the",
        "-- loaders and the validator. tests/test_metric_parity.py regenerates this",
        "-- block and fails if it has drifted, then runs both implementations over",
        "-- identical fixtures.",
        "--",
        "-- IMMUTABLE, so they can appear in index expressions and so the planner",
        "-- may inline them into the surrounding view - which is what makes a",
        "-- function call per row acceptable inside a materialized view refresh.",
        "",
    ]
    parts.extend(fn.to_sql() for fn in METRIC_FUNCTIONS)
    parts.append(END_MARKER)
    return "\n".join(parts).rstrip() + "\n"


def migration_path() -> Path:
    return Path(__file__).resolve().parents[1] / "db" / "migrations" / "0015_metrics.sql"


def rewrite_migration(path: Path | None = None) -> bool:
    """Replace the generated block in the migration. True if the file changed."""
    p = Path(path) if path is not None else migration_path()
    text = p.read_text(encoding="utf-8")
    if BEGIN_MARKER not in text or END_MARKER not in text:
        raise ValueError(
            f"{p.name} has no generated block. Put these two marker lines where "
            f"the functions should live:\n  {BEGIN_MARKER}\n  {END_MARKER}"
        )
    start = text.index(BEGIN_MARKER)
    end = text.index(END_MARKER) + len(END_MARKER)
    updated = text[:start] + generate_sql().rstrip() + text[end:]
    if updated == text:
        return False
    p.write_text(updated, encoding="utf-8", newline="\n")
    return True


def main() -> int:
    target = migration_path()
    changed = rewrite_migration(target)
    print(f"{target.name}: {'rewritten' if changed else 'already current'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
