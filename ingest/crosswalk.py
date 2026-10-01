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


# Master prompt 3.3: a split is one old station mapping to several new booths
# at or above this score. Lower than AUTO_ACCEPT because a split station's
# building name is often abbreviated differently on each of the new rows.
SPLIT_FLOOR = 0.75


def detect_splits(matches: list[Match]) -> set[str]:
    """booth_uids that more than one older station matched onto.

    Read from the older list's side this is a merge: several old stations now
    share one booth. Read from the newer list's side it is a split. Both need
    the comparison to be done on the aggregated group rather than on one member
    of it, which is why `booth_lineage` records the relationship and the swing
    views return NULL until it is aggregated.
    """
    counts: dict[str, int] = {}
    for m in matches:
        if m.booth_uid and m.score >= SPLIT_FLOOR:
            counts[m.booth_uid] = counts.get(m.booth_uid, 0) + 1
    return {uid for uid, n in counts.items() if n > 1}


def lineage_rows(matches: list[Match], splits: set[str]) -> list[dict]:
    """Rows for `booth_lineage`, one per (old station, new booth) pair in a
    split or merge group.

    `weight` is the share of the old station's electorate attributed to this new
    booth. Without a published split ratio - the CEO does not publish one - an
    equal split across the group is the only defensible assumption, and it is
    recorded as such so a later correction is a data edit rather than a code
    change.
    """
    grouped: dict[str, list[Match]] = {}
    for m in matches:
        if m.booth_uid in splits:
            grouped.setdefault(m.booth_uid, []).append(m)

    rows = []
    for uid, group in grouped.items():
        weight = round(1.0 / len(group), 4)
        for m in group:
            rows.append({
                "old_ps": m.ps_number,
                "new_booth_uid": uid,
                # Several old stations onto one new booth is a merge from the
                # old list's point of view.
                "kind": "merge" if len(group) > 1 else "split",
                "weight": weight,
                "note": (f"{len(group)} stations map onto {uid} at >= {SPLIT_FLOOR}; "
                         "weight is an equal split, no published ratio exists"),
            })
    return rows


# --------------------------------------------------------------------------
# Database glue
# --------------------------------------------------------------------------

