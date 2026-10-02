"""Bypoll scenario projection (LLD 11).

    votes[p] = SUM over booths of  t * SUM over q of ( votes2024[q] * T[q->p] )
                                   + additions * n[p]
    margin   = votes[JMM] - votes[BJP]

This is arithmetic on stated assumptions, not a forecast. The sympathy swing
after the sitting MLA's death is a scenario input precisely because nobody can
measure it in advance (HLD 12).

500 Monte-Carlo draws apply noise to the transfer matrix and the turnout
multiplier and report P10 / P50 / P90, plus the booths whose outcome varies
most - those are where the seat is actually decided.

No third-party dependency: this runs inside the API container too.
"""

from __future__ import annotations

import random
import statistics
from dataclasses import dataclass, field

DEFAULT_DRAWS = 500
DEFAULT_NOISE = 0.05

# NOTA is in the baseline totals because it is a real candidate row and part of
# the denominator, but it cannot win a seat, so it is excluded from the argmax.
NOTA = "NOTA"


@dataclass
class BoothBaseline:
    """One booth's 2024 result plus the electors added since."""

    booth_uid: str
    votes: dict[str, int] = field(default_factory=dict)   # party abbr -> votes
    additions: int = 0

    def total(self) -> int:
        return sum(self.votes.values())


@dataclass
class ScenarioInput:
    """Everything the user sets on the sliders."""

    turnout_multiplier: float = 1.0
    # T[from_party][to_party] = share of the 2024 vote that moves. Rows that do
    # not sum to 1 are normalised, and a missing row means the party holds its
    # own vote.
    transfer: dict[str, dict[str, float]] = field(default_factory=dict)
    # Sympathy: share of BJP's 2024 vote that moves to JMM. Negative moves the
    # other way.
    sympathy_swing: float = 0.0
    # How new electors split. Defaults to each booth's own 2024 shares.
    new_voter_split: dict[str, float] | None = None
    new_voter_turnout: float = 0.6
    draws: int = DEFAULT_DRAWS
    noise: float = DEFAULT_NOISE
    seed: int | None = 42
    # The pair the margin is measured between, from ac_contest. There is no
    # default: the audited code hardcoded ("JMM", "BJP"), so a Dumri scenario
    # that moved most of the vote to JLKM still reported JMM or BJP as the
    # winner while the votes dict beside it showed the real leader - the
    # response contradicted itself (D5). Four of the six ACs have a different
    # pair.
    contest: tuple[str, str] = ("", "")
    # party abbr -> alliance at the *target* event. Transfers are built on
    # alliances, not raw parties, because that is how a vote actually moves: an
    # AJSU voter in 2024 is being asked to back the NDA candidate, and AJSU was
    # outside the NDA in 2019.
    alliance: dict[str, str] = field(default_factory=dict)


@dataclass
class ScenarioResult:
    votes: dict[str, float]
    margin_p10: float
    margin_p50: float
    margin_p90: float
    margin_point: float
    # The projected leader, by argmax over candidate totals excluding NOTA - not
    # whichever of the contest pair is ahead. See project().
    winner: str | None
    winner_votes: float
    runner_up: str | None
    total_votes: float
    # The margin between the two contest-pair parties, which is a different
    # question from who wins and is reported separately so the two cannot be
    # confused.
    contest_margin: float | None = None
    booth_variance: list[tuple[str, float]] = field(default_factory=list)
    draws: int = 0
    assumptions: dict = field(default_factory=dict)
    # D6: the band is a sensitivity range at a stated noise level, not a
    # confidence interval, and it says so in the payload rather than only in the
    # UI copy.
    band_label: str = ""
    baseline_source: str | None = None
    ac_verified: bool | None = None


def _normalised_row(row: dict[str, float], fallback: str) -> dict[str, float]:
    total = sum(max(0.0, v) for v in row.values())
    if total <= 0:
        return {fallback: 1.0}
    return {k: max(0.0, v) / total for k, v in row.items()}


def _perturb_row(row: dict[str, float], noise: float, rng, fallback: str) -> dict[str, float]:
    """Apply noise to a transfer row, then renormalise.

    D6: the audited version multiplied each destination share by (1 +/- noise)
    and then renormalised, which for a single-destination row - an identity row
    {party: 1.0}, which is most of the matrix - cancelled the perturbation
    exactly. Only multi-destination rows carried any uncertainty, so with the
    frontend always sending one JLKM split, the entire P10-P90 band came from
    one arbitrary noise constant applied to one row, while being displayed next
    to a point estimate as though it were empirical.

    Perturbing in log space keeps shares positive and makes the noise
    multiplicative in the odds rather than in the level, so a row that happens
    to be near 0 or 1 is not pinned there by clipping.
    """
    if len(row) <= 1:
        # A single-destination row genuinely has no internal uncertainty to
        # model; its variance comes from the source total and the turnout
        # multiplier, which are perturbed separately.
        return _normalised_row(dict(row), fallback)

    import math

    perturbed: dict[str, float] = {}
    for dst, share in row.items():
        if share <= 0:
            perturbed[dst] = 0.0
            continue
        # log-normal jitter: exp(uniform(-noise, noise)) is centred near 1.
        perturbed[dst] = share * math.exp(rng.uniform(-noise, noise))
    return _normalised_row(perturbed, fallback)


