"""Core data endpoints: summary, booths (GeoJSON), results, rolls, caste,
transfer, local elections (LLD 9), all scoped to one constituency.

Every route here lives under `/acs/{ac_number}/` and takes `CurrentAC`, which
resolves the number and refuses an unknown one. The AC is applied to the query,
not just accepted as a parameter - the failure this guards against is a route
that takes an AC and then serves every constituency's rows under it.

`api/routers/legacy.py` keeps the old unscoped paths alive as 308 redirects to
`/acs/32/...` for one release, because bookmarks and the run guide both point at
them.
"""

from __future__ import annotations

import csv
import io
from datetime import date

from fastapi import APIRouter, HTTPException, Query, Response, status

from api.booth_card import build_booth_card
from api.deps import CurrentAC, CurrentUser, StrategistUser, scoped_block_id
from common.db import query, query_one

router = APIRouter(prefix="/acs/{ac_number}", tags=["data"])

# The vacancy and deadline used to be module constants here, which could only
# ever describe Giridih and would have reported its bypoll date for all six
# constituencies. They live on the `ac` row now.


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
def summary(user: CurrentUser, ac: CurrentAC) -> dict:
    # AC-level party totals per election, so the margin trend on the overview is
    # the loaded data rather than a constant compiled into the frontend. The
    # winner and margin are computed from the summed party columns here because
    # mv_result_booth_wide's own winner/margin are per booth.
    #
    # Two caveats, both being fixed in A-3 and neither introduced here: the party
    # columns are a fixed pivot (jmm/bjp/ajsu/jlkm/inc/rjd/jvm/others), so a
    # winner outside that set lands in `others`; and `votes_counted` excludes
    # NOTA while `total_valid` includes it, which is audit finding D1. Percentages
    # are therefore deliberately not computed here - the frontend shows vote
    # counts, and METRICS.md will own the percentage definitions.
    elections = query(
        "WITH totals AS ("
        "  SELECT e.election_id, e.label, e.type, e.year, e.is_baseline,"
        "         COUNT(DISTINCT w.booth_uid) AS booths,"
        "         SUM(w.votes_counted) AS votes,"
        "         SUM(w.electors) AS electors,"
        "         SUM(w.total_valid) AS total_valid,"
        "         SUM(w.nota) AS nota,"
        "         SUM(w.jmm) AS jmm, SUM(w.bjp) AS bjp, SUM(w.ajsu) AS ajsu,"
        "         SUM(w.jlkm) AS jlkm, SUM(w.inc) AS inc, SUM(w.rjd) AS rjd,"
        "         SUM(w.jvm) AS jvm, SUM(w.others) AS others"
        "  FROM election e"
        "  LEFT JOIN mv_result_booth_wide w ON w.election_id = e.election_id"
        "  WHERE e.ac_id = %s"
        "  GROUP BY e.election_id, e.label, e.type, e.year, e.is_baseline"
        "), ranked AS ("
        "  SELECT t.*, r.abbr, r.votes,"
        "         ROW_NUMBER() OVER (PARTITION BY t.election_id ORDER BY r.votes DESC) AS rn"
        "  FROM totals t"
        # NOTA is excluded: it is not a candidate and can never win or be
        # runner-up (METRICS.md, margin_votes).
        "  CROSS JOIN LATERAL (VALUES ('JMM', t.jmm), ('BJP', t.bjp), ('AJSU', t.ajsu),"
        "                             ('JLKM', t.jlkm), ('INC', t.inc), ('RJD', t.rjd),"
        "                             ('JVM', t.jvm), ('OTHERS', t.others)"
        "                     ) AS r(abbr, votes)"
        "  WHERE t.booths > 0 AND r.votes > 0"
        ")"
        "SELECT t.label, t.type, t.year, t.is_baseline, t.booths, t.votes, t.electors,"
        "       t.total_valid, t.nota,"
        "       win.abbr AS winner_party, win.votes AS winner_votes,"
        "       run.abbr AS runner_party, run.votes AS runner_votes,"
        "       (win.votes - run.votes)::INT AS margin_votes "
        "FROM totals t "
        "LEFT JOIN ranked win ON win.election_id = t.election_id AND win.rn = 1 "
        "LEFT JOIN ranked run ON run.election_id = t.election_id AND run.rn = 2 "
        "ORDER BY t.year DESC, t.type",
        (ac.ac_id,),
    )
    baseline = query_one(
        "SELECT e.label, SUM(w.jmm) AS jmm, SUM(w.bjp) AS bjp, SUM(w.jlkm) AS jlkm, "
        "SUM(w.nota) AS nota, SUM(w.electors) AS electors, SUM(w.votes_counted) AS votes "
        "FROM mv_result_booth_wide w JOIN election e ON e.election_id = w.election_id "
        "WHERE e.is_baseline AND e.ac_id = %s GROUP BY e.label",
        (ac.ac_id,),
    )

    # The data-health strip (spec 7.1). Each dataset reports loaded / partial /
    # missing so a page can say which command fills the gap rather than
    # rendering a blank panel. Counting per AC matters: five of the six have
    # nothing at booth level yet, and that has to read as "not loaded" rather
    # than as a fault.
    data_health = query_one(
        "SELECT (SELECT COUNT(*) FROM booth WHERE is_active AND ac_id = %(ac)s) AS booths, "
        "(SELECT COUNT(*) FROM booth WHERE is_active AND ac_id = %(ac)s AND geom IS NOT NULL) "
        "  AS booths_geocoded, "
        "(SELECT COUNT(*) FROM ps_list_entry WHERE ac_id = %(ac)s) AS ps_list_rows, "
        "(SELECT COUNT(DISTINCT election_id) FROM result_booth WHERE ac_id = %(ac)s) "
        "  AS elections_with_results, "
        "(SELECT COUNT(*) FROM review_queue WHERE status = 'open' AND ac_id = %(ac)s) "
        "  AS open_reviews, "
        "(SELECT COUNT(*) FROM booth_crosswalk WHERE ac_id = %(ac)s "
        "   AND confidence < 0.85 AND NOT reviewed) AS weak_crosswalks, "
        "(SELECT COUNT(*) FROM booth_crosswalk WHERE ac_id = %(ac)s) AS crosswalk_rows, "
        "(SELECT MAX(revision_date) FROM roll_revision WHERE ac_id = %(ac)s) AS latest_roll, "
        "(SELECT COUNT(*) FROM roll_revision WHERE ac_id = %(ac)s) AS roll_revisions, "
        "(SELECT COUNT(*) FROM caste_estimate WHERE ac_id = %(ac)s AND source = 'blend') "
        "  AS caste_rows, "
        "(SELECT COUNT(*) FROM demography WHERE ac_id = %(ac)s) AS census_rows, "
        "(SELECT COUNT(*) FROM local_result WHERE ac_id = %(ac)s) AS local_result_rows, "
        "(SELECT COUNT(*) FROM source_doc WHERE ac_id = %(ac)s) AS source_docs",
        {"ac": ac.ac_id},
    )

    today = date.today()
    deadline = ac.bypoll_due
    return {
        "constituency": {
            "ac_number": ac.ac_number,
            "code": ac.label,
            "name_en": ac.name_en,
            "name_hi": ac.name_hi,
            "reservation": ac.reservation,
            # The badge every page shows until a human has reconciled this AC's
            # seeded facts against ECI/CEO publications.
            "verified": ac.verified,
        },
        "bypoll": {
            "vacancy_date": ac.vacancy_date,
            "deadline": deadline,
            "days_to_deadline": (deadline - today).days if deadline else None,
            "note": ("The ECI must hold the poll within six months of the vacancy."
                     if deadline else "No by-election is pending in this constituency."),
        },
        "elections": elections,
        "baseline": baseline,
        "data_health": data_health,
        "scope": {"block_id": scoped_block_id(user), "sees_caste": user.sees_caste},
    }


