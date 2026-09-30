"""Booth-level community estimates (HLD 5, LLD 5).

AGGREGATE ONLY. Nothing in this module reads or writes an individual voter.
Its inputs are a per-booth surname histogram (produced and immediately
discarded by parse_roll), Census 2011 SC/ST proportions for the booth's area,
and an optional booth in-charge survey.

    blend = survey                      if a survey exists
          = 0.7 * surname + 0.3 * census  otherwise, with census informing only
                                          the SC and ST buckets

    confidence = 0.9                                     if surveyed
               = 0.6*coverage + 0.2*recency + 0.2*dict_quality   otherwise

Every figure this produces is an estimate. The UI must render it with its
confidence band, and anything under 0.4 is greyed and labelled
"अनुमान अपर्याप्त".
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from dataclasses import dataclass, field

from common.logging_setup import get_logger

log = get_logger(__name__)

SURNAME_WEIGHT = 0.7
CENSUS_WEIGHT = 0.3
SURVEY_CONFIDENCE = 0.9
LOW_CONFIDENCE = 0.4          # below this the UI greys the figure out
CENSUS_HALF_LIFE_YEARS = 20.0


@dataclass
class CommunityRef:
    community_id: int
    name_en: str
    category: str             # GEN | OBC | SC | ST | MUSLIM | OTHER


@dataclass
class BoothInputs:
    booth_uid: str
    electors: int
    surname_counts: dict[int, float] = field(default_factory=dict)   # community_id -> weighted count
    matched_tokens: float = 0.0
    confident_tokens: float = 0.0                                    # tokens whose dict weight >= 0.8
    census_sc_pct: float | None = None
    census_st_pct: float | None = None
    census_year: int | None = None
    survey: dict[int, float] | None = None                           # community_id -> voters


def _as_pct(counts: dict[int, float]) -> dict[int, float]:
    total = sum(counts.values())
    if total <= 0:
        return {}
    return {cid: 100.0 * v / total for cid, v in counts.items()}


def _category_total(pct: dict[int, float], communities: dict[int, CommunityRef], category: str) -> float:
    return sum(v for cid, v in pct.items() if communities.get(cid) and communities[cid].category == category)


def _rescale_category(pct: dict[int, float], communities: dict[int, CommunityRef],
                      category: str, target_pct: float) -> dict[int, float]:
    """Scale a category's communities so they sum to target_pct, keeping their
    relative proportions. If the surname pass found none of that category, the
    target is dropped into the category's 'other' bucket."""
    members = [cid for cid in pct if communities.get(cid) and communities[cid].category == category]
    current = sum(pct[cid] for cid in members)
    out = dict(pct)
    if current > 0:
        factor = target_pct / current
        for cid in members:
            out[cid] = pct[cid] * factor
    elif target_pct > 0:
        fallback = next((c.community_id for c in communities.values()
                         if c.category == category and "other" in c.name_en.lower()), None)
        if fallback is not None:
            out[fallback] = out.get(fallback, 0.0) + target_pct
    return out


def census_recency(census_year: int | None, current_year: int) -> float:
    """Census 2011 read in 2026 is 15 years stale; score it accordingly."""
    if not census_year:
        return 0.0
    age = max(0, current_year - census_year)
    return max(0.0, 1.0 - age / CENSUS_HALF_LIFE_YEARS)