def resolve_alliance_transfer(
    transfer: dict[str, dict[str, float]],
    alliance: dict[str, str],
    parties: list[str],
) -> dict[str, dict[str, float]]:
    """Expand an alliance-keyed transfer matrix into a party-keyed one.

    A row may be keyed by an alliance ("NDA": {...}) or by a party
    ("JLKM": {...}); a party-keyed row wins, since it is the more specific
    statement. Destinations may likewise be alliances, in which case the share
    is split across that alliance's members in proportion to nothing at all -
    equally - because the matrix says how much of a bloc's vote moves, not which
    of its candidates receives it. There is one candidate per alliance per seat
    in practice, so the split is almost always trivial.
    """
    by_alliance: dict[str, list[str]] = {}
    for party, name in alliance.items():
        by_alliance.setdefault(name, []).append(party)

    resolved: dict[str, dict[str, float]] = {}
    for party in parties:
        row = transfer.get(party)
        if row is None:
            row = transfer.get(alliance.get(party, ""))
        if row is None:
            continue

        expanded: dict[str, float] = {}
        for destination, share in row.items():
            members = by_alliance.get(destination)
            if members and destination not in parties:
                for member in members:
                    expanded[member] = expanded.get(member, 0.0) + share / len(members)
            else:
                expanded[destination] = expanded.get(destination, 0.0) + share
        resolved[party] = expanded
    return resolved


def effective_transfer(inp: ScenarioInput, parties: list[str]) -> dict[str, dict[str, float]]:
    """Build the full matrix: explicit rows, identity for the rest, then apply
    the sympathy swing on top of the second contest party's row."""
    supplied = resolve_alliance_transfer(inp.transfer, inp.alliance, parties)

    matrix: dict[str, dict[str, float]] = {}
    for party in parties:
        row = supplied.get(party)
        matrix[party] = _normalised_row(dict(row), party) if row else {party: 1.0}

    # Rows are "from" parties. A positive swing moves part of party B's own vote
    # to party A; a negative one moves part of party A's own vote to party B.
    # The negative branch used to edit party B's row and read party A's share
    # from it - normally 0 - so a negative swing moved nothing at all.
    party_a, party_b = inp.contest
    swing = max(-1.0, min(1.0, inp.sympathy_swing or 0.0))
    source, destination = (party_b, party_a) if swing > 0 else (party_a, party_b)
    if swing and source in matrix:
        row = dict(matrix[source])
        moved = row.get(source, 0.0) * abs(swing)
        row[source] = row.get(source, 0.0) - moved
        row[destination] = row.get(destination, 0.0) + moved
        matrix[source] = row
    return matrix


def _project_once(booths: list[BoothBaseline], matrix: dict[str, dict[str, float]],
                  turnout: float, inp: ScenarioInput) -> tuple[dict[str, float], dict[str, float]]:
    """One deterministic projection. Returns (party totals, per-booth margin)."""
    totals: dict[str, float] = {}
    booth_margin: dict[str, float] = {}
    jmm, bjp = inp.contest

    for booth in booths:
        local: dict[str, float] = {}
        for from_party, votes in booth.votes.items():
            row = matrix.get(from_party, {from_party: 1.0})
            for to_party, share in row.items():
                local[to_party] = local.get(to_party, 0.0) + votes * share * turnout

        if booth.additions:
            new_votes = booth.additions * inp.new_voter_turnout
            split = inp.new_voter_split
            if not split:
                base_total = booth.total()
                split = ({p: v / base_total for p, v in booth.votes.items()}
                         if base_total else {})
            for party, share in split.items():
                local[party] = local.get(party, 0.0) + new_votes * share

        for party, value in local.items():
            totals[party] = totals.get(party, 0.0) + value
        booth_margin[booth.booth_uid] = local.get(jmm, 0.0) - local.get(bjp, 0.0)

    return totals, booth_margin