def load_stations(election_label: str, ac_id: int) -> list[Station]:
    from common.db import query

    rows = query(
        "SELECT p.ps_number, p.building, p.village_or_locality, p.area_hint, p.roll_part, "
        "x.booth_uid FROM ps_list_entry p "
        "JOIN election e ON e.election_id = p.election_id "
        "LEFT JOIN booth_crosswalk x ON x.election_id = p.election_id "
        "                           AND x.ps_number = p.ps_number "
        "WHERE e.label = %s AND p.ac_id = %s ORDER BY p.ps_number",
        (election_label, ac_id),
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


def anchor_stations(ac_id: int) -> list[Station]:
    """The booth table itself is the anchor once parse_pslist --anchor has run."""
    from common.db import query

    rows = query(
        "SELECT booth_uid, current_ps_number, building, village_or_locality, roll_part "
        "FROM booth WHERE is_active AND ac_id = %s "
        "ORDER BY current_ps_number NULLS LAST, booth_uid",
        (ac_id,),
    )
    return [
        Station(
            ps_number=r["current_ps_number"] or 0,
            building=r["building"] or "",
            place=r["village_or_locality"] or "",
            # C10: `booth` did not carry roll_part, so
            # `old.roll_part is not None and new.roll_part is not None` was
            # always False and the documented 0.20 roll-part term never
            # contributed - every score was really 62.5% building / 37.5% place,
            # with the renormalisation doing all the work. 0014 adds the column;
            # this supplies it.
            roll_part=r["roll_part"],
            booth_uid=r["booth_uid"],
        )
        for r in rows
    ]


def apply_matches(election_label: str, ac_id: int, ac_number: int,
                  matches: list[Match], splits: set[str]) -> dict:
    """Write the crosswalk for one election, in one transaction.

    Three audit findings live here.

    **B2, the silent data loss.** A station scoring between REVIEW_FLOOR and
    AUTO_ACCEPT used to write *only* a review_queue row and no
    `booth_crosswalk` row at all - and every materialized view reaches results
    through an inner join on that table, so those stations' votes vanished from
    every rollup with no error. The HLD expects 10-20% of booths to need manual
    matching, so a 2019 comparison was built on 80-90% of the constituency while
    presenting itself as complete. The row is now written with its real
    sub-threshold confidence and `reviewed = false`: the confidence column and
    the weak-crosswalk counter already existed to carry exactly this
    uncertainty, and dropping the row destroyed information instead of flagging
    it. The swing views withhold a number for such a booth until a human
    reviews it, while the booth itself still appears everywhere.

    **B3, the colliding UIDs.** `main()` called this with `next_uid_start=0`
    hardcoded, so new booths were minted as B9001, B9002 ... from a counter that
    restarted at zero on every invocation. Crosswalking VS-2019 produced B9001;
    crosswalking VS-2014 next produced B9001 again, and because the booth insert
    was ON CONFLICT DO NOTHING the existing row survived while the new crosswalk
    row pointed at a booth created for a completely different polling station.
    Two unrelated stations' votes were then summed onto one booth_uid by
    mv_result_booth_party's GROUP BY. UIDs now come from the per-AC sequence.

    **C16, the doubling queue.** Both branches inserted into review_queue with
    no dedupe, and the runbook tells operators to re-run after fixes, so the
    queue doubled every time. 0014 adds a unique index on (kind, ref).
    """
    from common.db import connection

    stats = {"accepted": 0, "review": 0, "new": 0, "lineage": 0}

    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT election_id FROM election WHERE label = %s AND ac_id = %s",
            (election_label, ac_id),
        )
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"unknown election label {election_label!r} for AC {ac_number}")
        eid = row["election_id"]

        for m in matches:
            evidence = json.dumps(
                {"components": m.components, "runner_up": m.runner_up, "score": m.score},
                ensure_ascii=False,
            )

            if m.booth_uid and m.method in {"exact", "fuzzy", "review"}:
                # One branch for everything that matched. The band decides the
                # confidence and whether a human needs to look, not whether a
                # row exists at all.
                if m.booth_uid in splits:
                    method = "split"
                elif m.method == "review":
                    method = "fuzzy"
                else:
                    method = m.method

                cur.execute(
                    "INSERT INTO booth_crosswalk (election_id, ac_id, ps_number, booth_uid, "
                    "match_method, confidence, reviewed, evidence) "
                    "VALUES (%s, %s, %s, %s, %s, %s, false, %s) "
                    "ON CONFLICT (election_id, ps_number) DO UPDATE SET "
                    "  booth_uid = EXCLUDED.booth_uid, match_method = EXCLUDED.match_method, "
                    "  confidence = EXCLUDED.confidence, evidence = EXCLUDED.evidence, "
                    "  ac_id = EXCLUDED.ac_id "
                    "WHERE booth_crosswalk.reviewed = false",
                    (eid, ac_id, m.ps_number, m.booth_uid, method, m.score, evidence),
                )

                if m.method == "review":
                    stats["review"] += 1
                    _queue(
                        cur, ac_id, election_label, m, evidence,
                        f"PS {m.ps_number} -> {m.booth_uid} at {m.score:.3f}, between "
                        f"{REVIEW_FLOOR} and {AUTO_ACCEPT}. The row is loaded with this "
                        "confidence and reviewed=false, so the booth appears everywhere "
                        "but its swing is withheld until you confirm or correct it.",
                    )
                else:
                    stats["accepted"] += 1

            else:
                # Genuinely new or unmatchable: mint a booth so its votes are
                # not silently dropped from the AC totals.
                #
                # An existing binding is reused first. `next_booth_uid` mints a
                # fresh uid on every call, and the crosswalk insert below is
                # `ON CONFLICT ... DO NOTHING` - so a second run minted a new
                # uid, inserted a second `booth` row for the same station, and
                # then declined to repoint the crosswalk at it. Every re-run
                # leaked one orphan booth per unmatched station, and the runbook
                # tells operators to re-run after fixing the queue (finding N8).
                cur.execute(
                    "SELECT booth_uid FROM booth_crosswalk "
                    "WHERE election_id = %s AND ps_number = %s",
                    (eid, m.ps_number),
                )
                existing = cur.fetchone()
                if existing is not None:
                    new_uid = existing["booth_uid"]
                else:
                    cur.execute("SELECT next_booth_uid(%s) AS uid", (ac_number,))
                    new_uid = cur.fetchone()["uid"]

                cur.execute(
                    "SELECT area_id FROM booth WHERE ac_id = %s ORDER BY booth_uid LIMIT 1",
                    (ac_id,),
                )
                fallback = cur.fetchone()
                if fallback is None:
                    log.error("AC %s has no booths yet - run parse_pslist --anchor first",
                              ac_number)
                    break

                cur.execute(
                    "INSERT INTO booth (booth_uid, ac_id, area_id, building, notes, is_active) "
                    "VALUES (%s, %s, %s, %s, %s, false) ON CONFLICT (booth_uid) DO NOTHING",
                    (new_uid, ac_id, fallback["area_id"], "",
                     f"created by crosswalk for {election_label} PS {m.ps_number}; "
                     "area needs manual assignment"),
                )
                cur.execute(
                    "INSERT INTO booth_crosswalk (election_id, ac_id, ps_number, booth_uid, "
                    "match_method, confidence, reviewed, evidence) "
                    "VALUES (%s, %s, %s, %s, 'new', %s, false, %s) "
                    "ON CONFLICT (election_id, ps_number) DO NOTHING",
                    (eid, ac_id, m.ps_number, new_uid, m.score, evidence),
                )
                _queue(
                    cur, ac_id, election_label, m, evidence,
                    f"PS {m.ps_number}: best match only {m.score:.3f} - created {new_uid}, "
                    "which is inactive until you assign it to a panchayat or ward.",
                )
                stats["new"] += 1

        # Lineage for split and merge groups, so a multi-year comparison can be
        # made on the group rather than on one member of it.
        for lineage in lineage_rows(matches, splits):
            cur.execute(
                "INSERT INTO booth_lineage (old_election_id, old_ps, new_booth_uid, kind, "
                "weight, ac_id, note) VALUES (%s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (old_election_id, old_ps, new_booth_uid) DO UPDATE SET "
                "kind = EXCLUDED.kind, weight = EXCLUDED.weight, note = EXCLUDED.note",
                (eid, lineage["old_ps"], lineage["new_booth_uid"], lineage["kind"],
                 lineage["weight"], ac_id, lineage["note"]),
            )
            stats["lineage"] += 1

    return stats


