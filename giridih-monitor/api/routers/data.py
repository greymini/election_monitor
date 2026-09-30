"""Core data endpoints: summary, booths (GeoJSON), results, rolls, caste,
transfer, local elections (LLD 9)."""

from __future__ import annotations

import csv
import io
from datetime import date

from fastapi import APIRouter, HTTPException, Query, Response, status

from api.booth_card import build_booth_card
from api.deps import CurrentUser, StrategistUser, scoped_block_id
from common.db import query, query_one

router = APIRouter(tags=["data"])

# HLD 1: the seat fell vacant on 6 Sep 2026 and the ECI must poll within six months.
VACANCY_DATE = date(2026, 9, 6)
BYPOLL_DEADLINE = date(2027, 3, 6)


def _csv_response(rows: list[dict], filename: str) -> Response:
    buf = io.StringIO()
    if rows:
        writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/summary")
def summary(user: CurrentUser) -> dict:
    elections = query(
        "SELECT e.label, e.type, e.year, e.is_baseline, "
        "COUNT(DISTINCT w.booth_uid) AS booths, SUM(w.votes_counted) AS votes, "
        "SUM(w.electors) AS electors "
        "FROM election e LEFT JOIN mv_result_booth_wide w ON w.election_id = e.election_id "
        "GROUP BY e.label, e.type, e.year, e.is_baseline ORDER BY e.year DESC, e.type"
    )
    baseline = query_one(
        "SELECT e.label, SUM(w.jmm) AS jmm, SUM(w.bjp) AS bjp, SUM(w.jlkm) AS jlkm, "
        "SUM(w.nota) AS nota, SUM(w.electors) AS electors, SUM(w.votes_counted) AS votes "
        "FROM mv_result_booth_wide w JOIN election e ON e.election_id = w.election_id "
        "WHERE e.is_baseline GROUP BY e.label"
    )
    data_health = query_one(
        "SELECT (SELECT COUNT(*) FROM booth WHERE is_active) AS booths, "
        "(SELECT COUNT(*) FROM review_queue WHERE status = 'open') AS open_reviews, "
        "(SELECT COUNT(*) FROM booth_crosswalk WHERE confidence < 0.85 AND NOT reviewed) "
        "  AS weak_crosswalks, "
        "(SELECT MAX(revision_date) FROM roll_revision) AS latest_roll"
    )
    today = date.today()
    return {
        "constituency": {"code": "AC-32", "name_en": "Giridih", "name_hi": "गिरिडीह",
                         "parent_pc": "PC-11 Giridih"},
        "bypoll": {
            "vacancy_date": VACANCY_DATE,
            "deadline": BYPOLL_DEADLINE,
            "days_to_deadline": (BYPOLL_DEADLINE - today).days,
            "note": "The ECI must hold the poll within six months of the vacancy.",
        },
        "elections": elections,
        "baseline": baseline,
        "data_health": data_health,
        "scope": {"block_id": scoped_block_id(user), "sees_caste": user.sees_caste},
    }


@router.get("/booths")
def booths_geojson(
    user: CurrentUser,
    election_label: str | None = None,
    area_id: int | None = None,
    metric: str = Query("margin_pct", pattern=r"^[a-z_]{3,32}$"),
) -> dict:
    """Booth points as GeoJSON, carrying the metric the map is colouring by."""
    allowed_metrics = {
        "margin_pct", "turnout_pct", "new_voter_pct", "priority_score",
        "floating_pct", "margin_stddev", "electors",
    }
    if metric not in allowed_metrics:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"metric must be one of: {', '.join(sorted(allowed_metrics))}")

    clauses, params = ["b.is_active"], []
    block_id = scoped_block_id(user)
    if block_id is not None:
        clauses.append("a.block_id = %s")
        params.append(block_id)
    if area_id is not None:
        clauses.append("b.area_id = %s")
        params.append(area_id)
    where = " AND ".join(clauses)

    rows = query(
        f"""
        SELECT b.booth_uid, b.ps_name_hi, b.building, b.village_or_locality,
               b.current_ps_number, b.geocode_conf,
               ST_X(b.geom) AS lon, ST_Y(b.geom) AS lat,
               a.area_id, a.name_hi AS area_hi, a.name_en AS area_en, a.kind AS area_kind,
               a.block_id,
               p.margin_pct, p.turnout_pct, p.new_voter_pct, p.priority_score,
               p.floating_pct, p.margin_stddev, p.electors, p.winner_party, p.runner_party
        FROM booth b
        JOIN area a ON a.area_id = b.area_id
        LEFT JOIN mv_booth_priority p ON p.booth_uid = b.booth_uid
        WHERE {where}
        ORDER BY b.booth_uid
        """,
        params,
    )

    features = []
    for r in rows:
        geometry = None
        if r["lon"] is not None and r["lat"] is not None:
            geometry = {"type": "Point", "coordinates": [r["lon"], r["lat"]]}
        props = {k: v for k, v in r.items() if k not in {"lon", "lat"}}
        props["metric"] = r.get(metric)
        props["metric_name"] = metric
        features.append({"type": "Feature", "geometry": geometry, "properties": props})

    ungeocoded = sum(1 for f in features if f["geometry"] is None)
    return {
        "type": "FeatureCollection",
        "features": features,
        "meta": {"count": len(features), "ungeocoded": ungeocoded, "metric": metric,
                 "election_label": election_label},
    }


