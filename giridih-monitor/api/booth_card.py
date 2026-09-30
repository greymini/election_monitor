"""The booth card: everything known about one booth, in one payload.

Used by GET /booths/{uid}/card, by the map side drawer, and by the chatbot's
get_booth_card tool - one implementation so all three agree.
"""

from __future__ import annotations

from common.db import query, query_one


def build_booth_card(booth_uid: str, include_caste: bool = True,
                     ac_id: int | None = None) -> dict:
    """Everything known about one booth.

    `ac_id`, when given, is part of the lookup rather than a filter applied
    afterwards: a booth_uid carries its AC number now, so a mismatch means the
    caller asked the wrong constituency for it and should get a 404, not
    another AC's booth.
    """
    booth = query_one(
        "SELECT b.booth_uid, b.ps_name_hi, b.building, b.village_or_locality, "
        "b.current_ps_number, b.geocode_conf, "
        "a.area_id, a.name_en AS area_en, a.name_hi AS area_hi, a.kind AS area_kind, "
        "bl.block_id, bl.name_en AS block_en, bl.name_hi AS block_hi "
        "FROM booth b JOIN area a ON a.area_id = b.area_id "
        "JOIN block bl ON bl.block_id = a.block_id "
        "WHERE b.booth_uid = %s AND (%s::INT IS NULL OR b.ac_id = %s)",
        (booth_uid, ac_id, ac_id),
    )
    if booth is None:
        raise LookupError(booth_uid)

    results = query(
        "SELECT election_label, election_type, election_year, electors, votes_counted, "
        "jmm, bjp, ajsu, jlkm, inc, rjd, jvm, others, nota, winner_party, runner_party, "
        "margin_votes, margin_pct, turnout_pct, source_doc, source_page, ps_numbers "
        "FROM mv_result_booth_wide WHERE booth_uid = %s "
        "ORDER BY election_year DESC, election_type",
        (booth_uid,),
    )

    rolls = query(
        "SELECT r.label, r.revision_date, s.electors, s.male, s.female, s.other, "
        "s.age_18_19, s.age_20_29, s.age_30_39, s.age_40_49, s.age_50_59, s.age_60p "
        "FROM roll_snapshot s JOIN roll_revision r ON r.revision_id = s.revision_id "
        "WHERE s.booth_uid = %s ORDER BY r.revision_date DESC LIMIT 6",
        (booth_uid,),
    )

    changes = query(
        "SELECT r.label, r.revision_date, r.is_post_sir, c.additions, c.deletions, "
        "c.modifications, c.add_18_19, c.add_female, c.del_death, c.del_shifted "
        "FROM roll_change c JOIN roll_revision r ON r.revision_id = c.revision_id "
        "WHERE c.booth_uid = %s ORDER BY r.revision_date DESC LIMIT 8",
        (booth_uid,),
    )

    new_voters = query_one(
        "SELECT electors_now, additions, deletions, net_change, add_18_19, add_female, "
        "new_voter_pct, deleted_pct FROM mv_new_voter_share WHERE booth_uid = %s",
        (booth_uid,),
    )

    priority = query_one(
        "SELECT margin_pct, margin_votes, new_voter_pct, margin_stddev, floating_pct, "
        "priority_score, priority_quartile, winner_party, runner_party "
        "FROM mv_booth_priority WHERE booth_uid = %s",
        (booth_uid,),
    )

    crosswalk = query(
        "SELECT e.label, x.ps_number, x.match_method, x.confidence, x.reviewed "
        "FROM booth_crosswalk x JOIN election e ON e.election_id = x.election_id "
        "WHERE x.booth_uid = %s ORDER BY e.year DESC",
        (booth_uid,),
    )

    card = {
        "booth": booth,
        "results": results,
        "roll_snapshots": rolls,
        "roll_changes": changes,
        "new_voters": new_voters,
        "priority": priority,
        "crosswalk": crosswalk,
        "caveats": _caveats(crosswalk),
    }

    if include_caste:
        card["caste_estimate"] = query(
            "SELECT c.name_en, c.name_hi, c.category, ce.est_count, ce.est_pct, "
            "ce.confidence, ce.source FROM caste_estimate ce "
            "JOIN community c ON c.community_id = ce.community_id "
            "WHERE ce.booth_uid = %s AND ce.source = 'blend' "
            "ORDER BY ce.est_pct DESC NULLS LAST",
            (booth_uid,),
        )
        card["caste_note"] = (
            "Estimates only, at booth level. Derived from surname inference, Census 2011 "
            "proportions and any ground survey. No individual voter is tagged. Figures below "
            "0.4 confidence are too weak to use."
        )
    return card


def _caveats(crosswalk: list[dict]) -> list[str]:
    """Anything a reader must know before trusting the numbers above."""
    notes: list[str] = []
    weak = [c for c in crosswalk if (c["confidence"] or 0) < 0.85 and not c["reviewed"]]
    if weak:
        years = ", ".join(str(c["label"]) for c in weak)
        notes.append(
            f"Cross-year matching is unconfirmed for {years} "
            f"(confidence below 0.85, not yet reviewed). Treat swing against those years as provisional."
        )
    if any(c["match_method"] == "split" for c in crosswalk):
        notes.append(
            "This booth was split or merged at some point; votes from the related stations are "
            "summed so the comparison stays like-for-like."
        )
    return notes
