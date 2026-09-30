"""The canonical metric definitions (master prompt 3.2), in one place.

Every formula in this file is defined exactly once. The SQL views in
`db/migrations/0015_metrics.sql` implement the same definitions over whole
tables; this module is what the loaders, the validator and the tests use, and
`tests/test_metrics.py` checks the two agree on hand-computed fixtures.

Two rules run through all of it.

**Absent is NULL, never zero and never a default.** The audit found the opposite
everywhere: `swing_pct` reported a party's entire vote share as its swing for
the earliest loaded election because `LAG` was COALESCEd to 0 (D2);
`floating_pct` reported exactly 50.00 for every booth in the constituency
because a Pedersen index over one poll type is 100/2 (D4); `mv_new_voter_share`
reported 0 additions everywhere because its baseline CTE was empty (B1). Each of
those is a number that looks like data and is not. Every function here returns
`None` where the input is missing, and says in its docstring when that happens.

**One denominator.** Valid votes *including* NOTA, which is what the ECI's own
published margin percentage uses: Giridih 2024's 3,838-vote margin over 207,598
valid votes is the published 1.85%, and excluding NOTA's 2,004 gives 1.87%. The
old code divided by a NOTA-excluding total while displaying a NOTA-including one
in the same row (D1), so a reader could not reconcile the row against itself and
the headline margin was wrong in the second decimal place.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

# NOTA is a candidate row - it must be, or it cannot enter the denominator - but
# it is not a contestant: it can never win, never be runner-up, and never take
# part in a transfer or a swing between parties.
NOTA = "NOTA"

# Crosswalk confidence below which an unreviewed match may not be used for a
# multi-year comparison (master prompt 3.2, swing_pct).
CROSSWALK_MIN_CONFIDENCE = 0.85

PRIORITY_WEIGHTS = {
    "closeness": 0.35,
    "new_voter_pct": 0.25,
    "floating_pct": 0.20,
    "volatility": 0.20,
}


def _round(value: float | None, places: int = 2) -> float | None:
    return None if value is None else round(value, places)


# ---------------------------------------------------------------------------
# Vote totals
# ---------------------------------------------------------------------------


def valid_votes(candidate_votes: Mapping[str, int | None] | None) -> int | None:
    """Sum of candidate votes **including NOTA** - Form 20's "total valid votes".

    NULL when there is no result. An empty mapping is no result, not zero: a
    booth with no Form 20 row has not recorded zero valid votes.
    """
    if not candidate_votes:
        return None
    values = [v for v in candidate_votes.values() if v is not None]
    if not values:
        return None
    return sum(values)


def votes_polled(valid: int | None, rejected: int | None) -> int | None:
    """`valid_votes + rejected`. Tendered votes are excluded: a tendered ballot
    is recorded separately and is not in the count.

    NULL when there is no result. A missing `rejected` is treated as zero
    because Form 20 omits the column when it is zero - but a missing `valid` is
    NULL, since that is absence of a result rather than absence of rejections.
    """
    if valid is None:
        return None
    return valid + (rejected or 0)


def turnout_pct(polled: int | None, electors: int | None) -> float | None:
    """`votes_polled / electors x 100`.

    Electors come from the linked roll snapshot, falling back to the PS list -
    **never** from Form 20, which does not print them. NULL when electors are
    unknown, which was the case for every booth in the system before this work
    (B4): `result_booth_meta.electors` had no writer, so turnout was NULL
    everywhere it appeared while the map still offered it as a metric and
    coloured every booth grey.
    """
    if polled is None or not electors:
        return None
    return _round(100.0 * polled / electors)


def share_pct(candidate: int | None, valid: int | None) -> float | None:
    """`candidate_votes / valid_votes x 100`, NOTA inside the denominator."""
    if candidate is None or not valid:
        return None
    return _round(100.0 * candidate / valid)


# ---------------------------------------------------------------------------
# Margin
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Ranking:
    """The contest at one booth, ranked. NOTA is excluded from the ranking."""

    winner: str | None
    winner_votes: int | None
    runner_up: str | None
    runner_up_votes: int | None
    contestants: int

    @property
    def is_contest(self) -> bool:
        return self.contestants >= 2


def rank_candidates(candidate_votes: Mapping[str, int | None] | None) -> Ranking:
    """Rank real candidates by votes, excluding NOTA.

    Ranking is **by candidate, not by party bucket**. The old view grouped on
    `COALESCE(party_id, -1)`, so every unresolved or independent candidate was
    summed into one pseudo-party that then competed for winner: eight
    independents on 500 votes each became a single 4,000-vote "party" that
    outranked a real winner on 3,000, producing a NULL winner and a margin
    measured against a candidate who does not exist (D3).
    """
    if not candidate_votes:
        return Ranking(None, None, None, None, 0)

    real = [(k, v) for k, v in candidate_votes.items() if k != NOTA and v is not None]
    if not real:
        return Ranking(None, None, None, None, 0)

    # Sort by votes desc, then by name so a tie is at least deterministic.
    ordered = sorted(real, key=lambda kv: (-kv[1], kv[0]))
    winner, winner_votes = ordered[0]
    if len(ordered) == 1:
        return Ranking(winner, winner_votes, None, None, 1)
    runner_up, runner_votes = ordered[1]
    return Ranking(winner, winner_votes, runner_up, runner_votes, len(ordered))


def margin_votes(ranking: Ranking) -> int | None:
    """Winner minus runner-up, both real candidates.

    NULL with fewer than two candidates. Not zero: an uncontested seat has no
    margin, and reporting 0 would put it at the top of a "tightest contests"
    list.
    """
    if not ranking.is_contest or ranking.winner_votes is None:
        return None
    return ranking.winner_votes - (ranking.runner_up_votes or 0)


def margin_pct(ranking: Ranking, valid: int | None) -> float | None:
    """`margin_votes / valid_votes x 100`, NOTA inside the denominator."""
    margin = margin_votes(ranking)
    if margin is None or not valid:
        return None
    return _round(100.0 * margin / valid)


def signed_margin_pct(
    ranking: Ranking,
    valid: int | None,
    party_a: str | None,
    party_b: str | None,
) -> float | None:
    """`+margin_pct` if the contest pair's first party won, `-` if the second,
    NULL if neither did.

    This is what the map's diverging ramp reads. `margin_pct` is unsigned by
    construction, and the old map fed it straight into a diverging scale (F1):
    the ramp only ever reached its upper half, so a JMM-held booth on a 12-point
    margin and a BJP-held booth on a 12-point margin rendered the same colour
    while the legend below claimed the two arms meant opposite things.

    NULL rather than 0 when a third party won, because 0 would place it at the
    ramp's neutral midpoint - visually identical to a knife-edge contest between
    the pair, which is the opposite of what happened.
    """
    value = margin_pct(ranking, valid)
    if value is None or ranking.winner is None:
        return None
    if party_a and ranking.winner == party_a:
        return value
    if party_b and ranking.winner == party_b:
        return -value
    return None


# ---------------------------------------------------------------------------
# Swing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CrosswalkLink:
    """How a booth at one election maps to a booth at another."""

    confidence: float
    reviewed: bool
    kind: str = "fuzzy"  # anchor | exact | fuzzy | manual | new | split | merge

    @property
    def usable_for_comparison(self) -> bool:
        """A match may carry a multi-year comparison if a human reviewed it, or
        if it scored at or above the auto-accept threshold.

        The audit's B2 was the inverse failure: review-band stations got no
        crosswalk row at all and every view inner-joined that table, so their
        votes vanished from every rollup with no error - and C11's coverage
        check reported healthy because the dropped rows were missing from its
        denominator too.
        """
        return self.reviewed or self.confidence >= CROSSWALK_MIN_CONFIDENCE


def comparison_allowed(
    link: CrosswalkLink | None = None,
    lineage_kind: str | None = None,
    lineage_aggregated: bool = False,
) -> bool:
    """Whether this booth may be compared against its own past at all.

    The gate `swing_pct` applies, extracted so that swing share, swing votes and
    the vanished-party rows cannot end up with three slightly different versions
    of it. Its SQL counterpart is `metric_comparison_allowed`, and the parity
    test drives both over the same truth table.

    A link of `None` means the booth is the same station it was - an anchor, not
    an unverified guess - so there is nothing to gate on.
    """
    if link is not None and not link.usable_for_comparison:
        return False
    if lineage_kind in {"split", "merge"} and not lineage_aggregated:
        return False
    return True


def swing_pct(
    share_now: float | None,
    share_prev: float | None,
    link: CrosswalkLink | None = None,
    lineage_kind: str | None = None,
    lineage_aggregated: bool = False,
) -> float | None:
    """`share_now - share_prev` for the same election type and the same booth.

    NULL when:
      * there is no prior election for this booth - **not** zero-filled. The old
        view COALESCEd a missing LAG to 0, so the earliest loaded election
        reported every party's full vote share as its swing: a fabricated +38.3
        point BJP swing at every booth in VS-2014 (D2).
      * the crosswalk link is unreviewed and below the auto-accept threshold.
      * the booth split or merged and the lineage group has not been aggregated.

    A party that genuinely contested the prior election and polled nothing has
    `share_prev = 0.0`, which is a real zero and produces a real swing. That is
    why `share_prev` is typed as optional rather than defaulted: `None` means
    "did not contest / unknown", `0.0` means "contested and got nothing".
    """
    if share_now is None or share_prev is None:
        return None
    if not comparison_allowed(link, lineage_kind, lineage_aggregated):
        return None
    return _round(share_now - share_prev)


def alliance_swing_pct(
    shares_now: Mapping[str, float | None],
    shares_prev: Mapping[str, float | None],
    alliance_now: Mapping[str, str],
    alliance_prev: Mapping[str, str],
    alliance: str,
    link: CrosswalkLink | None = None,
) -> float | None:
    """Swing for an alliance, summing member parties **as they stood at each
    event**.

    The alliance map differs between the two events on purpose: AJSU was outside
    the NDA in 2019 and inside it in 2024, and JVM existed in 2019 and had
    merged into BJP by 2024. Using one event's alliances for both would attribute
    a party's votes to a bloc it was not in.

    NULL when either side has no members with a share, on the same reasoning as
    `swing_pct`.
    """
    if link is not None and not link.usable_for_comparison:
        return None

    def total(shares: Mapping[str, float | None], mapping: Mapping[str, str]) -> float | None:
        members = [shares.get(p) for p, a in mapping.items() if a == alliance]
        present = [m for m in members if m is not None]
        return sum(present) if present else None

    now = total(shares_now, alliance_now)
    prev = total(shares_prev, alliance_prev)
    if now is None or prev is None:
        return None
    return _round(now - prev)


# ---------------------------------------------------------------------------
# Roll movement
# ---------------------------------------------------------------------------


def new_voter_pct(additions: int | None, electors_at_end: int | None) -> float | None:
    """`additions in window / electors at window end x 100`.

    The window is (linked roll of the previous general election, linked roll of
    the target]. NULL when either roll is missing - which was every booth, since
    nothing wrote `election_roll_link` and the view's baseline CTE was therefore
    empty, giving `additions = 0` and `new_voter_pct = 0.00` everywhere while a
    different screen read `roll_change` directly and showed the real numbers
    (B1). Two contradictory figures for the same metric, neither flagged.
    """
    if additions is None or not electors_at_end:
        return None
    return _round(100.0 * additions / electors_at_end)


def net_roll_change_pct(
    additions: int | None,
    deletions: int | None,
    electors_at_start: int | None,
) -> float | None:
    """`(additions - deletions) / electors at window start x 100`.

    Deletions matter as much as additions after a Special Intensive Revision, so
    this is a first-class metric rather than a derived curiosity. NULL when
    either roll is missing.
    """
    if additions is None or deletions is None or not electors_at_start:
        return None
    return _round(100.0 * (additions - deletions) / electors_at_start)


# ---------------------------------------------------------------------------
# LS / VS transfer
# ---------------------------------------------------------------------------


def transfer_delta(share_vs: float | None, share_ls: float | None) -> float | None:
    """`share_VS - share_LS` for one party, same year, same booth.

    NULL when either leg is missing. The Giridih case this exists for: AJSU led
    the assembly segment at the Lok Sabha poll and JMM held the seat six months
    later, which the HLD calls the single most important dynamic to model.
    """
    if share_vs is None or share_ls is None:
        return None
    return _round(share_vs - share_ls)


def floating_pct(
    shares_ls: Mapping[str, float | None],
    shares_vs: Mapping[str, float | None],
) -> float | None:
    """Pedersen index: half the sum of absolute share changes, LS vs VS, same
    year.

    NULL when either leg is missing. This is the most important NULL rule in the
    file. The old view FULL OUTER JOINed the two polls, so where only one
    existed every party's delta equalled its full share in that poll and the
    index came to exactly 100/2: **every booth in the constituency reported
    `floating_pct = 50.00`** (D4). It appeared on the map, in the transfer view,
    on the booth card and in the priority score, it looked like a finding, and
    because it was uniform `PERCENT_RANK` flattened it to zero and the priority
    score silently lost its 0.20 floating-vote term.
    """
    if not shares_ls or not shares_vs:
        return None
    parties = set(shares_ls) | set(shares_vs)
    deltas = []
    for party in parties:
        ls = shares_ls.get(party)
        vs = shares_vs.get(party)
        # A party that contested one poll and not the other has a real delta:
        # its whole share moved. A party absent from both is not a delta at all.
        if ls is None and vs is None:
            continue
        deltas.append(abs((vs or 0.0) - (ls or 0.0)))
    if not deltas:
        return None
    return _round(sum(deltas) / 2.0)


# ---------------------------------------------------------------------------
# Volatility
# ---------------------------------------------------------------------------


def volatility(signed_margins: Sequence[float | None]) -> float | None:
    """Standard deviation of `signed_margin_pct` across available VS years.

    NULL with fewer than two years. The sample standard deviation is used, not
    the population one: these are the years we happen to hold, not the whole
    history of the seat.
    """
    values = [v for v in signed_margins if v is not None]
    if len(values) < 2:
        return None
    return _round(statistics.stdev(values))


# ---------------------------------------------------------------------------
# Booth priority
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PriorityInputs:
    """Percentile ranks in [0, 1], computed **within one AC**.

    Per-AC is not a detail: Kanke has 481,815 electors and Giridih 304,898, so a
    percentile across the pooled set would rank Kanke's booths high on
    electorate size alone and call that priority.
    """

    closeness: float | None = None        # 1 - margin percentile
    new_voter_pct: float | None = None
    floating_pct: float | None = None
    volatility: float | None = None


@dataclass(frozen=True)
class PriorityScore:
    score: float | None
    inputs_used: list[str] = field(default_factory=list)
    weight_used: float = 0.0


def priority_score(inputs: PriorityInputs) -> PriorityScore:
    """Weighted percentile ranks: 0.35 closeness, 0.25 new voters, 0.20
    floating, 0.20 volatility.

    Missing inputs are dropped and the remaining weights renormalised, and
    `inputs_used` records which contributed. That last part matters because two
    of the four inputs were constant in the audited system - `new_voter_pct` was
    0 everywhere (B1) and `floating_pct` was 50.00 everywhere (D4) - so a score
    that looked like a four-factor ranking was really a two-factor one, with no
    way to tell from the output.

    NULL when every input is missing.
    """
    available = {
        name: value
        for name, value in (
            ("closeness", inputs.closeness),
            ("new_voter_pct", inputs.new_voter_pct),
            ("floating_pct", inputs.floating_pct),
            ("volatility", inputs.volatility),
        )
        if value is not None
    }
    if not available:
        return PriorityScore(None, [], 0.0)

    total_weight = sum(PRIORITY_WEIGHTS[name] for name in available)
    weighted = sum(PRIORITY_WEIGHTS[name] * value for name, value in available.items())
    return PriorityScore(
        score=_round(weighted / total_weight, 4),
        inputs_used=sorted(available),
        weight_used=_round(total_weight, 4) or 0.0,
    )


def percentile_ranks(values: Iterable[float | None]) -> list[float | None]:
    """Percentile rank in [0, 1] for each value, NULLs preserved as NULL.

    Matches PostgreSQL's `PERCENT_RANK()`: the smallest value ranks 0. Ties
    share the lower rank, as SQL does.
    """
    items = list(values)
    present = sorted(v for v in items if v is not None)
    if len(present) < 2:
        # A single value has no meaningful percentile. Returning 0 would rank it
        # bottom and returning 1 would rank it top; neither is true.
        return [None if v is None else None for v in items]

    ranks: list[float | None] = []
    for value in items:
        if value is None:
            ranks.append(None)
            continue
        below = sum(1 for other in present if other < value)
        ranks.append(_round(below / (len(present) - 1), 6))
    return ranks