@router.get("/knowledge-cards")
def knowledge_cards(user: CurrentUser, ac: CurrentAC) -> dict:
    """Curated context cards (HLD module 9), from the database.

    The Factors page used to render these as a hardcoded array in the frontend.
    Its own comment claimed they were "the same cards that go into the
    assistant's cached prompt, so what the dashboard shows and what the
    assistant knows cannot drift apart" - but the assistant reads the
    `knowledge_card` table (chatbot/llm.py) while the page read a constant, and
    the two had already drifted: five cards against six seeded, with a
    mismatched slug. One source now, so the stated property is actually true.
    """
    # This AC's cards, plus the ones with no ac_id: the caste guardrails and
    # data-provenance cards are general guidance and apply everywhere.
    cards = query(
        "SELECT slug, topic, title_en, title_hi, body_en, body_hi, sources, "
        "last_reviewed, in_prompt, ac_id IS NULL AS is_general "
        "FROM knowledge_card WHERE ac_id = %s OR ac_id IS NULL "
        "ORDER BY ac_id NULLS LAST, slug",
        (ac.ac_id,),
    )
    return {
        "cards": cards,
        "note": (
            "Curated from public secondary sources. Every figure should be "
            "checked against the source document before it is relied on."
        ),
    }


@router.get("/booths")
def booths_geojson(
    user: CurrentUser,
    ac: CurrentAC,
    election_label: str | None = None,
    area_id: int | None = None,
    block_id: int | None = None,
    metric: str = Query("margin_pct", pattern=r"^[a-z_]{3,32}$"),
) -> dict:
    """Booth points as GeoJSON, carrying the metric the map is colouring by.

    `block_id` and `election_label` are honoured, not merely accepted. The audit
    found /booths echoing election_label back in its metadata while serving
    baseline numbers regardless (F2), so a caller asking for VS-2019 got 2024
    figures labelled 2019.
    """
    allowed_metrics = {
        "margin_pct", "signed_margin_pct", "turnout_pct", "new_voter_pct",
        "priority_score", "floating_pct", "margin_stddev", "electors",
    }
    if metric not in allowed_metrics:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"metric must be one of: {', '.join(sorted(allowed_metrics))}")

    clauses, params = ["b.is_active", "b.ac_id = %s"], [ac.ac_id]
    scoped = scoped_block_id(user)
    if scoped is not None:
        clauses.append("a.block_id = %s")
        params.append(scoped)
    elif block_id is not None:
        clauses.append("a.block_id = %s")
        params.append(block_id)
    if area_id is not None:
        clauses.append("b.area_id = %s")
        params.append(area_id)

    # The metric columns live on mv_booth_priority, which is baseline-only by
    # construction. Asking for a different election has to change which rows are
    # joined, not just the label in the response.
    if election_label:
        clauses.append("p.election_label = %s")
        params.append(election_label)
    where = " AND ".join(clauses)

    rows = query(
        f"""
        SELECT b.booth_uid, b.ps_name_hi, b.building, b.village_or_locality,
               b.current_ps_number, b.geocode_conf,
               ST_X(b.geom) AS lon, ST_Y(b.geom) AS lat,
               a.area_id, a.name_hi AS area_hi, a.name_en AS area_en, a.kind AS area_kind,
               a.block_id,
               p.margin_pct, p.signed_margin_pct, p.turnout_pct, p.new_voter_pct,
               p.priority_score, p.floating_pct, p.margin_stddev, p.electors,
               p.winner_party, p.runner_party, p.election_label,
               x.confidence AS crosswalk_confidence, x.reviewed AS crosswalk_reviewed
        FROM booth b
        JOIN area a ON a.area_id = b.area_id
        LEFT JOIN mv_booth_priority p ON p.booth_uid = b.booth_uid
        LEFT JOIN booth_crosswalk x ON x.booth_uid = b.booth_uid
                                   AND x.ps_number = b.current_ps_number
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
                 "election_label": election_label, "ac_number": ac.ac_number,
                 # Marker size is the electorate (F3), which is NULL until a roll
                 # snapshot is linked; the map must size by a real number or say
                 # it cannot.
                 "electors_known": sum(1 for f in features
                                       if f["properties"].get("electors") is not None)},
    }


@router.get("/booths/{booth_uid}/card")
def booth_card(booth_uid: str, user: CurrentUser, ac: CurrentAC) -> dict:
    try:
        card = build_booth_card(booth_uid, include_caste=user.sees_caste, ac_id=ac.ac_id)
    except LookupError:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"No booth {booth_uid} in AC-{ac.ac_number}",
        ) from None
    block_id = scoped_block_id(user)
    if block_id is not None and card["booth"]["block_id"] != block_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This booth is outside your block")
    return card


@router.get("/results/{election_label}/booths")
def results_by_booth(election_label: str, user: CurrentUser, ac: CurrentAC,
                     area_id: int | None = None, block_id: int | None = None,
                     format: str = Query("json", pattern="^(json|csv)$")):
    """Full Form 20 table for one election, sortable and exportable (module 3)."""
    clauses, params = ["w.election_label = %s", "w.ac_id = %s"], [election_label, ac.ac_id]
    scoped = scoped_block_id(user)
    if scoped is not None:
        clauses.append("w.block_id = %s")
        params.append(scoped)
    elif block_id is not None:
        clauses.append("w.block_id = %s")
        params.append(block_id)
    if area_id is not None:
        clauses.append("w.area_id = %s")
        params.append(area_id)

    rows = query(
        f"""
        SELECT w.booth_uid, w.ps_numbers, a.name_hi AS area_hi, a.name_en AS area_en,
               bl.name_en AS block_en, b.building, b.village_or_locality,
               w.electors, w.votes_polled, w.valid_votes, w.turnout_pct,
               w.jmm, w.bjp, w.ajsu, w.jlkm, w.inc, w.rjd, w.jvm, w.others, w.nota,
               w.winner_party, w.runner_party, w.margin_votes, w.margin_pct,
               w.signed_margin_pct, w.rejected, w.lineage_kind,
               x.confidence AS crosswalk_confidence, x.reviewed AS crosswalk_reviewed,
               w.source_doc, w.source_page
        FROM mv_result_booth_wide w
        JOIN booth b ON b.booth_uid = w.booth_uid
        JOIN area a ON a.area_id = w.area_id
        JOIN block bl ON bl.block_id = a.block_id
        LEFT JOIN booth_crosswalk x ON x.booth_uid = w.booth_uid
                                   AND x.election_id = w.election_id
        WHERE {' AND '.join(clauses)}
        ORDER BY w.booth_uid
        """,
        params,
    )
    if format == "csv":
        return _csv_response(
            rows, f"ac{ac.ac_number}_{election_label.replace(' ', '_')}_booths.csv")
    return {"election_label": election_label, "ac_number": ac.ac_number,
            "rows": rows, "count": len(rows)}


@router.get("/results/{election_label}/areas")
def results_by_area(election_label: str, user: CurrentUser, ac: CurrentAC,
                    format: str = Query("json", pattern="^(json|csv)$")):
    clauses, params = ["election_label = %s", "ac_id = %s"], [election_label, ac.ac_id]
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
        return _csv_response(
            rows, f"ac{ac.ac_number}_{election_label.replace(' ', '_')}_areas.csv")
    return {"election_label": election_label, "ac_number": ac.ac_number,
            "rows": rows, "count": len(rows)}


@router.get("/rolls/changes")
def roll_changes(user: CurrentUser, ac: CurrentAC, revision_label: str | None = None,
                 area_id: int | None = None, block_id: int | None = None,
                 format: str = Query("json", pattern="^(json|csv)$")):
    """New and deleted voters per booth per revision (module 4).

    Deletions matter as much as additions if the roll is post-SIR, so the
    revision's is_post_sir flag rides along with every row.
    """
    clauses, params = ["c.ac_id = %s"], [ac.ac_id]
    scoped = scoped_block_id(user)
    if scoped is not None:
        clauses.append("a.block_id = %s")
        params.append(scoped)
    elif block_id is not None:
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
        -- D7: roll_change rows are written for supplements and roll_snapshot
        -- rows only for mother rolls, and is_mother = NOT supplement, so a
        -- single revision_id can never be both. Joining on an equal revision_id
        -- therefore left electors NULL for every row, and additions_pct and
        -- deletions_pct NULL with it. The denominator has to come from the most
        -- recent mother roll at or before this change.
        LEFT JOIN LATERAL (
            SELECT s.electors
            FROM roll_snapshot s
            JOIN roll_revision mr ON mr.revision_id = s.revision_id
            WHERE s.booth_uid = c.booth_uid
              AND mr.is_mother
              AND mr.ac_id = c.ac_id
              AND mr.revision_date <= r.revision_date
            ORDER BY mr.revision_date DESC
            LIMIT 1
        ) s ON true
        WHERE {' AND '.join(clauses)}
        ORDER BY r.revision_date DESC, c.booth_uid
        """,
        params,
    )
    if format == "csv":
        return _csv_response(rows, f"ac{ac.ac_number}_roll_changes.csv")
    return {"rows": rows, "count": len(rows), "ac_number": ac.ac_number}


