"""Admin endpoints: review queue, crosswalk correction, surname dictionary,
job status and spend (LLD 9)."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from api.deps import AdminUser
from common.db import execute, query, query_one

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/review-queue")
def review_queue(user: AdminUser, kind: str | None = None,
                 status_filter: str = Query("open", alias="status"),
                 limit: int = Query(100, ge=1, le=500)) -> dict:
    clauses, params = ["status = %s"], [status_filter]
    if kind:
        clauses.append("kind = %s")
        params.append(kind)
    params.append(limit)
    rows = query(
        f"SELECT id, kind, ref, payload, status, note, created_at "
        f"FROM review_queue WHERE {' AND '.join(clauses)} ORDER BY id DESC LIMIT %s",
        params,
    )
    counts = query("SELECT kind, COUNT(*) AS open FROM review_queue "
                   "WHERE status = 'open' GROUP BY kind ORDER BY open DESC")
    return {"rows": rows, "count": len(rows), "open_by_kind": counts}


class ResolveItem(BaseModel):
    status: str = Field(pattern="^(resolved|rejected)$")
    note: str | None = None


@router.post("/review-queue/{item_id}")
def resolve_item(item_id: int, body: ResolveItem, user: AdminUser) -> dict:
    n = execute(
        "UPDATE review_queue SET status = %s, note = COALESCE(%s, note), "
        "resolved_at = now(), resolved_by = %s WHERE id = %s AND status = 'open'",
        (body.status, body.note, user.user_id, item_id),
    )
    if not n:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No open review item with that id")
    return {"updated": n}


class CrosswalkFix(BaseModel):
    election_label: str
    ps_number: int
    booth_uid: str


@router.post("/crosswalk")
def fix_crosswalk(body: CrosswalkFix, user: AdminUser) -> dict:
    """Correct a PS-to-booth mapping by hand.

    A manual fix is marked reviewed with confidence 1.0, which stops the
    crosswalk job overwriting it on the next run.
    """
    election = query_one("SELECT election_id FROM election WHERE label = %s", (body.election_label,))
    if election is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No election {body.election_label!r}")
    if query_one("SELECT 1 AS ok FROM booth WHERE booth_uid = %s", (body.booth_uid,)) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No booth {body.booth_uid!r}")

    execute(
        "INSERT INTO booth_crosswalk (election_id, ps_number, booth_uid, match_method, "
        "confidence, reviewed) VALUES (%s, %s, %s, 'manual', 1.0, true) "
        "ON CONFLICT (election_id, ps_number) DO UPDATE SET booth_uid = EXCLUDED.booth_uid, "
        "match_method = 'manual', confidence = 1.0, reviewed = true",
        (election["election_id"], body.ps_number, body.booth_uid),
    )
    return {"updated": True, "next": "Re-run analytics.refresh so the views pick this up."}


@router.get("/crosswalk")
def crosswalk_status(user: AdminUser, election_label: str | None = None) -> dict:
    clauses, params = ["true"], []
    if election_label:
        clauses.append("e.label = %s")
        params.append(election_label)
    where = " AND ".join(clauses)
    return {
        "summary": query(
            f"SELECT e.label, x.match_method, COUNT(*) AS n, "
            f"ROUND(AVG(x.confidence)::NUMERIC, 3) AS avg_confidence, "
            f"COUNT(*) FILTER (WHERE x.reviewed) AS reviewed "
            f"FROM booth_crosswalk x JOIN election e ON e.election_id = x.election_id "
            f"WHERE {where} GROUP BY e.label, x.match_method ORDER BY e.label, x.match_method",
            params,
        ),
        "weak": query(
            f"SELECT e.label, x.ps_number, x.booth_uid, x.confidence, x.match_method, x.evidence "
            f"FROM booth_crosswalk x JOIN election e ON e.election_id = x.election_id "
            f"WHERE {where} AND x.confidence < 0.85 AND NOT x.reviewed "
            f"ORDER BY x.confidence LIMIT 200",
            params,
        ),
    }


class SurnameEntry(BaseModel):
    surname_hi: str
    surname_en: str | None = None
    community_id: int
    weight: float = Field(1.0, gt=0, le=1)
    notes: str | None = None


@router.get("/surnames")
def list_surnames(user: AdminUser, q: str | None = None) -> dict:
    clauses, params = ["true"], []
    if q:
        clauses.append("(s.surname_hi ILIKE %s OR s.surname_en ILIKE %s)")
        params += [f"%{q}%", f"%{q}%"]
    return {"rows": query(
        f"SELECT s.surname_hi, s.surname_en, s.community_id, c.name_en AS community, "
        f"s.weight, s.notes FROM surname_dict s "
        f"JOIN community c ON c.community_id = s.community_id "
        f"WHERE {' AND '.join(clauses)} ORDER BY s.surname_hi LIMIT 500",
        params,
    )}


@router.post("/surnames")
def upsert_surname(body: SurnameEntry, user: AdminUser) -> dict:
    execute(
        "INSERT INTO surname_dict (surname_hi, surname_en, community_id, weight, notes) "
        "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (surname_hi, community_id) DO UPDATE SET "
        "surname_en = EXCLUDED.surname_en, weight = EXCLUDED.weight, notes = EXCLUDED.notes",
        (body.surname_hi, body.surname_en, body.community_id, body.weight, body.notes),
    )
    return {"saved": True, "next": "Re-run analytics.caste_estimate to apply this to the blend."}


@router.get("/usage")
def usage(user: AdminUser, days: int = Query(30, ge=1, le=180)) -> dict:
    """Token spend - the number the monthly cap is enforced against."""
    return {
        "by_day": query(
            "SELECT ts::date AS day, model, purpose, SUM(input_tokens) AS input_tokens, "
            "SUM(cached_tokens) AS cached_tokens, SUM(output_tokens) AS output_tokens, "
            "ROUND(SUM(cost_usd), 4) AS cost_usd, COUNT(*) AS calls "
            "FROM llm_usage WHERE ts >= now() - make_interval(days => %s) "
            "GROUP BY day, model, purpose ORDER BY day DESC, cost_usd DESC",
            (days,),
        ),
        "month_to_date": query_one(
            "SELECT ROUND(COALESCE(SUM(cost_usd), 0), 4) AS cost_usd, COUNT(*) AS calls "
            "FROM llm_usage WHERE date_trunc('month', ts) = date_trunc('month', now())"
        ),
        "cache_hit_rate": query_one(
            "SELECT ROUND(100.0 * COALESCE(SUM(cached_tokens), 0) / "
            "NULLIF(SUM(cached_tokens + input_tokens), 0), 1) AS pct "
            "FROM llm_usage WHERE ts >= now() - make_interval(days => %s) "
            "AND purpose LIKE 'chat.%%'",
            (days,),
        ),
        "by_user_today": query(
            "SELECT u.name, u.role, SUM(l.input_tokens + l.cached_tokens + l.output_tokens) "
            "AS tokens, ROUND(SUM(l.cost_usd), 4) AS cost_usd "
            "FROM llm_usage l JOIN app_user u ON u.user_id = l.user_id "
            "WHERE l.ts::date = %s GROUP BY u.name, u.role ORDER BY tokens DESC",
            (date.today(),),
        ),
    }


@router.get("/jobs")
def jobs(user: AdminUser, limit: int = Query(50, ge=1, le=200)) -> dict:
    return {
        "recent": query(
            "SELECT id, job, started, finished, status, meta, LEFT(log, 2000) AS log "
            "FROM job_run ORDER BY started DESC LIMIT %s",
            (limit,),
        ),
        "last_per_job": query(
            "SELECT DISTINCT ON (job) job, started, finished, status "
            "FROM job_run ORDER BY job, started DESC"
        ),
    }


@router.get("/sources")
def sources(user: AdminUser) -> dict:
    return {"rows": query(
        "SELECT doc_id, kind, filename, sha256, pages, ocr_pages, parse_status, "
        "fetched_at, parsed_at, note FROM source_doc ORDER BY fetched_at DESC LIMIT 200"
    )}