def project(booths: list[BoothBaseline], inp: ScenarioInput) -> ScenarioResult:
    parties = sorted({p for b in booths for p in b.votes})
    matrix = effective_transfer(inp, parties)
    jmm, bjp = inp.contest

    point_totals, _ = _project_once(booths, matrix, inp.turnout_multiplier, inp)
    point_margin = point_totals.get(jmm, 0.0) - point_totals.get(bjp, 0.0)

    rng = random.Random(inp.seed)
    margins: list[float] = []
    per_booth: dict[str, list[float]] = {b.booth_uid: [] for b in booths}

    for _ in range(max(1, inp.draws)):
        noisy = {src: _perturb_row(row, inp.noise, rng, src) for src, row in matrix.items()}
        turnout = inp.turnout_multiplier * (1 + rng.uniform(-inp.noise, inp.noise))
        # D6: the source totals are perturbed too, not only the transfer rows.
        # Without this an identity row contributes no variance at all, so the
        # band came entirely from whichever rows happened to be
        # multi-destination.
        source_jitter = 1 + rng.uniform(-inp.noise, inp.noise)
        totals, booth_margin = _project_once(booths, noisy, turnout * source_jitter, inp)
        margins.append(totals.get(jmm, 0.0) - totals.get(bjp, 0.0))
        for uid, m in booth_margin.items():
            per_booth[uid].append(m)

    margins.sort()

    def pct(p: float) -> float:
        if not margins:
            return 0.0
        idx = min(len(margins) - 1, max(0, int(round(p * (len(margins) - 1)))))
        return round(margins[idx], 1)

    variance = sorted(
        ((uid, round(statistics.pstdev(vals), 2)) for uid, vals in per_booth.items() if len(vals) > 1),
        key=lambda x: x[1], reverse=True,
    )[:20]

    # D5: the winner is the argmax of projected totals excluding NOTA, not
    # whichever of the contest pair is ahead. The audited code returned
    # `jmm if point_margin >= 0 else bjp`, so a scenario that moved most of the
    # vote to JLKM still named JMM or BJP - while the votes dict in the same
    # response showed the real leader. The contest pair now decides only which
    # margin is measured, which is what it is for.
    contestants = {p: v for p, v in point_totals.items() if p != NOTA}
    ordered = sorted(contestants.items(), key=lambda kv: (-kv[1], kv[0]))
    winner = ordered[0][0] if ordered else None
    winner_votes = ordered[0][1] if ordered else 0.0
    runner_up = ordered[1][0] if len(ordered) > 1 else None

    return ScenarioResult(
        votes={p: round(v, 1) for p, v in sorted(point_totals.items(), key=lambda x: -x[1])},
        margin_p10=pct(0.10), margin_p50=pct(0.50), margin_p90=pct(0.90),
        margin_point=round(point_margin, 1),
        winner=winner,
        winner_votes=round(winner_votes, 1),
        runner_up=runner_up,
        contest_margin=round(point_margin, 1) if jmm and bjp else None,
        total_votes=round(sum(point_totals.values()), 1),
        booth_variance=variance,
        draws=len(margins),
        assumptions={
            "turnout_multiplier": inp.turnout_multiplier,
            "sympathy_swing": inp.sympathy_swing,
            "new_voter_turnout": inp.new_voter_turnout,
            "transfer": inp.transfer,
            "noise": inp.noise,
            "contest": list(inp.contest),
            "alliance": inp.alliance,
        },
        band_label=(
            f"sensitivity range at noise={inp.noise:g} over {len(margins)} draws - "
            "not a confidence interval"
        ),
    )


def load_baseline(ac_id: int, area_id: int | None = None,
                  block_id: int | None = None) -> list[BoothBaseline]:
    """Read one AC's baseline booth results plus additions since.

    ac_id is required, not optional. A scenario run without it would project
    across every constituency at once and report the total as one seat's margin.
    """
    from common.db import query

    where = ["w.ac_id = %s"]
    params: list = [ac_id]
    if area_id is not None:
        where.append("w.area_id = %s")
        params.append(area_id)
    if block_id is not None:
        where.append("w.block_id = %s")
        params.append(block_id)
    clause = " AND " + " AND ".join(where)

    rows = query(
        f"""
        SELECT w.booth_uid, w.jmm, w.bjp, w.ajsu, w.jlkm, w.inc, w.rjd, w.jvm,
               w.others, w.nota, COALESCE(n.additions, 0) AS additions
        FROM mv_result_booth_wide w
        JOIN election e ON e.election_id = w.election_id AND e.is_baseline
        -- Same election as the baseline row: mv_new_voter_share has one row per
        -- (election, booth), so joining on booth alone duplicated every booth
        -- once a second election had a roll linked, multiplying the totals.
        LEFT JOIN mv_new_voter_share n ON n.booth_uid = w.booth_uid
                                      AND n.ac_id = w.ac_id
                                      AND n.election_id = w.election_id
        WHERE true {clause}
        """,
        params,
    )
    out: list[BoothBaseline] = []
    for r in rows:
        votes = {abbr: int(r[abbr.lower()] or 0)
                 for abbr in ("JMM", "BJP", "AJSU", "JLKM", "INC", "RJD", "JVM")}
        votes["OTH"] = int(r["others"] or 0)
        votes["NOTA"] = int(r["nota"] or 0)
        out.append(BoothBaseline(
            booth_uid=r["booth_uid"],
            votes={p: v for p, v in votes.items() if v > 0},
            additions=int(r["additions"] or 0),
        ))
    return out


def jlkm_transfer_scenario(to_bjp: float) -> dict[str, dict[str, float]]:
    """The question the HLD asks directly: 'if 60% of JLKM's 2024 votes shift to
    BJP in the bypoll, what is the projected margin?'"""
    to_bjp = max(0.0, min(1.0, to_bjp))
    return {"JLKM": {"BJP": to_bjp, "JMM": 1.0 - to_bjp}}