@router.get("/rolls/revisions")
def roll_revisions(user: CurrentUser, ac: CurrentAC) -> dict:
    return {"rows": query(
        "SELECT revision_id, label, revision_date, is_post_sir, is_mother "
        "FROM roll_revision WHERE ac_id = %s ORDER BY revision_date DESC",
        (ac.ac_id,),
    )}


@router.get("/caste")
def caste(user: StrategistUser, ac: CurrentAC, area_id: int | None = None,
          booth_uid: str | None = None,
          min_conf: float = Query(0.4, ge=0.0, le=1.0),
          source: str = Query("blend", pattern="^(blend|surname|census|survey)$")) -> dict:
    """Aggregate community estimates only (HLD 5). Hidden from block-role users."""
    clauses = ["ce.source = %s", "ce.confidence >= %s", "ce.ac_id = %s"]
    params = [source, min_conf, ac.ac_id]
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
               ce.est_count, ce.est_pct, ce.matched_pct, ce.confidence, ce.source,
               ce.method_version
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
def transfer(user: StrategistUser, ac: CurrentAC, year: int = 2024,
             area_id: int | None = None,
             format: str = Query("json", pattern="^(json|csv)$")):
    """Where the Lok Sabha vote went at the assembly poll (module 6)."""
    clauses, params = ["t.year = %s", "t.ac_id = %s"], [year, ac.ac_id]
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
                                    AND f.ac_id = t.ac_id
        WHERE {' AND '.join(clauses)}
        ORDER BY f.floating_pct DESC NULLS LAST, t.booth_uid, t.party
        """,
        params,
    )
    if format == "csv":
        return _csv_response(rows, f"ac{ac.ac_number}_transfer_{year}.csv")
    return {
        "year": year, "ac_number": ac.ac_number, "rows": rows, "count": len(rows),
        "note": (f"Lok Sabha figures here are the AC-{ac.ac_number} segment of its parliamentary "
                 "seat, not the whole PC. floating_pct is the Pedersen index between the two "
                 "polls and is NULL, not 50%, where only one of them is loaded."),
    }


@router.get("/local-results")
def local_results(user: CurrentUser, ac: CurrentAC, election_label: str | None = None,
                  seat_type: str | None = None) -> dict:
    """Panchayat and municipal results (module 7)."""
    clauses, params = ["lr.ac_id = %s"], [ac.ac_id]
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
def priority(user: CurrentUser, ac: CurrentAC, limit: int = Query(50, ge=1, le=500),
             format: str = Query("json", pattern="^(json|csv)$")):
    """Booth priority ranking (module 10)."""
    clauses, params = ["p.ac_id = %s"], [ac.ac_id]
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
               p.priority_score, p.priority_quartile, p.winner_party, p.runner_party,
               p.inputs_used
        FROM mv_booth_priority p
        JOIN booth b ON b.booth_uid = p.booth_uid
        JOIN area a ON a.area_id = p.area_id
        WHERE {' AND '.join(clauses)}
        ORDER BY p.priority_score DESC LIMIT %s
        """,
        params,
    )
    if format == "csv":
        return _csv_response(rows, f"ac{ac.ac_number}_booth_priority.csv")
    return {
        "rows": rows, "count": len(rows),
        "ac_number": ac.ac_number,
        "formula": ("0.35 x tight margin + 0.25 x new-voter share + 0.20 x volatility "
                    "+ 0.20 x floating vote, each percentile-ranked within this AC. "
                    "Missing inputs are dropped and the remaining weights renormalised; "
                    "inputs_used records which contributed."),
    }