def _queue(cur, ac_id: int, election_label: str, m: Match, evidence: str, note: str) -> None:
    """One review-queue row per (kind, ref), upserted rather than appended.

    C16: the runbook tells operators to re-run the crosswalk after fixing
    something, and these inserts had no dedupe, so every re-run doubled the
    queue. The WHERE clause also stops a re-run reopening an item somebody has
    already closed.
    """
    cur.execute(
        "INSERT INTO review_queue (kind, ref, ac_id, payload, note) "
        "VALUES ('crosswalk', %s, %s, %s, %s) "
        "ON CONFLICT (kind, ref) DO UPDATE SET payload = EXCLUDED.payload, "
        "note = EXCLUDED.note, ac_id = EXCLUDED.ac_id "
        "WHERE review_queue.status = 'open'",
        (f"{election_label}#PS{m.ps_number}", ac_id, evidence, note),
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Crosswalk an older PS list onto the anchor booths")
    ap.add_argument("--election", required=True, help="election label to crosswalk, e.g. VS-2019")
    # Required, not optional: a PS number is only unique within an AC, so
    # crosswalking without one would match stations across constituencies.
    ap.add_argument("--ac", type=int, required=True, help="constituency number, e.g. 32")
    ap.add_argument("--apply", action="store_true", help="write booth_crosswalk rows")
    ap.add_argument("--report", action="store_true", help="print every match, not just the summary")
    args = ap.parse_args(argv)

    from common.db import query_one

    ac = query_one("SELECT ac_id, ac_number, name_en FROM ac WHERE ac_number = %s", (args.ac,))
    if ac is None:
        log.error("no constituency numbered %s is seeded", args.ac)
        return 2

    anchor = anchor_stations(ac["ac_id"])
    if not anchor:
        log.error("AC %s has no anchor booths - run parse_pslist with --anchor on its newest "
                  "PS list first", args.ac)
        return 2

    old = load_stations(args.election, ac["ac_id"])
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
        stats = apply_matches(args.election, ac["ac_id"], ac["ac_number"], matches, splits)
        log.info("applied: %(accepted)d accepted, %(review)d loaded for review, %(new)d new, "
                 "%(lineage)d lineage row(s)", stats)
        log.info("Review-band matches are loaded with reviewed=false, so their booths appear "
                 "everywhere but their swing is withheld. Clear /admin/crosswalk before "
                 "trusting a multi-year comparison.")
    else:
        log.info("dry run - nothing written. Re-run with --apply to write.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
