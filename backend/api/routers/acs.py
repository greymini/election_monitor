"""The constituency list, and per-AC comparison (spec 2.5, 7.14).

`GET /acs` is what the header switcher and the compare page read. It is
deliberately built from base tables rather than from `mv_ac_summary` alone: a
constituency with nothing loaded must still appear in the switcher, with honest
zeroes and nulls, or five of the six would be invisible until someone loaded a
Form 20 for them. Where a headline figure exists it comes from the summary view;
where it does not the field is NULL and the UI renders "not loaded".
"""

from __future__ import annotations

from fastapi import APIRouter

from api.deps import CurrentAC, CurrentUser
from common.db import query, query_one

router = APIRouter(tags=["constituencies"])


@router.get("/acs")
def list_acs(user: CurrentUser) -> dict:
    """Every active constituency, with enough to render the switcher and the
    comparison table.

    A block-role user still sees the whole list - the switcher would be
    confusing otherwise - but `accessible` says which one they may open, and
    `current_ac` refuses the rest with a 403.
    """
    rows = query(
        """
        SELECT a.ac_id, a.ac_number, a.name_en, a.name_hi, a.reservation,
               a.verified, a.bypoll_due, a.vacancy_date, a.notes,
               p.pc_number, p.name_en AS pc_name_en,
               (SELECT string_agg(d.name_en, ' / ' ORDER BY ad.is_primary DESC, d.name_en)
                  FROM ac_district ad JOIN district d ON d.district_id = ad.district_id
                 WHERE ad.ac_id = a.ac_id) AS districts,
               (SELECT COUNT(*) FROM block bl WHERE bl.ac_id = a.ac_id) AS blocks,
               (SELECT COUNT(*) FROM booth b
                 WHERE b.ac_id = a.ac_id AND b.is_active) AS booths,
               (SELECT COUNT(DISTINCT rb.election_id) FROM result_booth rb
                 WHERE rb.ac_id = a.ac_id) AS elections_with_results,
               (SELECT COUNT(*) FROM roll_revision rr WHERE rr.ac_id = a.ac_id) AS roll_revisions,
               s.election_label, s.winner_party, s.runner_party, s.margin_votes,
               s.margin_pct, s.turnout_pct, s.electors, s.valid_votes,
               s.jlkm_share_pct, s.new_voter_pct, s.crosswalk_coverage_pct
        FROM ac a
        LEFT JOIN pc p ON p.pc_id = a.pc_id
        LEFT JOIN mv_ac_summary s ON s.ac_id = a.ac_id AND s.is_baseline
        WHERE a.is_active
        ORDER BY a.ac_number
        """
    )

    accessible: int | None = None
    if user.role == "block" and user.block_id is not None:
        owner = query_one("SELECT ac_id FROM block WHERE block_id = %s", (user.block_id,))
        accessible = owner["ac_id"] if owner else None

    for row in rows:
        row["accessible"] = accessible is None or row["ac_id"] == accessible
        # What the UI needs to decide between a number and "not loaded".
        row["has_booth_data"] = bool(row["booths"]) and bool(row["elections_with_results"])

    return {
        "acs": rows,
        "count": len(rows),
        "unverified": [r["ac_number"] for r in rows if not r["verified"]],
        "note": (
            "Constituencies marked unverified are seeded from secondary sources "
            "(MULTI_AC_EXPANSION_SPEC 1) and must be checked against ECI/CEO Jharkhand "
            "before any figure is used. Blank headline figures mean nothing is loaded "
            "for that constituency yet, not zero."
        ),
    }


@router.get("/acs/{ac_number}")
def ac_detail(user: CurrentUser, ac: CurrentAC) -> dict:
    """One constituency's identity and per-election headline rows.

    Separate from `/acs/{ac_number}/summary`, which carries the data-health
    strip and is what the overview page reads. This is the smaller payload the
    switcher and the compare page use.
    """
    return {
        "ac_number": ac.ac_number,
        "name_en": ac.name_en,
        "name_hi": ac.name_hi,
        "reservation": ac.reservation,
        "verified": ac.verified,
        "bypoll_due": ac.bypoll_due,
        "vacancy_date": ac.vacancy_date,
        "elections": query(
            "SELECT election_label, election_type, year, is_baseline, winner_party, "
            "       runner_party, margin_votes, margin_pct, turnout_pct, electors, "
            "       valid_votes, booths, jlkm_share_pct, new_voter_pct, "
            "       crosswalk_coverage_pct "
            "FROM mv_ac_summary WHERE ac_id = %s ORDER BY year DESC, election_type",
            (ac.ac_id,),
        ),
        "contests": query(
            "SELECT ev.label AS event_label, pa.abbr AS party_a, pb.abbr AS party_b, c.source "
            "FROM ac_contest c "
            "JOIN election_event ev ON ev.event_id = c.event_id "
            "JOIN party pa ON pa.party_id = c.party_a "
            "JOIN party pb ON pb.party_id = c.party_b "
            "WHERE c.ac_id = %s ORDER BY ev.year DESC",
            (ac.ac_id,),
        ),
    }


@router.get("/compare")
def compare(user: CurrentUser, metric: str | None = None) -> dict:
    """Every AC's headline series, for the comparison small multiples (spec 7.14).

    One query rather than one per AC: the page draws four sparklines per
    constituency and six round trips per chart would be absurd.
    """
    rows = query(
        """
        SELECT s.ac_id, a.ac_number, a.name_en, a.name_hi, a.verified,
               s.election_label, s.election_type, s.year, s.is_baseline,
               s.winner_party, s.runner_party, s.margin_votes, s.margin_pct,
               s.signed_margin_pct, s.turnout_pct, s.electors, s.valid_votes,
               s.jlkm_share_pct, s.new_voter_pct, s.crosswalk_coverage_pct, s.booths
        FROM mv_ac_summary s
        JOIN ac a ON a.ac_id = s.ac_id
        WHERE a.is_active AND s.election_type = 'VS'
        ORDER BY a.ac_number, s.year
        """
    )
    return {
        "rows": rows,
        "count": len(rows),
        "metric": metric,
        "note": (
            "Assembly elections only. A constituency with no rows has no results loaded; "
            "an empty cell is absence, not zero. Figures for unverified constituencies "
            "come from secondary sources."
        ),
    }