@router.get("/booths/{booth_uid}/card")
def booth_card(booth_uid: str, user: CurrentUser) -> dict:
    try:
        card = build_booth_card(booth_uid, include_caste=user.sees_caste)
    except LookupError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No booth {booth_uid}") from None
    block_id = scoped_block_id(user)
    if block_id is not None and card["booth"]["block_id"] != block_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This booth is outside your block")
    return card


@router.get("/results/{election_label}/booths")
def results_by_booth(election_label: str, user: CurrentUser, area_id: int | None = None,
                     format: str = Query("json", pattern="^(json|csv)$")):
    """Full Form 20 table for one election, sortable and exportable (module 3)."""
    clauses, params = ["w.election_label = %s"], [election_label]
    block_id = scoped_block_id(user)
    if block_id is not None:
        clauses.append("w.block_id = %s")
        params.append(block_id)
    if area_id is not None:
        clauses.append("w.area_id = %s")
        params.append(area_id)

    rows = query(
        f"""
        SELECT w.booth_uid, w.ps_numbers, a.name_hi AS area_hi, a.name_en AS area_en,
               bl.name_en AS block_en, b.building, b.village_or_locality,
               w.electors, w.votes_counted, w.turnout_pct,
               w.jmm, w.bjp, w.ajsu, w.jlkm, w.inc, w.rjd, w.jvm, w.others, w.nota,
               w.winner_party, w.runner_party, w.margin_votes, w.margin_pct,
               w.source_doc, w.source_page
        FROM mv_result_booth_wide w
        JOIN booth b ON b.booth_uid = w.booth_uid
        JOIN area a ON a.area_id = w.area_id
        JOIN block bl ON bl.block_id = a.block_id
        WHERE {' AND '.join(clauses)}
        ORDER BY w.booth_uid
        """,
        params,
    )
    if format == "csv":
        return _csv_response(rows, f"{election_label.replace(' ', '_')}_booths.csv")
    return {"election_label": election_label, "rows": rows, "count": len(rows)}


@router.get("/results/{election_label}/areas")
def results_by_area(election_label: str, user: CurrentUser,
                    format: str = Query("json", pattern="^(json|csv)$")):
    clauses, params = ["election_label = %s"], [election_label]
    block_id = scoped_block_id(user)
    if block_id is not None:
        clauses.append("block_id = %s")
        params.append(block_id)
    rows = query(
        f"SELECT * FROM mv_area_rollup WHERE {' AND '.join(clauses)} "
        f"ORDER BY block_id, area_name_en",
        params,
    )
    if format == "csv":
        return _csv_response(rows, f"{election_label.replace(' ', '_')}_areas.csv")
    return {"election_label": election_label, "rows": rows, "count": len(rows)}