def blend_booth(inputs: BoothInputs, communities: dict[int, CommunityRef],
                current_year: int = 2026) -> tuple[dict[int, float], float]:
    """Return (community_id -> estimated pct, confidence 0-1)."""
    if inputs.survey:
        pct = _as_pct(inputs.survey)
        return pct, SURVEY_CONFIDENCE

    surname_pct = _as_pct(inputs.surname_counts)
    if not surname_pct:
        return {}, 0.0

    blended = dict(surname_pct)

    # Census informs the SC and ST buckets only - it has nothing to say about
    # the distinction between, say, Kurmi and Yadav.
    for category, census_pct in (("SC", inputs.census_sc_pct), ("ST", inputs.census_st_pct)):
        if census_pct is None:
            continue
        surname_share = _category_total(surname_pct, communities, category)
        target = SURNAME_WEIGHT * surname_share + CENSUS_WEIGHT * census_pct
        blended = _rescale_category(blended, communities, category, target)

    total = sum(blended.values())
    if total > 0:
        blended = {cid: 100.0 * v / total for cid, v in blended.items()}

    coverage = (inputs.matched_tokens / inputs.electors) if inputs.electors else 0.0
    coverage = min(1.0, coverage)
    dict_quality = (inputs.confident_tokens / inputs.matched_tokens) if inputs.matched_tokens else 0.0
    recency = census_recency(inputs.census_year, current_year)
    confidence = 0.6 * coverage + 0.2 * recency + 0.2 * dict_quality

    return blended, round(min(1.0, confidence), 3)


# --------------------------------------------------------------------------
# Database glue
# --------------------------------------------------------------------------

def load_surname_dict(cur) -> dict[str, list[tuple[int, float]]]:
    """surname -> [(community_id, weight), ...]. Ambiguous surnames split."""
    cur.execute("SELECT surname_hi, community_id, weight FROM surname_dict")
    out: dict[str, list[tuple[int, float]]] = {}
    for r in cur.fetchall():
        out.setdefault(r["surname_hi"], []).append((r["community_id"], float(r["weight"])))
    return out


def write_surname_estimates(cur, surname_totals: dict[str, Counter]) -> int:
    """Turn per-booth surname histograms into caste_estimate(source='surname').

    Called by parse_roll while the histogram is still in memory. The histogram
    itself is never persisted - only the community totals it implies.
    """
    lookup = load_surname_dict(cur)
    rows = 0
    for booth_uid, histogram in surname_totals.items():
        totals: dict[int, float] = {}
        matched = 0.0
        confident = 0.0
        for surname, n in histogram.items():
            entries = lookup.get(surname)
            if not entries:
                continue
            matched += n
            for community_id, weight in entries:
                totals[community_id] = totals.get(community_id, 0.0) + n * weight
                if weight >= 0.8:
                    confident += n * weight

        if not totals:
            continue
        grand = sum(totals.values())
        for community_id, value in totals.items():
            cur.execute(
                "INSERT INTO caste_estimate (booth_uid, community_id, est_count, est_pct, "
                "confidence, source, updated_at) VALUES (%s, %s, %s, %s, %s, 'surname', now()) "
                "ON CONFLICT (booth_uid, community_id, source) DO UPDATE SET "
                "est_count = EXCLUDED.est_count, est_pct = EXCLUDED.est_pct, "
                "confidence = EXCLUDED.confidence, updated_at = now()",
                (booth_uid, community_id, int(round(value)),
                 round(100.0 * value / grand, 2) if grand else 0.0,
                 round(min(1.0, confident / matched if matched else 0.0), 3)),
            )
            rows += 1
    return rows


def _communities(cur) -> dict[int, CommunityRef]:
    cur.execute("SELECT community_id, name_en, category FROM community")
    return {r["community_id"]: CommunityRef(r["community_id"], r["name_en"], r["category"])
            for r in cur.fetchall()}