@router.get("/areas")
def areas(user: CurrentUser, ac: CurrentAC) -> dict:
    """Filter vocabulary for this AC: its blocks, areas, elections and parties.

    Every page's filters are built from this, so it has to be AC-scoped or a
    block picker would offer another constituency's blocks.
    """
    clauses, params = ["a.ac_id = %s"], [ac.ac_id]
    block_id = scoped_block_id(user)
    if block_id is not None:
        clauses.append("a.block_id = %s")
        params.append(block_id)
    return {
        "ac_number": ac.ac_number,
        "blocks": query(
            "SELECT block_id, name_en, name_hi, kind FROM block WHERE ac_id = %s "
            "ORDER BY block_id",
            (ac.ac_id,),
        ),
        "areas": query(
            f"SELECT a.area_id, a.block_id, a.kind, a.name_en, a.name_hi, a.code, "
            f"COUNT(b.booth_uid) AS booths "
            f"FROM area a LEFT JOIN booth b ON b.area_id = a.area_id AND b.is_active "
            f"WHERE {' AND '.join(clauses)} "
            f"GROUP BY a.area_id, a.block_id, a.kind, a.name_en, a.name_hi, a.code "
            f"ORDER BY a.block_id, a.kind, a.name_en",
            params,
        ),
        # Contests for this AC, with whether any result is actually loaded, so a
        # picker can grey out an election rather than offering an empty table.
        "elections": query(
            "SELECT e.election_id, e.label, e.type, e.year, e.is_baseline, "
            "       EXISTS (SELECT 1 FROM result_booth rb "
            "               WHERE rb.election_id = e.election_id) AS has_results "
            "FROM election e WHERE e.ac_id = %s ORDER BY e.year DESC, e.type",
            (ac.ac_id,),
        ),
        "parties": query("SELECT party_id, abbr, name_en, name_hi, colour FROM party "
                         "ORDER BY party_id"),
        # The pair the signed margin ramp is oriented by, for this AC's baseline.
        "contest": query_one(
            "SELECT pa.abbr AS party_a, pb.abbr AS party_b, c.source "
            "FROM ac_contest c "
            "JOIN election e ON e.event_id = c.event_id AND e.ac_id = c.ac_id "
            "JOIN party pa ON pa.party_id = c.party_a "
            "JOIN party pb ON pb.party_id = c.party_b "
            "WHERE c.ac_id = %s AND e.is_baseline",
            (ac.ac_id,),
        ),
    }
