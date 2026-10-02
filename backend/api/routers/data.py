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
import json
from datetime import date
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Response, status

from api.booth_card import build_booth_card
from api.deps import CurrentAC, CurrentUser, StrategistUser, scoped_block_id
from api.election_results import (
    any_synthetic,
    booths_led,
    candidate_results,
    election_sources,
    published_results,
)
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
    # One row per election in this AC. Loaded elections come from mv_ac_summary,
    # which ranks candidates on the declared result - EVM votes from the booth
    # rows plus the postal ballots Form 20 reports only for the whole AC - so the
    # winner and margin here are the ones the Returning Officer declared. Ranking
    # booth rows alone (as this route did before 0023) left postal ballots out
    # of every total.
    #
    # The legacy names `votes` (= votes polled, valid + rejected) and
    # `total_valid` (valid including NOTA, METRICS.md) are kept for the pages
    # that already read them.
    elections = query(
        "SELECT e.label, e.type, e.year, e.is_baseline,"
        "       COALESCE(s.booths, 0)::INT AS booths,"
        "       s.votes_polled AS votes, s.electors, s.electors_source,"
        "       s.valid_votes AS total_valid, s.nota, s.rejected,"
        "       s.evm_votes, s.postal_votes, s.votes_polled_published,"
        "       s.winner_party, s.winner_candidate, s.winner_votes, s.winner_evm_votes,"
        "       s.runner_party, s.runner_candidate, s.runner_votes, s.runner_evm_votes,"
        "       s.contestants, s.margin_votes, s.margin_pct, s.turnout_pct,"
        "       s.election_id IS NOT NULL AS has_results "
        "FROM election e "
        "LEFT JOIN mv_ac_summary s ON s.election_id = e.election_id "
        "WHERE e.ac_id = %(ac)s "
        "ORDER BY e.year DESC, e.type",
        {"ac": ac.ac_id},
    )
    sources = election_sources(ac.ac_id)
    led = booths_led(ac.ac_id)
    published = published_results(ac.ac_id)
    for e in elections:
        docs = sources.get(e["label"], [])
        e["sources"] = docs
        e["source_doc"] = docs[0]["source_doc"] if docs else None
        e["synthetic"] = any(d["synthetic"] for d in docs) if docs else None
        e["booths_led"] = led.get(e["label"], [])
        # Elections with no Form 20 loaded keep their published result, with
        # its source, rather than a list compiled into the frontend.
        e["published"] = None if e["has_results"] else published.get(e["label"])

    baseline = query_one(
        "SELECT e.label, SUM(w.jmm) AS jmm, SUM(w.bjp) AS bjp, SUM(w.jlkm) AS jlkm, "
        "SUM(w.nota) AS nota, SUM(w.electors) AS electors, SUM(w.votes_polled) AS votes "
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
        "(SELECT COUNT(*) FROM booth WHERE is_active AND ac_id = %(ac)s AND lon IS NOT NULL) "
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
        "(SELECT COUNT(*) FROM local_office_holder WHERE ac_id = %(ac)s) AS office_holder_rows, "
        "(SELECT COUNT(*) FROM source_doc WHERE ac_id = %(ac)s) AS source_docs, "
        "(SELECT COUNT(*) FROM source_doc WHERE ac_id = %(ac)s AND kind = 'form20' "
        "   AND parse_status = 'loaded' AND NOT is_synthetic) AS form20_real_docs, "
        "(SELECT COUNT(*) FROM result_booth_meta WHERE ac_id = %(ac)s) AS form20_booth_rows",
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
        # True while any loaded document is generated test data, so every page
        # can say so instead of presenting mock figures as a result.
        "synthetic": any_synthetic(ac.ac_id),
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

    where = " AND ".join(clauses)

    # One election - the one asked for, else the baseline - and every join is
    # to that election. Result figures come from mv_result_booth_wide, which
    # has every election; the priority-only inputs come from mv_booth_priority,
    # which is baseline-only and so is NULL for any other year (grey on the
    # map, which is the honest answer). This used to filter on
    # mv_booth_priority.election_label, so any non-baseline election returned
    # no booths at all; and it joined booth_crosswalk on PS number with no
    # election, so a booth appeared once per election that used its number.
    rows = query(
        f"""
        WITH selected_election AS (
            SELECT election_id, label FROM election
            WHERE ac_id = %s AND (label = %s OR (%s::TEXT IS NULL AND is_baseline))
        )
        SELECT b.booth_uid, b.ps_name_hi, b.building, b.village_or_locality,
               b.current_ps_number, b.geocode_conf,
               b.lon, b.lat,
               a.area_id, a.name_hi AS area_hi, a.name_en AS area_en, a.kind AS area_kind,
               a.block_id,
               w.margin_pct, w.signed_margin_pct, w.turnout_pct, p.new_voter_pct,
               p.priority_score, p.floating_pct, p.margin_stddev, w.electors,
               w.winner_party, w.runner_party,
               sw.swing_pct, w.contest_party_a AS swing_party,
               CASE WHEN w.booth_uid IS NOT NULL THEN sel.label END AS election_label,
               x.confidence AS crosswalk_confidence, x.reviewed AS crosswalk_reviewed
        FROM booth b
        JOIN area a ON a.area_id = b.area_id
        LEFT JOIN selected_election sel ON true
        LEFT JOIN mv_result_booth_wide w ON w.booth_uid = b.booth_uid
                                        AND w.election_id = sel.election_id
        LEFT JOIN mv_booth_priority p ON p.booth_uid = b.booth_uid
                                     AND p.election_id = sel.election_id
        -- Swing of this AC's contest party A, not a hardcoded JMM.
        LEFT JOIN mv_swing sw ON sw.booth_uid = b.booth_uid
                             AND sw.election_id = sel.election_id
                             AND sw.party = w.contest_party_a
        LEFT JOIN LATERAL (
            SELECT MIN(confidence) AS confidence, BOOL_AND(reviewed) AS reviewed
            FROM booth_crosswalk
            WHERE booth_uid = b.booth_uid AND election_id = sel.election_id
        ) x ON true
        WHERE {where}
        ORDER BY b.booth_uid
        """,
        [ac.ac_id, election_label, election_label] + params,
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
               w.winner_candidate, ru.candidate_name AS runner_candidate, w.contestants,
               w.signed_margin_pct, w.rejected, tv.tendered, w.lineage_kind,
               x.confidence AS crosswalk_confidence, x.reviewed AS crosswalk_reviewed,
               -- The Booths page's swing / voters / priority presets read these;
               -- without them every one of those columns was a dash.
               sw.swing_pct, w.contest_party_a AS swing_party,
               nv.new_voter_pct, nv.additions, fl.floating_pct, vo.margin_stddev,
               w.source_doc, w.source_page
        FROM mv_result_booth_wide w
        JOIN booth b ON b.booth_uid = w.booth_uid
        JOIN area a ON a.area_id = w.area_id
        JOIN block bl ON bl.block_id = a.block_id
        -- One row per booth: a merge maps several PS numbers to one booth in
        -- the same election, and a plain join repeated the booth per number.
        -- The weakest link and whether all of them are reviewed is what a
        -- reader of the table needs.
        LEFT JOIN LATERAL (
            SELECT MIN(confidence) AS confidence, BOOL_AND(reviewed) AS reviewed
            FROM booth_crosswalk
            WHERE booth_uid = w.booth_uid AND election_id = w.election_id
        ) x ON true
        -- Runner-up by name, ranked as mv_result_booth_wide ranks (votes, then
        -- contestant key), so it is the candidate behind runner_party.
        LEFT JOIN LATERAL (
            SELECT c.candidate_name FROM mv_result_booth_candidate c
            WHERE c.booth_uid = w.booth_uid AND c.election_id = w.election_id
              AND c.party IS DISTINCT FROM 'NOTA'
            ORDER BY c.votes DESC, c.contestant OFFSET 1 LIMIT 1
        ) ru ON true
        LEFT JOIN LATERAL (
            SELECT SUM(m.tendered)::INT AS tendered
            FROM booth_crosswalk bx
            JOIN result_booth_meta m ON m.election_id = bx.election_id
                                    AND m.ps_number = bx.ps_number
            WHERE bx.booth_uid = w.booth_uid AND bx.election_id = w.election_id
        ) tv ON true
        LEFT JOIN mv_swing sw ON sw.booth_uid = w.booth_uid
                             AND sw.election_id = w.election_id
                             AND sw.party = w.contest_party_a
        LEFT JOIN mv_new_voter_share nv ON nv.booth_uid = w.booth_uid
                                       AND nv.election_id = w.election_id
        LEFT JOIN mv_floating_vote fl ON fl.ac_id = w.ac_id AND fl.booth_uid = w.booth_uid
                                     AND fl.year = w.election_year
        LEFT JOIN mv_volatility vo ON vo.ac_id = w.ac_id AND vo.booth_uid = w.booth_uid
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
               -- `ce.matched_pct` and `ce.method_version` were selected here
               -- and exist on neither the table nor any writer, so GET /caste
               -- was a 500 - the same defect as N7 on /summary, found the same
               -- way, by running the query. Removed rather than added to the
               -- schema: a nullable column nothing populates is audit B4, where
               -- `electors` had no writer and the map offered turnout anyway.
               -- Recorded as N10; they belong with the estimator that should
               -- record them.
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


@router.get("/caste/correlation")
def caste_correlation(
    user: StrategistUser,
    ac: CurrentAC,
    community: str | None = None,
    min_conf: float = Query(0.0, ge=0.0, le=1.0),
) -> dict:
    """Community share against party share, per booth (audit F4).

    The audited dashboard plotted community share against a constant zero with
    the Y axis hidden - a one-dimensional strip plot - while its caption
    explained how to read a relationship with vote share that was never on the
    chart. The ecological regression existed as SQL in analytics/metrics.sql, a
    file nothing executed, and was exposed by no endpoint.

    Both columns are booth-level aggregates, and the caveat in the response says
    what that means in the terms that matter: a relationship here is equally
    consistent with the opposite behaviour at individual level. The community
    figure is also an estimate rather than a count, so its confidence travels
    with every row and the caller can exclude the weak ones.
    """
    target = community or "Kurmi (Mahato)"
    rows = query(
        """
        SELECT ce.booth_uid,
               a.name_en AS area_en,
               a.name_hi AS area_hi,
               ce.est_pct   AS community_pct,
               ce.confidence,
               w.electors,
               ROUND((100.0 * w.jlkm / NULLIF(w.valid_votes, 0))::NUMERIC, 2) AS jlkm_share_pct,
               ROUND((100.0 * w.jmm  / NULLIF(w.valid_votes, 0))::NUMERIC, 2) AS jmm_share_pct,
               ROUND((100.0 * w.bjp  / NULLIF(w.valid_votes, 0))::NUMERIC, 2) AS bjp_share_pct
        FROM caste_estimate ce
        JOIN community c ON c.community_id = ce.community_id
        JOIN booth b     ON b.booth_uid = ce.booth_uid
        JOIN area a      ON a.area_id = b.area_id
        JOIN election e  ON e.ac_id = ce.ac_id AND e.is_baseline
        LEFT JOIN mv_result_booth_wide w
               ON w.booth_uid = ce.booth_uid AND w.election_id = e.election_id
        WHERE ce.ac_id = %s AND ce.source = 'blend' AND c.name_en = %s
          AND ce.confidence >= %s
        ORDER BY ce.booth_uid
        """,
        (ac.ac_id, target, min_conf),
    )
    return {
        "rows": rows,
        "community": target,
        "party": "JLKM",
        "caveat": (
            "This is an ecological correlation between two booth-level aggregates. It "
            "cannot show how any community voted; a relationship here is equally "
            "consistent with the opposite behaviour at individual level (Simpson's "
            "paradox). The community share is itself an estimate with the confidence "
            "shown, and UNMATCHED is the share the surname dictionary could not place - "
            "not a community."
        ),
    }


@router.get("/candidates")
def candidates(user: CurrentUser, ac: CurrentAC, election_label: str | None = None) -> dict:
    """Candidate profiles for this AC's contests (spec 7.9).

    Everything beyond the vote count is declared or transcribed rather than
    counted, so the response says so and the page labels it. Assets and criminal
    cases come from the candidate's own affidavit.
    """
    clauses, params = ["c.ac_id = %s"], [ac.ac_id]
    if election_label:
        clauses.append("e.label = %s")
        params.append(election_label)

    rows = query(
        f"""
        SELECT c.candidate_id, c.name_en, c.name_hi, p.abbr AS party,
               e.label AS election_label, c.is_winner,
               t.value AS votes,
               ROUND((100.0 * t.value / NULLIF(tot.value, 0))::NUMERIC, 2) AS share_pct,
               cp.incumbent, cp.contests_prior, cp.wins_prior,
               pp.abbr AS prev_party, cp.turncoat, cp.deposit_forfeited,
               cp.age, cp.education, cp.profession,
               cp.assets_declared, cp.liabilities,
               cp.criminal_cases, cp.criminal_serious,
               cp.source, cp.affidavit_url
        FROM candidate c
        JOIN election e   ON e.election_id = c.election_id
        LEFT JOIN party p ON p.party_id = c.party_id
        LEFT JOIN candidate_profile cp ON cp.candidate_id = c.candidate_id
        LEFT JOIN party pp ON pp.party_id = cp.prev_party_id
        LEFT JOIN result_ac_total t
               ON t.candidate_id = c.candidate_id AND t.metric = 'votes'
        LEFT JOIN result_ac_total tot
               ON tot.election_id = c.election_id AND tot.metric = 'total_valid'
              AND tot.candidate_id IS NULL
        -- NOTA is seeded as a candidate row so its votes can be stored per
        -- booth; it is not a person and has no profile.
        WHERE {' AND '.join(clauses)} AND p.abbr IS DISTINCT FROM 'NOTA'
        ORDER BY e.year DESC, t.value DESC NULLS LAST
        """,
        params,
    )
    return {
        "rows": rows,
        "note": (
            "Candidate profiles are transcribed from affidavit and MyNeta data and are "
            "seeded unverified. Assets and criminal cases are as declared by the "
            "candidate, not as established by a court."
        ),
    }


@router.get("/elections/{election_label}/candidates")
def election_candidates(election_label: str, user: CurrentUser, ac: CurrentAC) -> dict:
    """The full declared result of one election: every candidate, ranked.

    EVM, postal and total votes, share of valid votes (NOTA included, as
    everywhere in METRICS.md), polling stations led, and whether the deposit
    was forfeited. Constituency-wide for every role: these are published
    figures, unlike the booth rows a block user is scoped to.
    """
    result = candidate_results(ac.ac_id, election_label)
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            f"No election {election_label} in AC {ac.ac_number}")
    return result


@router.get("/local-politics")
def local_politics(user: CurrentUser, ac: CurrentAC) -> dict:
    """Office holders, events and organisations for this AC (spec 7.10).

    The influencer registry is deliberately not here: it holds named individuals
    and is restricted to strategist and admin, so it gets its own role-gated
    endpoint rather than riding along on one open to every role.
    """
    return {
        "office_holders": query(
            "SELECT h.id, h.office, h.name, a.name_en AS area_en, "
            "       p.abbr AS tagged_party, h.tag_source, h.term_start, h.term_end "
            "FROM local_office_holder h "
            "LEFT JOIN area a ON a.area_id = h.area_id "
            "LEFT JOIN party p ON p.party_id = h.tagged_party_id "
            "WHERE h.ac_id = %s ORDER BY h.office, h.name",
            (ac.ac_id,),
        ),
        "events": query(
            "SELECT ev.id, ev.occurred_on, ev.kind, a.name_en AS area_en, ev.title, "
            "       p.abbr AS effect_party, ev.effect_sign, ev.source "
            "FROM political_event ev "
            "LEFT JOIN area a ON a.area_id = ev.area_id "
            "LEFT JOIN party p ON p.party_id = ev.effect_party_id "
            "WHERE ev.ac_id = %s ORDER BY ev.occurred_on DESC LIMIT 200",
            (ac.ac_id,),
        ),
        "organisations": query(
            "SELECT o.id, o.name, o.kind, c.name_en AS community, p.abbr AS alignment_party "
            "FROM organisation o "
            "LEFT JOIN community c ON c.community_id = o.community_id "
            "LEFT JOIN party p ON p.party_id = o.alignment_party_id "
            "WHERE o.ac_id = %s ORDER BY o.name",
            (ac.ac_id,),
        ),
        "note": (
            "Panchayat elections are contested without party symbols. Any party shown "
            "against an office holder is a manual tag; tag_source records who assigned "
            "it and on what basis. An untagged holder is untagged, not independent."
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


@router.get("/local-caste")
def local_caste(user: CurrentUser, ac: CurrentAC, area_id: int | None = None) -> dict:
    """Estimated caste composition per panchayat, derived from booth-level estimates.

    Returns an empty array (not an error) when caste_estimate has no rows for
    this AC. community_pct is each community's share within the GP; estimated_votes
    is turnout-weighted and NULL when booth-level result data is absent.
    """
    clauses, params = ["v.ac_id = %s"], [ac.ac_id]
    if area_id is not None:
        clauses.append("v.area_id = %s")
        params.append(area_id)
    block_id = scoped_block_id(user)
    if block_id is not None:
        clauses.append(
            "v.area_id IN (SELECT area_id FROM area WHERE block_id = %s)"
        )
        params.append(block_id)

    rows = query(
        f"""
        SELECT v.area_id, v.area_en, v.area_hi, v.block_en,
               v.community, v.estimated_voters, v.community_pct, v.estimated_votes
        FROM mv_local_caste_vote v
        WHERE {' AND '.join(clauses)}
        ORDER BY v.area_en, v.community_pct DESC
        """,
        params,
    )
    return {
        "rows": rows,
        "count": len(rows),
        "note": (
            "Community figures are estimates derived from booth-level caste data and "
            "should not be presented as census counts. estimated_votes is turnout-weighted "
            "and approximates how many votes each community may have cast at the last VS "
            "election in the corresponding booths. Empty when caste data is not loaded."
        ),
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


BOUNDARY_FILE = Path(__file__).resolve().parents[2] / "db" / "seed" / "geo" / "boundaries.json"


@lru_cache(maxsize=1)
def _boundary_file() -> dict:
    """The built boundary file, for what the database has no row to hold.

    Two things live only here: the source warnings (AC-32's outline does not
    contain Giridih town), and blocks that overlap the AC but are not in the
    seed, which have no `block` row to attach a shape to. Missing file means
    neither, not an error - the database layers are still served.
    """
    try:
        return json.loads(BOUNDARY_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"features": [], "warnings": [], "sources": {}}


def _feature(geometry: dict, **properties) -> dict:
    return {"type": "Feature", "geometry": geometry, "properties": properties}


@router.get("/boundaries")
def boundaries(user: CurrentUser, ac: CurrentAC) -> dict:
    """Polygons for the map: the AC outline, its blocks, and any area shapes.

    A block-scoped user gets their own block and its areas only, the same scope
    /booths applies; the AC outline is not scoped, since it is the frame both
    are drawn in. Every shape is unverified and says where it came from.
    """
    scoped = scoped_block_id(user)

    row = query_one(
        "SELECT boundary, boundary_bbox, boundary_source FROM ac WHERE ac_id = %s",
        (ac.ac_id,),
    )
    outline = (
        _feature(row["boundary"], layer="ac", ac_number=ac.ac_number,
                 bbox=row["boundary_bbox"], source=row["boundary_source"], verified=False)
        if row and row["boundary"] else None
    )

    block_clauses, block_params = ["ac_id = %s", "boundary IS NOT NULL"], [ac.ac_id]
    if scoped is not None:
        block_clauses.append("block_id = %s")
        block_params.append(scoped)
    blocks = [
        _feature(r["boundary"], layer="block", ac_number=ac.ac_number,
                 block_id=r["block_id"], name_en=r["name_en"], name_hi=r["name_hi"],
                 kind=r["kind"], seeded=True, bbox=r["boundary_bbox"],
                 source=r["boundary_source"], verified=False)
        for r in query(
            f"SELECT block_id, name_en, name_hi, kind, boundary, boundary_bbox, "
            f"boundary_source FROM block WHERE {' AND '.join(block_clauses)} "
            f"ORDER BY block_id",
            block_params,
        )
    ]
    built = _boundary_file()
    if scoped is None:
        blocks += [
            f for f in built["features"]
            if f["properties"]["layer"] == "block"
            and f["properties"]["ac_number"] == ac.ac_number
            and not f["properties"]["seeded"]
        ]

    area_clauses, area_params = ["a.ac_id = %s", "a.boundary IS NOT NULL"], [ac.ac_id]
    if scoped is not None:
        area_clauses.append("a.block_id = %s")
        area_params.append(scoped)
    areas_ = [
        _feature(r["boundary"], layer="area", ac_number=ac.ac_number,
                 area_id=r["area_id"], block_id=r["block_id"], name_en=r["name_en"],
                 name_hi=r["name_hi"], kind=r["kind"], source=r["boundary_source"],
                 verified=False)
        for r in query(
            f"SELECT a.area_id, a.block_id, a.name_en, a.name_hi, a.kind, a.boundary, "
            f"a.boundary_source FROM area a WHERE {' AND '.join(area_clauses)} "
            f"ORDER BY a.block_id, a.area_id",
            area_params,
        )
    ]

    return {
        "ac_number": ac.ac_number,
        "ac": outline,
        "blocks": {"type": "FeatureCollection", "features": blocks},
        "areas": {"type": "FeatureCollection", "features": areas_},
        "sources": built.get("sources", {}),
        # A "no shape for this block" warning is about the source file; once a
        # shape has been loaded for that block some other way (the dev stack's
        # synthetic ULB, or a hand-digitised one) it is no longer true here.
        "warnings": [
            w for w in built.get("warnings", [])
            if w["ac_number"] == ac.ac_number
            and not (w["code"] == "seed_block_without_shape"
                     and w.get("params", {}).get("block")
                     in {b["properties"].get("name_en") for b in blocks})
        ],
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
        # A block-scoped user was offered every block of the AC here while
        # /booths silently returned only their own, so picking another block
        # emptied the map with no explanation.
        "blocks": query(
            "SELECT block_id, name_en, name_hi, kind FROM block WHERE ac_id = %s "
            + ("AND block_id = %s " if block_id is not None else "")
            + "ORDER BY block_id",
            (ac.ac_id, block_id) if block_id is not None else (ac.ac_id,),
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