@router.get("/rolls/changes")
def roll_changes(user: CurrentUser, revision_label: str | None = None,
                 area_id: int | None = None,
                 format: str = Query("json", pattern="^(json|csv)$")):
    """New and deleted voters per booth per revision (module 4).

    Deletions matter as much as additions if the roll is post-SIR, so the
    revision's is_post_sir flag rides along with every row.
    """
    clauses, params = ["true"], []
    block_id = scoped_block_id(user)
    if block_id is not None:
        clauses.append("a.block_id = %s")
        params.append(block_id)
    if area_id is not None:
        clauses.append("b.area_id = %s")
        params.append(area_id)
    if revision_label:
        clauses.append("r.label = %s")
        params.append(revision_label)

    rows = query(
        f"""
        SELECT c.booth_uid, r.label AS revision, r.revision_date, r.is_post_sir,
               a.name_hi AS area_hi, a.name_en AS area_en, a.block_id,
               c.additions, c.deletions, c.modifications,
               c.add_18_19, c.add_female, c.del_death, c.del_shifted, c.del_other,
               s.electors,
               ROUND(100.0 * c.additions / NULLIF(s.electors, 0), 2) AS additions_pct,
               ROUND(100.0 * c.deletions / NULLIF(s.electors, 0), 2) AS deletions_pct
        FROM roll_change c
        JOIN roll_revision r ON r.revision_id = c.revision_id
        JOIN booth b ON b.booth_uid = c.booth_uid
        JOIN area a ON a.area_id = b.area_id
        LEFT JOIN roll_snapshot s ON s.booth_uid = c.booth_uid AND s.revision_id = c.revision_id
        WHERE {' AND '.join(clauses)}
        ORDER BY r.revision_date DESC, c.booth_uid
        """,
        params,
    )
    if format == "csv":
        return _csv_response(rows, "roll_changes.csv")
    return {"rows": rows, "count": len(rows)}


@router.get("/rolls/revisions")
def roll_revisions(user: CurrentUser) -> dict:
    return {"rows": query(
        "SELECT revision_id, label, revision_date, is_post_sir, is_mother "
        "FROM roll_revision ORDER BY revision_date DESC"
    )}


@router.get("/caste")
def caste(user: StrategistUser, area_id: int | None = None, booth_uid: str | None = None,
          min_conf: float = Query(0.4, ge=0.0, le=1.0),
          source: str = Query("blend", pattern="^(blend|surname|census|survey)$")) -> dict:
    """Aggregate community estimates only (HLD 5). Hidden from block-role users."""
    clauses, params = ["ce.source = %s", "ce.confidence >= %s"], [source, min_conf]
    if area_id is not None:
        clauses.append("b.area_id = %s")
        params.append(area_id)
    if booth_uid is not None:
        clauses.append("ce.booth_uid = %s")
        params.append(booth_uid)

    rows = query(
        f"""
        SELECT ce.booth_uid, b.area_id, a.name_hi AS area_hi, a.name_en AS area_en,
               c.name_en AS community_en, c.name_hi AS community_hi, c.category,
               ce.est_count, ce.est_pct, ce.confidence, ce.source
        FROM caste_estimate ce
        JOIN community c ON c.community_id = ce.community_id
        JOIN booth b ON b.booth_uid = ce.booth_uid
        JOIN area a ON a.area_id = b.area_id
        WHERE {' AND '.join(clauses)}
        ORDER BY ce.booth_uid, ce.est_pct DESC NULLS LAST
        """,
        params,
    )
    return {
        "rows": rows,
        "count": len(rows),
        "min_conf": min_conf,
        "disclaimer": (
            "Estimates at booth level only, derived from surname inference, Census 2011 "
            "proportions and any ground survey. No individual voter is tagged with a community. "
            "Anything below 0.4 confidence is too weak to act on. Associations with vote share "
            "are ecological correlations, not statements about how any community voted."
        ),
    }


@router.get("/transfer")
def transfer(user: StrategistUser, year: int = 2024, area_id: int | None = None,
             format: str = Query("json", pattern="^(json|csv)$")):
    """Where the Lok Sabha vote went at the assembly poll (module 6)."""
    clauses, params = ["t.year = %s"], [year]
    if area_id is not None:
        clauses.append("b.area_id = %s")
        params.append(area_id)

    rows = query(
        f"""
        SELECT t.booth_uid, a.name_hi AS area_hi, a.name_en AS area_en, a.block_id,
               t.party, t.ls_votes, t.vs_votes, t.delta_votes,
               t.ls_share_pct, t.vs_share_pct, t.delta_share_pct,
               f.floating_pct
        FROM mv_transfer_ls_vs t
        JOIN booth b ON b.booth_uid = t.booth_uid
        JOIN area a ON a.area_id = b.area_id
        LEFT JOIN mv_floating_vote f ON f.booth_uid = t.booth_uid AND f.year = t.year
        WHERE {' AND '.join(clauses)}
        ORDER BY f.floating_pct DESC NULLS LAST, t.booth_uid, t.party
        """,
        params,
    )
    if format == "csv":
        return _csv_response(rows, f"transfer_{year}.csv")
    return {
        "year": year, "rows": rows, "count": len(rows),
        "note": ("Lok Sabha figures here are the AC-32 segment of PC-11 Giridih, not the whole "
                 "parliamentary seat. floating_pct is the Pedersen index between the two polls."),
    }


