"""News timeline, issue clusters and ground reports (module 8)."""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from api.deps import CurrentAC, CurrentUser
from common.db import execute, query, query_one
from common.logging_setup import get_logger
from common.pii import PiiRejected, screen

log = get_logger(__name__)
router = APIRouter(prefix="/acs/{ac_number}", tags=["news"])

ISSUES = [
    "water", "roads", "electricity", "health", "education", "employment/migration",
    "mining/coal", "Parasnath/Marang Buru", "law-and-order", "welfare-schemes",
    "corruption", "candidate/organisation", "alliance", "other",
]


@router.get("/news")
def news_list(user: CurrentUser, ac: CurrentAC, q: str | None = None,
              date_from: date | None = None, date_to: date | None = None,
              issue: str | None = None, area_id: int | None = None,
              include_unlabelled: bool = False,
              limit: int = Query(50, ge=1, le=200)) -> dict:
    """Recent items for this constituency. With `q`, ranks by vector similarity.

    An article can concern several constituencies, so `news_item.ac_ids` is an
    array and this filters with the GIN-indexed `@>`.

    `include_unlabelled` exists because labelling needs an Anthropic key. Without
    one the crawl still works but nothing is ever labelled, and the page was
    permanently empty with no explanation - the filter on labelled_at looked
    like "no news" rather than "no key". Unlabelled items are keyword-tagged to
    an AC by the crawl (news.crawl_rss.tag_acs) and the UI marks them as such.
    """
    clauses, params = ["ac_ids @> ARRAY[%s]::INT[]"], [ac.ac_id]
    if not include_unlabelled:
        clauses.append("labelled_at IS NOT NULL")
    if date_from:
        clauses.append("published >= %s")
        params.append(date_from)
    if date_to:
        clauses.append("published <= %s")
        params.append(date_to)
    if issue:
        clauses.append("%s = ANY(issues)")
        params.append(issue)
    if area_id:
        clauses.append("%s = ANY(area_ids)")
        params.append(area_id)
    where = " AND ".join(clauses)

    vector = None
    if q:
        try:
            from news.embed import embed_query

            vector = embed_query(q)
        except Exception as exc:
            log.warning("embedding unavailable (%s) - ranking by date", exc)

    if vector is not None:
        rows = query(
            f"SELECT news_id, published, source, title, summary_hi, summary_en, issues, "
            f"parties, sentiment, sentiment_by_party, area_ids, url, "
            f"1 - (embedding <=> %s::vector) AS similarity "
            f"FROM news_item WHERE {where} AND embedding IS NOT NULL "
            f"ORDER BY embedding <=> %s::vector LIMIT %s",
            [str(vector)] + params + [str(vector), limit],
        )
    else:
        rows = query(
            f"SELECT news_id, published, source, title, summary_hi, summary_en, issues, "
            f"parties, sentiment, sentiment_by_party, area_ids, url, NULL AS similarity "
            f"FROM news_item WHERE {where} ORDER BY published DESC, news_id DESC LIMIT %s",
            params + [limit],
        )
    return {"rows": rows, "count": len(rows), "issues": ISSUES}


@router.get("/news/issues")
def issue_clusters(user: CurrentUser, ac: CurrentAC,
                   days: int = Query(30, ge=1, le=365)) -> dict:
    """What the local press has been about lately, and how it is trending."""
    since = date.today() - timedelta(days=days)
    return {
        "since": since,
        "by_issue": query(
            "SELECT issue, COUNT(*) AS items, ROUND(AVG(sentiment)::NUMERIC, 2) AS avg_sentiment "
            "FROM news_item, UNNEST(issues) AS issue "
            "WHERE published >= %s AND labelled_at IS NOT NULL "
            "  AND ac_ids @> ARRAY[%s]::INT[] "
            "GROUP BY issue ORDER BY items DESC",
            (since, ac.ac_id),
        ),
        "by_week": query(
            "SELECT date_trunc('week', published)::date AS week, COUNT(*) AS items "
            "FROM news_item WHERE published >= %s AND labelled_at IS NOT NULL "
            "  AND ac_ids @> ARRAY[%s]::INT[] "
            "GROUP BY week ORDER BY week",
            (since, ac.ac_id),
        ),
        "by_party": query(
            "SELECT party, COUNT(*) AS mentions "
            "FROM news_item, UNNEST(parties) AS party "
            "WHERE published >= %s AND labelled_at IS NOT NULL "
            "  AND ac_ids @> ARRAY[%s]::INT[] "
            "GROUP BY party ORDER BY mentions DESC",
            (since, ac.ac_id),
        ),
    }


