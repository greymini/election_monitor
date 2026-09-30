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
    contest: tuple[str, str] = ("JMM", "BJP")


@dataclass
class ScenarioResult:
    votes: dict[str, float]
    margin_p10: float
    margin_p50: float
    margin_p90: float
    margin_point: float
    winner: str
    total_votes: float
    booth_variance: list[tuple[str, float]] = field(default_factory=list)
    draws: int = 0
    assumptions: dict = field(default_factory=dict)


def _normalised_row(row: dict[str, float], fallback: str) -> dict[str, float]:
    total = sum(max(0.0, v) for v in row.values())
    if total <= 0:
        return {fallback: 1.0}
    return {k: max(0.0, v) / total for k, v in row.items()}


def effective_transfer(inp: ScenarioInput, parties: list[str]) -> dict[str, dict[str, float]]:
    """Build the full matrix: explicit rows, identity for the rest, then apply
    the sympathy swing on top of the BJP row."""
    matrix: dict[str, dict[str, float]] = {}
    for party in parties:
        row = inp.transfer.get(party)
        matrix[party] = _normalised_row(dict(row), party) if row else {party: 1.0}

    jmm, bjp = inp.contest
    if inp.sympathy_swing and bjp in matrix:
        swing = max(-1.0, min(1.0, inp.sympathy_swing))
        row = dict(matrix[bjp])
        if swing > 0:
            moved = row.get(bjp, 0.0) * swing
            row[bjp] = row.get(bjp, 0.0) - moved
            row[jmm] = row.get(jmm, 0.0) + moved
        else:
            moved = row.get(jmm, 0.0) * abs(swing)
            row[jmm] = row.get(jmm, 0.0) - moved
            row[bjp] = row.get(bjp, 0.0) + moved
        matrix[bjp] = row
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
        noisy = {
            src: _normalised_row(
                {dst: max(0.0, share * (1 + rng.uniform(-inp.noise, inp.noise)))
                 for dst, share in row.items()},
                src,
            )
            for src, row in matrix.items()
        }
        turnout = inp.turnout_multiplier * (1 + rng.uniform(-inp.noise, inp.noise))
        totals, booth_margin = _project_once(booths, noisy, turnout, inp)
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

    return ScenarioResult(
        votes={p: round(v, 1) for p, v in sorted(point_totals.items(), key=lambda x: -x[1])},
        margin_p10=pct(0.10), margin_p50=pct(0.50), margin_p90=pct(0.90),
        margin_point=round(point_margin, 1),
        winner=jmm if point_margin >= 0 else bjp,
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
        },
    )


def load_baseline(area_id: int | None = None, block_id: int | None = None) -> list[BoothBaseline]:
    """Read the baseline election's booth results plus additions since."""
    from common.db import query

    where = []
    params: list = []
    if area_id is not None:
        where.append("w.area_id = %s")
        params.append(area_id)
    if block_id is not None:
        where.append("w.block_id = %s")
        params.append(block_id)
    clause = (" AND " + " AND ".join(where)) if where else ""

    rows = query(
        f"""
        SELECT w.booth_uid, w.jmm, w.bjp, w.ajsu, w.jlkm, w.inc, w.rjd, w.jvm,
               w.others, w.nota, COALESCE(n.additions, 0) AS additions
        FROM mv_result_booth_wide w
        JOIN election e ON e.election_id = w.election_id AND e.is_baseline
        LEFT JOIN mv_new_voter_share n ON n.booth_uid = w.booth_uid
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