def gather_inputs(cur) -> list[BoothInputs]:
    """Assemble the blend inputs for every booth from what is already loaded."""
    cur.execute(
        """
        SELECT b.booth_uid,
               COALESCE(rs.electors, 0) AS electors,
               d.census_year,
               CASE WHEN d.population > 0 THEN 100.0 * d.sc / d.population END AS census_sc_pct,
               CASE WHEN d.population > 0 THEN 100.0 * d.st / d.population END AS census_st_pct
        FROM booth b
        LEFT JOIN LATERAL (
            SELECT s.electors FROM roll_snapshot s
            JOIN roll_revision r ON r.revision_id = s.revision_id
            WHERE s.booth_uid = b.booth_uid
            ORDER BY r.revision_date DESC LIMIT 1
        ) rs ON true
        LEFT JOIN LATERAL (
            SELECT * FROM demography dd WHERE dd.area_id = b.area_id
            ORDER BY dd.census_year DESC LIMIT 1
        ) d ON true
        WHERE b.is_active
        """
    )
    booths = {r["booth_uid"]: r for r in cur.fetchall()}

    cur.execute(
        "SELECT booth_uid, community_id, est_count, confidence FROM caste_estimate WHERE source = 'surname'"
    )
    surname_rows = cur.fetchall()

    cur.execute("SELECT booth_uid, community_id, COALESCE(voters, households) AS n FROM caste_survey")
    survey_rows = cur.fetchall()

    inputs: dict[str, BoothInputs] = {
        uid: BoothInputs(booth_uid=uid, electors=r["electors"] or 0,
                         census_sc_pct=float(r["census_sc_pct"]) if r["census_sc_pct"] is not None else None,
                         census_st_pct=float(r["census_st_pct"]) if r["census_st_pct"] is not None else None,
                         census_year=r["census_year"])
        for uid, r in booths.items()
    }

    for r in surname_rows:
        bi = inputs.get(r["booth_uid"])
        if bi is None:
            continue
        bi.surname_counts[r["community_id"]] = float(r["est_count"] or 0)
        bi.matched_tokens += float(r["est_count"] or 0)
        bi.confident_tokens += float(r["est_count"] or 0) * float(r["confidence"] or 0)

    for r in survey_rows:
        bi = inputs.get(r["booth_uid"])
        if bi is None or not r["n"]:
            continue
        bi.survey = bi.survey or {}
        bi.survey[r["community_id"]] = float(r["n"])

    return list(inputs.values())


def refresh(current_year: int = 2026) -> dict:
    """Recompute caste_estimate(source='blend') for every booth."""
    from common.db import connection

    stats = {"booths": 0, "rows": 0, "low_confidence": 0, "surveyed": 0}
    with connection() as conn, conn.cursor() as cur:
        communities = _communities(cur)
        for bi in gather_inputs(cur):
            pct, confidence = blend_booth(bi, communities, current_year)
            if not pct:
                continue
            stats["booths"] += 1
            if bi.survey:
                stats["surveyed"] += 1
            if confidence < LOW_CONFIDENCE:
                stats["low_confidence"] += 1
            for community_id, value in pct.items():
                est_count = int(round(bi.electors * value / 100.0)) if bi.electors else None
                cur.execute(
                    "INSERT INTO caste_estimate (booth_uid, community_id, est_count, est_pct, "
                    "confidence, source, updated_at) VALUES (%s, %s, %s, %s, %s, 'blend', now()) "
                    "ON CONFLICT (booth_uid, community_id, source) DO UPDATE SET "
                    "est_count = EXCLUDED.est_count, est_pct = EXCLUDED.est_pct, "
                    "confidence = EXCLUDED.confidence, updated_at = now()",
                    (bi.booth_uid, community_id, est_count, round(value, 2), confidence),
                )
                stats["rows"] += 1
    return stats


def main(argv: list[str] | None = None) -> int:
    from common.jobs import job_context

    ap = argparse.ArgumentParser(description="Recompute booth caste estimates")
    ap.add_argument("--year", type=int, default=2026, help="year used for census recency scoring")
    args = ap.parse_args(argv)

    with job_context("analytics.caste_estimate") as job:
        stats = refresh(args.year)
        job.set(**stats)
        job.log_line(
            f"{stats['booths']} booth(s) estimated, {stats['rows']} row(s), "
            f"{stats['surveyed']} from ground survey, "
            f"{stats['low_confidence']} below confidence {LOW_CONFIDENCE}"
        )
        if stats["low_confidence"]:
            log.warning("%d booth(s) below confidence %.1f will render greyed in the UI",
                        stats["low_confidence"], LOW_CONFIDENCE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