class GroundReport(BaseModel):
    booth_uid: str | None = None
    area_id: int | None = None
    text: str = Field(min_length=5, max_length=4000)
    issues: list[str] = Field(default_factory=list)
    sentiment: int | None = Field(default=None, ge=-2, le=2)


@router.post("/ground-reports", status_code=201)
def create_ground_report(body: GroundReport, user: CurrentUser, ac: CurrentAC) -> dict:
    """Field form from a booth in-charge. Highest signal, lowest volume (HLD 4).

    Reporters write about places and conditions, not about named individuals;
    the form text is stored as written and is never exposed to raw SQL.
    """
    # E5: this was the one unguarded free-text ingress in a system whose whole
    # design is aggregate-only - 4,000 characters from any authenticated user,
    # stored verbatim and embedded for vector search, with nothing screening it.
    # The docstring asserted that reporters write about places and conditions
    # rather than named individuals, which is an assumption, not a control.
    try:
        screen(body.text, field="report")
    except PiiRejected as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from None

    # The booth or area must be in this AC, and in the reporter's own block for
    # a block user. Unchecked, an unknown uid hit the foreign key (a 500) and a
    # valid uid from another AC or block was stored against this one.
    target = None
    if body.booth_uid:
        target = query_one("SELECT b.ac_id, a.block_id FROM booth b "
                           "JOIN area a ON a.area_id = b.area_id WHERE b.booth_uid = %s",
                           (body.booth_uid,))
    elif body.area_id is not None:
        target = query_one("SELECT ac_id, block_id FROM area WHERE area_id = %s",
                           (body.area_id,))
    if (body.booth_uid or body.area_id is not None) and (
            target is None or target["ac_id"] != ac.ac_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such booth or area in this constituency")
    if target is not None and user.role == "block" and target["block_id"] != user.block_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "That booth is outside your block")

    execute(
        "INSERT INTO ground_report (ac_id, booth_uid, area_id, reporter_id, text, issues, "
        "sentiment) VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (ac.ac_id, body.booth_uid, body.area_id, user.user_id, body.text, body.issues,
         body.sentiment),
    )
    return {"saved": True}


@router.get("/ground-reports")
def list_ground_reports(user: CurrentUser, ac: CurrentAC, booth_uid: str | None = None,
                        limit: int = Query(50, ge=1, le=200)) -> dict:
    clauses, params = ["g.ac_id = %s"], [ac.ac_id]
    if booth_uid:
        clauses.append("g.booth_uid = %s")
        params.append(booth_uid)
    if user.role == "block":
        clauses.append("a.block_id = %s")
        params.append(user.block_id)
    params.append(limit)
    return {"rows": query(
        f"SELECT g.report_id, g.reported_at, g.booth_uid, g.text, g.issues, g.sentiment, "
        f"u.name AS reporter FROM ground_report g "
        f"LEFT JOIN booth b ON b.booth_uid = g.booth_uid "
        f"LEFT JOIN area a ON a.area_id = COALESCE(b.area_id, g.area_id) "
        f"LEFT JOIN app_user u ON u.user_id = g.reporter_id "
        f"WHERE {' AND '.join(clauses)} ORDER BY g.reported_at DESC LIMIT %s",
        params,
    )}
