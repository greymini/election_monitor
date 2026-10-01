"""Hand-computed cases for every master prompt 3.2 metric.

Shared by two test files that check different things against the same numbers:

  * `tests/test_metric_parity.py` runs the **Python** side and asserts it
    matches `expected`. No database, so it runs in the default suite.
  * `tests/e2e/test_metric_parity_sql.py` runs the **SQL** function and asserts
    it matches `expected` *and* matches the Python result.

The point of the third column is that it is worked out by hand, on paper, from
the definition in the master prompt - not read off either implementation. Two
implementations agreeing proves they are consistent; it does not prove they are
right, and the failure mode this project keeps hitting is a formula that is
wrong in a plausible, self-consistent way. `expected` is what makes the parity
test a correctness test rather than a diff.

Each case carries the SQL argument tuple in declared parameter order and a
zero-argument callable for the Python side, because the two signatures are not
the same shape: SQL takes scalars, while Python takes a `Ranking` for the margin
family and share mappings for the Pedersen index. Writing both out per case is
deliberate - an adapter that derived one from the other could satisfy the test
while disagreeing about what the inputs mean.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from analytics import metrics
from analytics.metrics import CrosswalkLink, PriorityInputs, Ranking
from fixtures import giridih

# The Giridih 2024 assembly result, from the one fixture source.
#
# Imported rather than restated. These constants used to live here as their own
# copy, and said a different valid-vote total from the one the frontend
# two fixtures for one constituency, disagreeing, with no test able to notice
# because both round the margin to the published 1.85%. That was finding N8, and
# the fix is that there is now one of them: `fixtures/giridih.py`, which
# `scripts/generate_fixtures.py` also emits the frontend's copy from.
#
# `valid_votes` is 207,598, the frontend's figure. Neither value is verified
# against a document; this is a choice of which unverified number to use
# consistently, not a determination of which is right, and it must be checked
# when a Form 20 exists.
GIRIDIH_2024 = {
    "jmm": giridih.PARTY_TOTALS["jmm"],
    "bjp": giridih.PARTY_TOTALS["bjp"],
    "jlkm": giridih.PARTY_TOTALS["jlkm"],
    "nota": giridih.PARTY_TOTALS["nota"],
    "valid_votes": giridih.VALID_VOTES,
    "margin_votes": giridih.MARGIN_VOTES,
    "margin_pct": giridih.ac_totals()["margin_pct"],
    "electors": giridih.ELECTORS,
    "turnout_pct": giridih.ac_totals()["turnout_pct"],
    "jmm_share_pct": giridih.ac_totals()["jmm_share_pct"],
    "nota_share_pct": giridih.ac_totals()["nota_share_pct"],
}


@dataclass(frozen=True)
class Case:
    """One metric evaluation, with the answer worked out independently."""

    name: str
    sql_args: tuple[Any, ...]
    expected: Any
    python: Callable[[], Any]


# ---------------------------------------------------------------------------
# votes_polled
# ---------------------------------------------------------------------------

VOTES_POLLED = [
    Case("valid plus rejected", (94_042, 12), 94_054,
         lambda: metrics.votes_polled(94_042, 12)),
    # Form 20 omits the rejected column when it is zero, so absent means zero
    # here and only here. Everywhere else in this file absent means NULL.
    Case("a missing rejected count counts as zero", (94_042, None), 94_042,
         lambda: metrics.votes_polled(94_042, None)),
    Case("no result at all is NULL", (None, 12), None,
         lambda: metrics.votes_polled(None, 12)),
    Case("a genuine zero is zero", (0, 0), 0,
         lambda: metrics.votes_polled(0, 0)),
]

# ---------------------------------------------------------------------------
# turnout_pct
# ---------------------------------------------------------------------------

# 207598 / 304898 = 0.6808..., so 68.09 to two places.
TURNOUT_PCT = [
    Case("round arithmetic", (1_000, 2_000), 50.0,
         lambda: metrics.turnout_pct(1_000, 2_000)),
    Case("Giridih 2024 polled against electors",
         (giridih.VALID_VOTES + giridih.REJECTED, GIRIDIH_2024["electors"]),
         GIRIDIH_2024["turnout_pct"],
         lambda: metrics.turnout_pct(
             giridih.VALID_VOTES + giridih.REJECTED, GIRIDIH_2024["electors"])),
    # B4: the electors column had no writer, so this was the real case for
    # every booth in the system while the map still offered turnout as a metric.
    Case("unknown electors is NULL, not zero", (1_000, None), None,
         lambda: metrics.turnout_pct(1_000, None)),
    Case("zero electors is NULL, not a division error", (1_000, 0), None,
         lambda: metrics.turnout_pct(1_000, 0)),
    Case("no votes recorded is NULL", (None, 2_000), None,
         lambda: metrics.turnout_pct(None, 2_000)),
]

# ---------------------------------------------------------------------------
# share_pct
# ---------------------------------------------------------------------------

# Computed from the shared totals rather than written out, so a change to the
# fixture cannot leave a stale expectation here. 94,042 / 207,598 = 45.30%.
SHARE_PCT = [
    Case("JMM share of valid votes including NOTA",
         (GIRIDIH_2024["jmm"], GIRIDIH_2024["valid_votes"]),
         GIRIDIH_2024["jmm_share_pct"],
         lambda: metrics.share_pct(GIRIDIH_2024["jmm"], GIRIDIH_2024["valid_votes"])),
    Case("NOTA has a share like any other line on the form",
         (GIRIDIH_2024["nota"], GIRIDIH_2024["valid_votes"]),
         GIRIDIH_2024["nota_share_pct"],
         lambda: metrics.share_pct(GIRIDIH_2024["nota"], GIRIDIH_2024["valid_votes"])),
    Case("round arithmetic", (250, 1_000), 25.0,
         lambda: metrics.share_pct(250, 1_000)),
    Case("zero valid votes is NULL", (100, 0), None,
         lambda: metrics.share_pct(100, 0)),
    Case("no votes recorded is NULL", (None, 1_000), None,
         lambda: metrics.share_pct(None, 1_000)),
]

# ---------------------------------------------------------------------------
# margin_votes and margin_pct
# ---------------------------------------------------------------------------

_GIRIDIH_RANKING = Ranking(
    winner="JMM", winner_votes=GIRIDIH_2024["jmm"],
    runner_up="BJP", runner_up_votes=GIRIDIH_2024["bjp"],
    contestants=3,
)
_UNCONTESTED = Ranking(winner="JMM", winner_votes=94_042,
                       runner_up=None, runner_up_votes=None, contestants=1)

MARGIN_VOTES = [
    Case("Giridih 2024 margin",
         (GIRIDIH_2024["jmm"], GIRIDIH_2024["bjp"], 3), GIRIDIH_2024["margin_votes"],
         lambda: metrics.margin_votes(_GIRIDIH_RANKING)),
    # Never 0. Zero would sort an uncontested booth as the tightest contest in
    # the constituency, which is the top of the priority list.
    Case("one contestant has no margin, not a margin of zero",
         (94_042, None, 1), None,
         lambda: metrics.margin_votes(_UNCONTESTED)),
    Case("no winner recorded is NULL", (None, 90_204, 3), None,
         lambda: metrics.margin_votes(
             Ranking(None, None, "BJP", 90_204, 3))),
]

# 3838 / 207598 = 0.018488... -> 1.85, which is the published figure.
MARGIN_PCT = [
    Case("Giridih 2024 reconciles to the published 1.85 percent",
         (GIRIDIH_2024["jmm"], GIRIDIH_2024["bjp"], 3, GIRIDIH_2024["valid_votes"]),
         GIRIDIH_2024["margin_pct"],
         lambda: metrics.margin_pct(_GIRIDIH_RANKING, GIRIDIH_2024["valid_votes"])),
    Case("round arithmetic", (5_000, 4_000, 3, 100_000), 1.0,
         lambda: metrics.margin_pct(
             Ranking("A", 5_000, "B", 4_000, 3), 100_000)),
    Case("one contestant is NULL", (5_000, 4_000, 1, 100_000), None,
         lambda: metrics.margin_pct(
             Ranking("A", 5_000, "B", 4_000, 1), 100_000)),
    Case("zero valid votes is NULL", (5_000, 4_000, 3, 0), None,
         lambda: metrics.margin_pct(
             Ranking("A", 5_000, "B", 4_000, 3), 0)),
]

# D1 in one line. The old views divided by a NOTA-excluding total while showing
# the NOTA-including one in the same row: 207598 - 2004 = 205594, and
# 3838 / 205594 = 0.018668 -> 1.87 against the published 1.85. The number was
# wrong by two hundredths of a point, which is small enough to look like
# rounding and large enough that no row reconciled against itself.
MARGIN_PCT_WITH_D1_DENOMINATOR = round(
    100.0 * GIRIDIH_2024["margin_votes"]
    / (GIRIDIH_2024["valid_votes"] - GIRIDIH_2024["nota"]), 2
)

# ---------------------------------------------------------------------------
# signed_margin_pct
# ---------------------------------------------------------------------------

SIGNED_MARGIN_PCT = [
    Case("the pair's first party won, so positive",
         ("JMM", GIRIDIH_2024["jmm"], GIRIDIH_2024["bjp"], 3,
          GIRIDIH_2024["valid_votes"], "JMM", "BJP"),
         1.85,
         lambda: metrics.signed_margin_pct(
             _GIRIDIH_RANKING, GIRIDIH_2024["valid_votes"], "JMM", "BJP")),
    # The same contest with the result the other way round: BJP takes the
    # winner's 94,042 and JMM the runner-up's 90,204, so the margin is the same
    # 3,838 and only the sign changes. Writing it with BJP on 90,204 would make
    # the winner poll fewer votes than the runner-up, and the two sign flips -
    # a negative margin, then negated again for party_b - would cancel and give
    # +1.85, which is how this case first read.
    Case("the pair's second party won, so negative",
         ("BJP", GIRIDIH_2024["jmm"], GIRIDIH_2024["bjp"], 3,
          GIRIDIH_2024["valid_votes"], "JMM", "BJP"),
         -1.85,
         lambda: metrics.signed_margin_pct(
             Ranking("BJP", GIRIDIH_2024["jmm"], "JMM", GIRIDIH_2024["bjp"], 3),
             GIRIDIH_2024["valid_votes"], "JMM", "BJP")),
    # F1: not zero. Zero is the ramp's neutral midpoint, so a third-party win
    # would render identically to a knife-edge contest between the pair.
    Case("a third party won, so NULL rather than the ramp midpoint",
         ("JLKM", 80_000, 79_000, 3, GIRIDIH_2024["valid_votes"], "JMM", "BJP"),
         None,
         lambda: metrics.signed_margin_pct(
             Ranking("JLKM", 80_000, "BJP", 79_000, 3),
             GIRIDIH_2024["valid_votes"], "JMM", "BJP")),
    Case("no winner is NULL",
         (None, None, 90_204, 3, GIRIDIH_2024["valid_votes"], "JMM", "BJP"),
         None,
         lambda: metrics.signed_margin_pct(
             Ranking(None, None, "BJP", 90_204, 3),
             GIRIDIH_2024["valid_votes"], "JMM", "BJP")),
]

# ---------------------------------------------------------------------------
# comparison_allowed - the gate shared by swing share, swing votes and the
# vanished-party rows.
#
# The absent-crosswalk case is in here now. It used to be excluded: a NULL
# confidence meant "cannot compare" in SQL while `link=None` meant "nothing to
# gate on" in Python, so the two sides gave opposite answers for the input most
# likely to occur in practice, and the divergence was documented rather than
# fixed. That was N1, and it is closed.
# ---------------------------------------------------------------------------

COMPARISON_ALLOWED = [
    # N1. In SQL a NULL confidence is a LEFT JOIN that found no booth_crosswalk
    # row; in Python it is an explicit None. Either way the booth cannot be
    # shown to be the station it was, so there is nothing to compare against.
    Case("no crosswalk row at all means no comparison",
         (None, None, None, False), False,
         lambda: metrics.comparison_allowed(None)),
    Case("an anchor is the same station, so it compares",
         (1.0, True, None, False), True,
         lambda: metrics.comparison_allowed(metrics.ANCHOR)),
    Case("a strong match carries a comparison", (0.95, False, None, False), True,
         lambda: metrics.comparison_allowed(CrosswalkLink(0.95, False))),
    Case("the auto-accept boundary is inclusive", (0.85, False, None, False), True,
         lambda: metrics.comparison_allowed(CrosswalkLink(0.85, False))),
    # B2: these stations used to get no crosswalk row at all, and every view
    # inner-joined that table, so their votes vanished from every rollup.
    Case("a review-band match does not, until reviewed",
         (0.70, False, None, False), False,
         lambda: metrics.comparison_allowed(CrosswalkLink(0.70, False))),
    Case("a human review admits it", (0.70, True, None, False), True,
         lambda: metrics.comparison_allowed(CrosswalkLink(0.70, True))),
    Case("a split booth cannot be compared station to station",
         (0.95, False, "split", False), False,
         lambda: metrics.comparison_allowed(CrosswalkLink(0.95, False), "split")),
    Case("a merged booth likewise", (0.95, False, "merge", False), False,
         lambda: metrics.comparison_allowed(CrosswalkLink(0.95, False), "merge")),
    Case("unless the lineage group has been aggregated back together",
         (0.95, False, "split", True), True,
         lambda: metrics.comparison_allowed(
             CrosswalkLink(0.95, False), "split", True)),
    Case("a new booth is not a split, so it is not gated on lineage",
         (0.95, False, "new", False), True,
         lambda: metrics.comparison_allowed(CrosswalkLink(0.95, False), "new")),
]

# ---------------------------------------------------------------------------
# swing_pct
# ---------------------------------------------------------------------------

SWING_PCT = [
    Case("a fall", (45.3, 48.2, 0.95, False, None, False), -2.9,
         lambda: metrics.swing_pct(45.3, 48.2, CrosswalkLink(0.95, False))),
    Case("a rise", (43.45, 38.7, 0.95, False, None, False), 4.75,
         lambda: metrics.swing_pct(43.45, 38.7, CrosswalkLink(0.95, False))),
    # D2: the old view COALESCEd a missing LAG to 0, so the earliest loaded
    # election reported every party's whole vote share as its swing - a
    # fabricated +38.3 point swing at every booth in VS-2014.
    Case("no prior election is NULL, not the whole share",
         (38.3, None, 0.95, False, None, False), None,
         lambda: metrics.swing_pct(38.3, None, CrosswalkLink(0.95, False))),
    # The distinction D2 destroyed: 0.0 means contested and polled nothing,
    # None means did not contest or not loaded. They are different facts.
    Case("a party that contested and polled nothing has a real swing",
         (5.0, 0.0, 0.95, False, None, False), 5.0,
         lambda: metrics.swing_pct(5.0, 0.0, CrosswalkLink(0.95, False))),
    Case("a weak unreviewed crosswalk carries no swing",
         (45.0, 40.0, 0.70, False, None, False), None,
         lambda: metrics.swing_pct(45.0, 40.0, CrosswalkLink(0.70, False))),
    Case("a reviewed weak crosswalk does",
         (45.0, 40.0, 0.70, True, None, False), 5.0,
         lambda: metrics.swing_pct(45.0, 40.0, CrosswalkLink(0.70, True))),
    Case("a split booth carries no swing",
         (45.0, 40.0, 0.95, False, "split", False), None,
         lambda: metrics.swing_pct(45.0, 40.0, CrosswalkLink(0.95, False), "split")),
    # N1 at the swing level: this returned 5.0 in Python and NULL in SQL.
    Case("no crosswalk row carries no swing",
         (45.0, 40.0, None, None, None, False), None,
         lambda: metrics.swing_pct(45.0, 40.0, None)),
]

# ---------------------------------------------------------------------------
# new_voter_pct and net_roll_change_pct
# ---------------------------------------------------------------------------

NEW_VOTER_PCT = [
    Case("round arithmetic", (1_200, 100_000), 1.2,
         lambda: metrics.new_voter_pct(1_200, 100_000)),
    # B1: an empty baseline CTE gave every booth zero additions, while a
    # different screen read roll_change directly and showed the real numbers.
    Case("no roll link is NULL, not zero additions", (None, 100_000), None,
         lambda: metrics.new_voter_pct(None, 100_000)),
    Case("no electors at window end is NULL", (1_200, 0), None,
         lambda: metrics.new_voter_pct(1_200, 0)),
    Case("a genuine zero additions is zero", (0, 100_000), 0.0,
         lambda: metrics.new_voter_pct(0, 100_000)),
]

NET_ROLL_CHANGE_PCT = [
    Case("more additions than deletions", (1_500, 1_200, 100_000), 0.3,
         lambda: metrics.net_roll_change_pct(1_500, 1_200, 100_000)),
    # Negative is the post-revision case and a real finding, not an error.
    Case("more deletions than additions is negative",
         (1_200, 1_500, 100_000), -0.3,
         lambda: metrics.net_roll_change_pct(1_200, 1_500, 100_000)),
    Case("a missing deletion count is NULL", (1_200, None, 100_000), None,
         lambda: metrics.net_roll_change_pct(1_200, None, 100_000)),
    Case("no electors at window start is NULL", (1_200, 1_500, 0), None,
         lambda: metrics.net_roll_change_pct(1_200, 1_500, 0)),
]

# ---------------------------------------------------------------------------
# transfer_delta
# ---------------------------------------------------------------------------

TRANSFER_DELTA = [
    Case("the party did better in the assembly poll", (47.0, 40.0), 7.0,
         lambda: metrics.transfer_delta(47.0, 40.0)),
    Case("and worse", (40.0, 47.0), -7.0,
         lambda: metrics.transfer_delta(40.0, 47.0)),
    Case("a missing assembly leg is NULL", (None, 40.0), None,
         lambda: metrics.transfer_delta(None, 40.0)),
    Case("a missing parliamentary leg is NULL", (47.0, None), None,
         lambda: metrics.transfer_delta(47.0, None)),
]

# ---------------------------------------------------------------------------
# floating_pct - the Pedersen index
# ---------------------------------------------------------------------------

_LS_SHARES = {"JMM": 40.0, "BJP": 45.0, "OTH": 15.0}
_VS_SHARES = {"JMM": 47.0, "BJP": 43.0, "OTH": 10.0}
# |47-40| + |43-45| + |10-15| = 7 + 2 + 5 = 14, halved -> 7.0
_ABS_DELTA_SUM = 14.0

FLOATING_PCT = [
    Case("both polls present", (_ABS_DELTA_SUM, 2), 7.0,
         lambda: metrics.floating_pct(_LS_SHARES, _VS_SHARES)),
    Case("no movement at all is a real zero", (0.0, 2), 0.0,
         lambda: metrics.floating_pct(_LS_SHARES, _LS_SHARES)),
    # D4, the most consequential NULL rule in the file. With one poll every
    # party's delta equals its whole share in that poll, the shares sum to 100,
    # and the index comes to exactly 100/2 - so every booth in the constituency
    # reported 50.00, it reached the map and the priority score, it looked like
    # a finding, and being uniform it flattened PERCENT_RANK so the priority
    # score silently lost its 0.20 floating-vote term.
    Case("one poll type only is NULL, not fifty percent", (100.0, 1), None,
         lambda: metrics.floating_pct({}, _VS_SHARES)),
]

# What the D4 bug produced from the same inputs, kept as a number so the test
# can assert the current code does not return it.
FLOATING_PCT_WITH_D4_BUG = 50.0

# ---------------------------------------------------------------------------
# volatility
# ---------------------------------------------------------------------------

# [1.85, -3.2, 5.6]: mean 1.416667, squared deviations 0.187778 + 21.313611 +
# 17.500278 = 39.001667, divided by n-1 = 2 gives 19.500833, square root
# 4.415975 -> 4.42. Sample, not population: dividing by 3 would give 3.606.
VOLATILITY = [
    Case("three elections", ([1.85, -3.2, 5.6],), 4.42,
         lambda: metrics.volatility([1.85, -3.2, 5.6])),
    # [10, 20]: mean 15, deviations +/-5, sum of squares 50, / 1 = 50,
    # square root 7.0710678 -> 7.07. The population form would give 5.00, which
    # understates the spread by a factor of root two.
    Case("two elections, sample not population", ([10.0, 20.0],), 7.07,
         lambda: metrics.volatility([10.0, 20.0])),
    # D6: one election has no variability. Zero would rank the booth as the
    # most stable in the constituency rather than the least known about.
    Case("one election is NULL, not zero", ([5.0],), None,
         lambda: metrics.volatility([5.0])),
    Case("NULLs are dropped before counting", ([5.0, None],), None,
         lambda: metrics.volatility([5.0, None])),
    Case("and dropped without changing the answer",
         ([10.0, None, 20.0],), 7.07,
         lambda: metrics.volatility([10.0, None, 20.0])),
    Case("no elections at all is NULL", ([],), None,
         lambda: metrics.volatility([])),
]

# ---------------------------------------------------------------------------
# priority_score and priority_weight
# ---------------------------------------------------------------------------

# All four: 0.35(0.8) + 0.25(0.6) + 0.20(0.5) + 0.20(0.4)
#         = 0.28 + 0.15 + 0.10 + 0.08 = 0.61, over weight 1.0 -> 0.61
# Floating missing: 0.28 + 0.15 + 0.08 = 0.51, over weight 0.80 -> 0.6375
PRIORITY_SCORE = [
    Case("all four inputs present", (0.8, 0.6, 0.5, 0.4), 0.61,
         lambda: metrics.priority_score(
             PriorityInputs(0.8, 0.6, 0.5, 0.4)).score),
    Case("a missing input renormalises the rest", (0.8, 0.6, None, 0.4), 0.6375,
         lambda: metrics.priority_score(
             PriorityInputs(0.8, 0.6, None, 0.4)).score),
    Case("one input is that input's own value", (0.8, None, None, None), 0.8,
         lambda: metrics.priority_score(
             PriorityInputs(0.8, None, None, None)).score),
    # Averaging nothing is not a priority of zero.
    Case("no inputs at all is NULL", (None, None, None, None), None,
         lambda: metrics.priority_score(
             PriorityInputs(None, None, None, None)).score),
]

PRIORITY_WEIGHT = [
    Case("all four", (0.8, 0.6, 0.5, 0.4), 1.0,
         lambda: metrics.priority_score(
             PriorityInputs(0.8, 0.6, 0.5, 0.4)).weight_used),
    Case("closeness, new voters and volatility", (0.8, 0.6, None, 0.4), 0.8,
         lambda: metrics.priority_score(
             PriorityInputs(0.8, 0.6, None, 0.4)).weight_used),
    Case("closeness alone", (0.8, None, None, None), 0.35,
         lambda: metrics.priority_score(
             PriorityInputs(0.8, None, None, None)).weight_used),
    Case("none", (None, None, None, None), 0.0,
         lambda: metrics.priority_score(
             PriorityInputs(None, None, None, None)).weight_used),
]


# ---------------------------------------------------------------------------
# The registry. Keyed by SQL function name, which is what the e2e test calls
# and what test_metric_parity.py checks against METRIC_FUNCTIONS - so a metric
# with no cases is a failure rather than a gap nobody notices.
# ---------------------------------------------------------------------------

CASES: dict[str, list[Case]] = {
    "metric_votes_polled": VOTES_POLLED,
    "metric_turnout_pct": TURNOUT_PCT,
    "metric_share_pct": SHARE_PCT,
    "metric_margin_votes": MARGIN_VOTES,
    "metric_margin_pct": MARGIN_PCT,
    "metric_signed_margin_pct": SIGNED_MARGIN_PCT,
    "metric_comparison_allowed": COMPARISON_ALLOWED,
    "metric_swing_pct": SWING_PCT,
    "metric_new_voter_pct": NEW_VOTER_PCT,
    "metric_net_roll_change_pct": NET_ROLL_CHANGE_PCT,
    "metric_transfer_delta": TRANSFER_DELTA,
    "metric_floating_pct": FLOATING_PCT,
    "metric_volatility": VOLATILITY,
    "metric_priority_weight": PRIORITY_WEIGHT,
    "metric_priority_score": PRIORITY_SCORE,
}


def flat() -> list[tuple[str, Case]]:
    """Every case, tagged with its metric, for parametrisation."""
    return [(name, case) for name, cases in CASES.items() for case in cases]


def case_id(item: tuple[str, Case]) -> str:
    name, case = item
    return f"{name.removeprefix('metric_')}-{case.name.replace(' ', '_')}"
