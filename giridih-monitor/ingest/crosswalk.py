"""Match polling stations across years onto a stable booth_uid (LLD 4.4).

Scoring, after canonicalising the building name and transliterating to Latin:

    score = 0.50 * jaro_winkler(building)
          + 0.30 * jaro_winkler(village or ward)
          + 0.20 * (roll part matches ? 1 : 0)

with one hard rule on top: if both sides name a recognised but *different*
building type (a primary school is not a middle school), the score is capped at
0.60 so it can never auto-accept.

    score >= 0.85  -> auto-accept
    0.65 .. 0.85   -> review_queue(kind='crosswalk'), nothing loaded
    < 0.65         -> a new booth_uid (a genuinely new or split station)

Splits are detected after the fact: when two stations in the newer list both
match one older station well, the pair is marked match_method='split' and
multi-year comparisons sum their votes onto the surviving booth_uid.

    python -m ingest.crosswalk --election VS-2019
    python -m ingest.crosswalk --election VS-2019 --apply
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field

from common.logging_setup import get_logger
from common.similarity import jaro_winkler, token_overlap
from common.textnorm import building_type, canonical_building, to_latin

log = get_logger(__name__)

AUTO_ACCEPT = 0.85
REVIEW_FLOOR = 0.65
TYPE_MISMATCH_CAP = 0.60

W_BUILDING = 0.50
W_PLACE = 0.30
W_PART = 0.20


@dataclass
class Station:
    ps_number: int
    building: str = ""
    place: str = ""
    roll_part: int | None = None
    booth_uid: str | None = None

    @property
    def building_key(self) -> str:
        return to_latin(canonical_building(self.building))

    @property
    def place_key(self) -> str:
        return to_latin(self.place)

    @property
    def btype(self) -> str | None:
        return building_type(self.building)


@dataclass
class Match:
    ps_number: int
    booth_uid: str | None
    score: float
    method: str
    components: dict = field(default_factory=dict)
    runner_up: tuple[str, float] | None = None


def score_pair(old: Station, new: Station) -> tuple[float, dict]:
    """Weighted similarity, normalised over the components actually available.

    Village names and roll-part numbers are printed inconsistently across
    revisions. Scoring a missing component as zero would drag a perfect
    building-and-village match down to 0.80 - below the auto-accept threshold -
    purely because one list omitted the part number. So each component
    contributes only if both sides carry it, and the weights are renormalised
    over what is present. With all three present this is exactly the LLD 4.4
    formula.
    """
    have_place = bool(old.place_key and new.place_key)
    have_part = old.roll_part is not None and new.roll_part is not None

    b = jaro_winkler(old.building_key, new.building_key)
    p = jaro_winkler(old.place_key, new.place_key) if have_place else 0.0
    part = 1.0 if (have_part and old.roll_part == new.roll_part) else 0.0

    weighted = W_BUILDING * b
    total_weight = W_BUILDING
    if have_place:
        weighted += W_PLACE * p
        total_weight += W_PLACE
    if have_part:
        weighted += W_PART * part
        total_weight += W_PART
    score = weighted / total_weight

    capped = False
    if old.btype and new.btype and old.btype != new.btype:
        score = min(score, TYPE_MISMATCH_CAP)
        capped = True

    return round(score, 4), {
        "building": round(b, 4),
        "place": round(p, 4) if have_place else None,
        "roll_part": part if have_part else None,
        "type_mismatch_capped": capped,
        "token_overlap": round(token_overlap(old.building_key, new.building_key), 4),
    }


def match_stations(old_list: list[Station], anchor: list[Station]) -> list[Match]:
    """Match each station in `old_list` to its best anchor station."""
    matches: list[Match] = []
    for old in old_list:
        scored = []
        for new in anchor:
            s, comp = score_pair(old, new)
            scored.append((s, new, comp))
        scored.sort(key=lambda x: x[0], reverse=True)

        if not scored:
            matches.append(Match(old.ps_number, None, 0.0, "new"))
            continue

        best_score, best, comp = scored[0]
        runner = (scored[1][1].booth_uid or "", round(scored[1][0], 4)) if len(scored) > 1 else None

        if best_score >= AUTO_ACCEPT:
            method = "exact" if best_score >= 0.995 else "fuzzy"
        elif best_score >= REVIEW_FLOOR:
            method = "review"
        else:
            method = "new"

        matches.append(Match(
            ps_number=old.ps_number,
            booth_uid=best.booth_uid if method != "new" else None,
            score=best_score, method=method, components=comp, runner_up=runner,
        ))
    return matches


def detect_splits(matches: list[Match]) -> set[str]:
    """booth_uids that more than one older station matched onto.

    A one-to-many match means the station was split (or merged, read the other
    way). Comparisons across such a booth must use summed votes, which
    mv_result_booth_party already does by grouping on booth_uid.
    """
    counts: dict[str, int] = {}
    for m in matches:
        if m.booth_uid and m.method in {"exact", "fuzzy"}:
            counts[m.booth_uid] = counts.get(m.booth_uid, 0) + 1
    return {uid for uid, n in counts.items() if n > 1}


# --------------------------------------------------------------------------
# Database glue
# --------------------------------------------------------------------------

def load_stations(election_label: str) -> list[Station]:
    from common.db import query

    rows = query(
        "SELECT p.ps_number, p.building, p.village_or_locality, p.area_hint, p.roll_part, x.booth_uid "
        "FROM ps_list_entry p "
        "JOIN election e ON e.election_id = p.election_id "
        "LEFT JOIN booth_crosswalk x ON x.election_id = p.election_id AND x.ps_number = p.ps_number "
        "WHERE e.label = %s ORDER BY p.ps_number",
        (election_label,),
    )
    return [
        Station(
            ps_number=r["ps_number"],
            building=r["building"] or "",
            place=r["village_or_locality"] or r["area_hint"] or "",
            roll_part=r["roll_part"],
            booth_uid=r["booth_uid"],
        )
        for r in rows
    ]


def anchor_stations() -> list[Station]:
    """The booth table itself is the anchor once parse_pslist --anchor has run."""
    from common.db import query

    rows = query(
        "SELECT booth_uid, current_ps_number, building, village_or_locality FROM booth "
        "WHERE is_active ORDER BY current_ps_number NULLS LAST, booth_uid"
    )
    return [
        Station(
            ps_number=r["current_ps_number"] or 0,
            building=r["building"] or "",
            place=r["village_or_locality"] or "",
            booth_uid=r["booth_uid"],
        )
        for r in rows
    ]


def apply_matches(election_label: str, matches: list[Match], splits: set[str],
                  next_uid_start: int) -> dict:
    from common.db import connection

    stats = {"accepted": 0, "review": 0, "new": 0}
    counter = next_uid_start

    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT election_id FROM election WHERE label = %s", (election_label,))
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"unknown election label {election_label!r}")
        eid = row["election_id"]

        for m in matches:
            evidence = json.dumps(
                {"components": m.components, "runner_up": m.runner_up, "score": m.score},
                ensure_ascii=False,
            )

            if m.method in {"exact", "fuzzy"} and m.booth_uid:
                method = "split" if m.booth_uid in splits else m.method
                cur.execute(
                    "INSERT INTO booth_crosswalk (election_id, ps_number, booth_uid, match_method, "
                    "confidence, reviewed, evidence) VALUES (%s, %s, %s, %s, %s, false, %s) "
                    "ON CONFLICT (election_id, ps_number) DO UPDATE SET booth_uid = EXCLUDED.booth_uid, "
                    "match_method = EXCLUDED.match_method, confidence = EXCLUDED.confidence, "
                    "evidence = EXCLUDED.evidence WHERE booth_crosswalk.reviewed = false",
                    (eid, m.ps_number, m.booth_uid, method, m.score, evidence),
                )
                stats["accepted"] += 1

            elif m.method == "review":
                cur.execute(
                    "INSERT INTO review_queue (kind, ref, payload, note) VALUES "
                    "('crosswalk', %s, %s, %s) ",
                    (f"{election_label}#PS{m.ps_number}", evidence,
                     f"PS {m.ps_number} -> {m.booth_uid} at {m.score:.3f} "
                     f"(between {REVIEW_FLOOR} and {AUTO_ACCEPT}) - confirm or correct"),
                )
                stats["review"] += 1

            else:
                # Genuinely new or unmatchable station: give it its own booth_uid
                # so its votes are not silently dropped from AC totals.
                counter += 1
                new_uid = f"B9{counter:03d}"
                cur.execute(
                    "SELECT area_id FROM booth ORDER BY booth_uid LIMIT 1"
                )
                fallback = cur.fetchone()
                if fallback is None:
                    log.error("no booths exist yet - run parse_pslist --anchor first")
                    break
                cur.execute(
                    "INSERT INTO booth (booth_uid, area_id, building, notes, is_active) "
                    "VALUES (%s, %s, %s, %s, false) ON CONFLICT (booth_uid) DO NOTHING",
                    (new_uid, fallback["area_id"], "",
                     f"created by crosswalk for {election_label} PS {m.ps_number}; "
                     f"area needs manual assignment"),
                )
                cur.execute(
                    "INSERT INTO booth_crosswalk (election_id, ps_number, booth_uid, match_method, "
                    "confidence, reviewed, evidence) VALUES (%s, %s, %s, 'new', %s, false, %s) "
                    "ON CONFLICT (election_id, ps_number) DO NOTHING",
                    (eid, m.ps_number, new_uid, m.score, evidence),
                )
                cur.execute(
                    "INSERT INTO review_queue (kind, ref, payload, note) VALUES "
                    "('crosswalk', %s, %s, %s)",
                    (f"{election_label}#PS{m.ps_number}", evidence,
                     f"PS {m.ps_number}: best match only {m.score:.3f} - created {new_uid}, "
                     f"assign it to a panchayat or ward"),
                )
                stats["new"] += 1
    return stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Crosswalk an older PS list onto the anchor booths")
    ap.add_argument("--election", required=True, help="election label to crosswalk, e.g. VS-2019")
    ap.add_argument("--apply", action="store_true", help="write booth_crosswalk rows")
    ap.add_argument("--report", action="store_true", help="print every match, not just the summary")
    args = ap.parse_args(argv)

    anchor = anchor_stations()
    if not anchor:
        log.error("no anchor booths - run parse_pslist with --anchor on the newest PS list first")
        return 2

    old = load_stations(args.election)
    if not old:
        log.error("no ps_list_entry rows for %s - run parse_pslist --load for that election first",
                  args.election)
        return 2

    matches = match_stations(old, anchor)
    splits = detect_splits(matches)

    buckets = {"exact": 0, "fuzzy": 0, "review": 0, "new": 0}
    for m in matches:
        buckets[m.method] += 1
    auto = buckets["exact"] + buckets["fuzzy"]
    pct = 100.0 * auto / len(matches) if matches else 0.0

    log.info("%s: %d station(s) against %d anchor booth(s)", args.election, len(old), len(anchor))
    log.info("  auto-accepted : %4d (%.1f%%)   [exact %d, fuzzy %d]",
             auto, pct, buckets["exact"], buckets["fuzzy"])
    log.info("  needs review  : %4d", buckets["review"])
    log.info("  new booth     : %4d", buckets["new"])
    log.info("  splits/merges : %4d anchor booth(s) matched by more than one station", len(splits))
    if pct < 90:
        log.warning("auto-crosswalk below the 90%% acceptance target in LLD 14 (S0). "
                    "Check the PS list parse before trusting any multi-year comparison.")

    if args.report:
        for m in sorted(matches, key=lambda x: x.score):
            log.info("  PS %3d -> %-8s %.3f %-6s %s", m.ps_number, m.booth_uid or "-",
                     m.score, m.method, m.components)

    if args.apply:
        stats = apply_matches(args.election, matches, splits, next_uid_start=0)
        log.info("applied: %(accepted)d accepted, %(review)d queued for review, %(new)d new", stats)
    else:
        log.info("dry run - nothing written. Re-run with --apply to write.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