@router.get("/local-results")
def local_results(user: CurrentUser, election_label: str | None = None,
                  seat_type: str | None = None) -> dict:
    """Panchayat and municipal results (module 7)."""
    clauses, params = ["true"], []
    if election_label:
        clauses.append("e.label = %s")
        params.append(election_label)
    if seat_type:
        clauses.append("lr.seat_type = %s")
        params.append(seat_type)
    block_id = scoped_block_id(user)
    if block_id is not None:
        clauses.append("a.block_id = %s")
        params.append(block_id)

    rows = query(
        f"""
        SELECT lr.local_result_id, e.label AS election, lr.seat_type, lr.seat_name,
               a.name_hi AS area_hi, a.name_en AS area_en,
               lr.winner, lr.runner_up, p.abbr AS tagged_party, lr.tag_source,
               lr.tag_confidence, lr.votes, lr.runner_up_votes, lr.margin, lr.source_doc
        FROM local_result lr
        JOIN election e ON e.election_id = lr.election_id
        LEFT JOIN area a ON a.area_id = lr.area_id
        LEFT JOIN party p ON p.party_id = lr.tagged_party_id
        WHERE {' AND '.join(clauses)}
        ORDER BY e.year DESC, lr.seat_type, lr.seat_name
        """,
        params,
    )
    return {
        "rows": rows, "count": len(rows),
        "note": ("Panchayat elections are contested without party symbols. Any party shown here "
                 "is a manual tag; tag_source records who assigned it and on what basis."),
    }


@router.get("/priority")
def priority(user: CurrentUser, limit: int = Query(50, ge=1, le=500),
             format: str = Query("json", pattern="^(json|csv)$")):
    """Booth priority ranking (module 10)."""
    clauses, params = ["true"], []
    block_id = scoped_block_id(user)
    if block_id is not None:
        clauses.append("a.block_id = %s")
        params.append(block_id)
    params.append(limit)

    rows = query(
        f"""
        SELECT p.booth_uid, a.name_hi AS area_hi, a.name_en AS area_en, a.block_id,
               b.building, p.margin_pct, p.margin_votes, p.electors, p.turnout_pct,
               p.new_voter_pct, p.additions, p.margin_stddev, p.floating_pct,
               p.priority_score, p.priority_quartile, p.winner_party, p.runner_party
        FROM mv_booth_priority p
        JOIN booth b ON b.booth_uid = p.booth_uid
        JOIN area a ON a.area_id = p.area_id
        WHERE {' AND '.join(clauses)}
        ORDER BY p.priority_score DESC LIMIT %s
        """,
        params,
    )
    if format == "csv":
        return _csv_response(rows, "booth_priority.csv")
    return {
        "rows": rows, "count": len(rows),
        "formula": ("0.35 x tight margin + 0.25 x new-voter share + 0.20 x volatility "
                    "+ 0.20 x floating vote, each percentile-ranked across booths."),
    }


@router.get("/areas")
def areas(user: CurrentUser) -> dict:
    clauses, params = ["true"], []
    block_id = scoped_block_id(user)
    if block_id is not None:
        clauses.append("a.block_id = %s")
        params.append(block_id)
    return {
        "blocks": query("SELECT block_id, name_en, name_hi, kind FROM block ORDER BY block_id"),
        "areas": query(
            f"SELECT a.area_id, a.block_id, a.kind, a.name_en, a.name_hi, a.code, "
            f"COUNT(b.booth_uid) AS booths "
            f"FROM area a LEFT JOIN booth b ON b.area_id = a.area_id AND b.is_active "
            f"WHERE {' AND '.join(clauses)} "
            f"GROUP BY a.area_id, a.block_id, a.kind, a.name_en, a.name_hi, a.code "
            f"ORDER BY a.block_id, a.kind, a.name_en",
            params,
        ),
        "elections": query("SELECT election_id, label, type, year, is_baseline "
                           "FROM election ORDER BY year DESC, type"),
        "parties": query("SELECT party_id, abbr, name_en, name_hi, alliance_2024, colour "
                         "FROM party ORDER BY party_id"),
    }
