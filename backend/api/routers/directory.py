"""Panchayat Directory (Places): LGD panchayats, BLO PS list, GP officials, poll calendar."""

from __future__ import annotations

from fastapi import APIRouter

from api.deps import CurrentAC, CurrentUser, scoped_block_id
from common.db import query

router = APIRouter(prefix="/acs/{ac_number}", tags=["directory"])

PANCHAYAT_SOURCE = "Local Government Directory, 01 Oct 2026"
BLO_SOURCE = "BLO list, AC 32 parts 276–385"
GP_SOURCE = "grampanchayat.jharkhand.gov.in"


@router.get("/directory/panchayats")
def list_panchayats(user: CurrentUser, ac: CurrentAC) -> dict:
    block_id = scoped_block_id(user)
    clauses, params = ["a.ac_id = %s", "a.kind = 'panchayat'"], [ac.ac_id]
    if block_id is not None:
        clauses.append("a.block_id = %s")
        params.append(block_id)
    rows = query(
        f"""
        SELECT a.area_id, a.name_en, a.name_hi, a.code AS lgd_code, bl.name_en AS block_en,
               (a.boundary IS NOT NULL) AS has_boundary,
               (SELECT COUNT(*) FROM area_alias aa WHERE aa.area_id = a.area_id) AS village_count,
               (SELECT COUNT(*) FROM gp_official g WHERE g.area_id = a.area_id) AS official_count,
               (SELECT lr.winner FROM local_result lr
                 JOIN election e ON e.election_id = lr.election_id
                WHERE lr.area_id = a.area_id AND e.label = 'PANCHAYAT-2022'
                  AND lr.seat_type = 'mukhiya' LIMIT 1) AS mukhiya_2022
        FROM area a
        JOIN block bl ON bl.block_id = a.block_id
        WHERE {' AND '.join(clauses)}
        ORDER BY bl.name_en, a.name_en
        """,
        params,
    )
    return {"rows": rows, "source": PANCHAYAT_SOURCE}


@router.get("/directory/panchayats/{area_id}/villages")
def list_villages(user: CurrentUser, ac: CurrentAC, area_id: int) -> dict:
    rows = query(
        """
        SELECT aa.alias AS name, aa.script, aa.source
        FROM area_alias aa
        JOIN area a ON a.area_id = aa.area_id
        WHERE aa.area_id = %s AND a.ac_id = %s
        ORDER BY aa.alias
        """,
        (area_id, ac.ac_id),
    )
    return {"rows": rows, "source": PANCHAYAT_SOURCE}


@router.get("/directory/polling-stations")
def list_polling_stations(user: CurrentUser, ac: CurrentAC) -> dict:
    block_id = scoped_block_id(user)
    clauses, params = ["p.ac_id = %s"], [ac.ac_id]
    if block_id is not None:
        clauses.append("p.block_id = %s")
        params.append(block_id)
    rows = query(
        f"""
        SELECT p.part_number, p.building_hi, p.village_hi, bl.name_en AS block_en,
               ar.name_en AS panchayat_en, p.match_status, p.match_score, p.note
        FROM ps_current_part p
        LEFT JOIN block bl ON bl.block_id = p.block_id
        LEFT JOIN area ar ON ar.area_id = p.area_id
        WHERE {' AND '.join(clauses)}
        ORDER BY p.part_number
        """,
        params,
    )
    return {
        "rows": rows,
        "source": BLO_SOURCE,
        "note": "Part numbers are from the current roll, not the 2024 Form 20 PS numbering.",
    }


@router.get("/directory/officials")
def list_officials(user: CurrentUser, ac: CurrentAC, area_id: int | None = None) -> dict:
    clauses, params = ["a.ac_id = %s"], [ac.ac_id]
    if area_id is not None:
        clauses.append("g.area_id = %s")
        params.append(area_id)
    block_id = scoped_block_id(user)
    if block_id is not None:
        clauses.append("a.block_id = %s")
        params.append(block_id)
    rows = query(
        f"""
        SELECT g.official_id, a.area_id, a.name_en AS panchayat_en, g.portal_role, g.office, g.name
        FROM gp_official g
        JOIN area a ON a.area_id = g.area_id
        WHERE {' AND '.join(clauses)}
        ORDER BY a.name_en, g.portal_role, g.name
        """,
        params,
    )
    return {"rows": rows, "source": GP_SOURCE}


@router.get("/directory/poll-calendar")
def poll_calendar(user: CurrentUser, ac: CurrentAC) -> dict:
    rows = query(
        """
        SELECT e.label, e.type, e.year, e.poll_date, e.phase, e.is_baseline, e.notes
        FROM election e
        WHERE e.ac_id = %s
        ORDER BY e.poll_date NULLS LAST, e.year DESC, e.type
        """,
        (ac.ac_id,),
    )
    return {"rows": rows, "source": "poll_dates.csv (seed)"}
