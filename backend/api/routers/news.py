"""News timeline, issue clusters and ground reports (module 8)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from api.deps import CurrentAC, CurrentUser
from common.db import execute, query, query_one
from common.logging_setup import get_logger
from common.pii import PiiRejected, screen

log = get_logger(__name__)
router = APIRouter(prefix="/acs/{ac_number}", tags=["news"])

# The same list as news/label_batch.py ISSUE_ENUM (tests/test_news_label_rules.py
# keeps them equal). Not imported: label_batch pulls in the Anthropic client,
# which the API does not need.
ISSUES = [
    "water", "roads", "electricity", "health", "education", "employment/migration",
    "mining/coal", "Parasnath/Marang Buru", "law-and-order", "welfare-schemes",
    "corruption", "electoral-roll/SIR", "candidate/organisation", "alliance", "other",
]

# Items as lists return them. `relevance` and `label_method` let the UI say how
# an item was labelled; `persons` are candidates only (news/label_rules.py).
_ITEM_COLUMNS = (
    "news_id, published, source, title, summary_hi, summary_en, issues, parties, persons, "
    "sentiment, sentiment_by_party, area_ids, ac_ids, scope, relevance, label_method, url"
)

# "Political" in the summary: a party or candidate named, or an election or
# office word (the rules score those 0.3 or more).
_POLITICAL = "(cardinality(parties) > 0 OR cardinality(persons) > 0 OR relevance >= 0.3)"


def _places(ac) -> list[dict]:
    """The places of one constituency a reader would recognise in a headline:
    the AC itself, its blocks and towns, landmarks and named areas, each with
    the folded spellings the crawler records in `matched_terms`. Blocks and the
    town that share a name ("Giridih Block", "Giridih Municipal Corporation")
    are one place, because a headline cannot tell them apart."""
    from common.textnorm import fold
    from news.crawl_rss import _BLOCK_SUFFIX, AC_LANDMARKS

    places: dict[str, dict] = {}

    def add(name_en: str, name_hi: str | None, kind: str) -> None:
        name_en = _BLOCK_SUFFIX.sub("", name_en or "").strip()
        name_hi = _BLOCK_SUFFIX.sub("", name_hi or "").strip()
        key = fold(name_en)
        if not key or len(key) < 4 or key.startswith(("ward", "unassigned", "ps list")):
            return
        place = places.setdefault(key, {"place": name_en, "name_en": name_en,
                                        "name_hi": name_hi or name_en, "kind": kind,
                                        "terms": set()})
        place["terms"].update(t for t in (fold(name_en), fold(name_hi)) if t)

    add(ac.name_en, ac.name_hi, "constituency")
    for r in query("SELECT name_en, name_hi FROM block WHERE ac_id = %s ORDER BY block_id",
                   (ac.ac_id,)):
        add(r["name_en"], r["name_hi"], "block")
    for en, hi_name in AC_LANDMARKS.get(ac.ac_number, []):
        add(en, hi_name, "landmark")
    for r in query("SELECT name_en, name_hi FROM area WHERE ac_id = %s ORDER BY area_id",
                   (ac.ac_id,)):
        add(r["name_en"], r["name_hi"], "area")
    return [{**p, "terms": sorted(p["terms"])} for p in places.values()]


def _scope_clause(scope: str, ac_id: int) -> tuple[str, list]:
    """'ac': items tagged to this constituency. 'state': all the Jharkhand news
    the crawler kept, tagged or not - the by-election is fought by state parties
    over state issues, and that coverage names no seat."""
    if scope == "ac":
        return "ac_ids @> ARRAY[%s]::INT[]", [ac_id]
    return "TRUE", []


@router.get("/news")
def news_list(user: CurrentUser, ac: CurrentAC, q: str | None = None,
              date_from: date | None = None, date_to: date | None = None,
              days: int | None = Query(None, ge=1, le=3650),
              issue: str | None = None, party: str | None = None,
              area_id: int | None = None, place: str | None = None,
              scope: Literal["ac", "state"] = "ac",
              sort: Literal["date", "relevance"] = "date",
              include_unlabelled: bool = False,
              limit: int = Query(50, ge=1, le=200)) -> dict:
    """Recent items for this constituency, or with scope=state for all of
    Jharkhand. With `q`, ranks by vector similarity when embeddings exist and
    falls back to a title match when they do not.

    An article can concern several constituencies, so `news_item.ac_ids` is an
    array and this filters with the GIN-indexed `@>`.

    `include_unlabelled` covers items the labellers have not reached. Every
    crawl is followed by news/label_rules.py, so that is only the moments in
    between; an item labelled by keyword rules says so in `label_method`.
    """
    scope_sql, params = _scope_clause(scope, ac.ac_id)
    clauses = [scope_sql]
    if not include_unlabelled:
        clauses.append("labelled_at IS NOT NULL")
    if days:
        clauses.append("published >= %s")
        params.append(date.today() - timedelta(days=days))
    if date_from:
        clauses.append("published >= %s")
        params.append(date_from)
    if date_to:
        clauses.append("published <= %s")
        params.append(date_to)
    if issue:
        clauses.append("%s = ANY(issues)")
        params.append(issue)
    if party:
        clauses.append("%s = ANY(parties)")
        params.append(party)
    if area_id:
        clauses.append("%s = ANY(area_ids)")
        params.append(area_id)
    if place:
        # A place of this constituency, as /news/summary lists it.
        match = next((p for p in _places(ac) if p["place"] == place), None)
        if match is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"no place {place!r} in this constituency")
        clauses.append("matched_terms && %s::TEXT[]")
        params.append(match["terms"])

    vector = None
    if q:
        try:
            from news.embed import embed_query

            vector = embed_query(q)
        except Exception as exc:
            log.warning("embedding unavailable (%s) - matching titles instead", exc)

    rows = []
    if vector is not None:
        where = " AND ".join(clauses)
        rows = query(
            f"SELECT {_ITEM_COLUMNS}, 1 - (embedding <=> %s::vector) AS similarity "
            f"FROM news_item WHERE {where} AND embedding IS NOT NULL "
            f"ORDER BY embedding <=> %s::vector LIMIT %s",
            [str(vector)] + params + [str(vector), limit],
        )
    # news.embed runs nightly, so fresh items have no embedding yet; with none
    # the vector search finds nothing, and a title match is the honest fallback.
    if not rows:
        if q:
            # Without embeddings the search box used to be ignored altogether.
            clauses.append("title ILIKE %s")
            params.append(f"%{q}%")
        where = " AND ".join(clauses)
        order = ("relevance DESC NULLS LAST, published DESC NULLS LAST, news_id DESC"
                 if sort == "relevance" else "published DESC NULLS LAST, news_id DESC")
        rows = query(
            f"SELECT {_ITEM_COLUMNS}, NULL AS similarity "
            f"FROM news_item WHERE {where} ORDER BY {order} LIMIT %s",
            params + [limit],
        )
    return {"rows": rows, "count": len(rows), "issues": ISSUES, "scope": scope}


@router.get("/news/summary")
def news_summary(user: CurrentUser, ac: CurrentAC,
                 days: int = Query(30, ge=1, le=365),
                 scope: Literal["ac", "state"] = "ac") -> dict:
    """What the press has said in the last `days`: which parties it talks
    about, which issues, and the most relevant items.

    Party figures are **mentions in coverage** - items naming a party - not
    support, and the UI labels them so. Tone exists only for LLM-labelled
    items; the keyword rules never set it.
    """
    since = date.today() - timedelta(days=days)
    scope_sql, scope_params = _scope_clause(scope, ac.ac_id)
    base = f"published >= %s AND labelled_at IS NOT NULL AND {scope_sql}"
    params = [since] + scope_params

    totals = query_one(
        f"SELECT COUNT(*) AS items, "
        f"  COUNT(*) FILTER (WHERE {_POLITICAL}) AS political, "
        f"  COUNT(*) FILTER (WHERE cardinality(parties) > 0) AS with_party, "
        f"  COUNT(*) FILTER (WHERE label_method = 'rules') AS rules_labelled, "
        f"  COUNT(*) FILTER (WHERE label_method = 'llm') AS llm_labelled, "
        f"  COUNT(*) FILTER (WHERE sentiment IS NOT NULL) AS with_tone "
        f"FROM news_item WHERE {base}", params)
    unlabelled = query_one(
        f"SELECT COUNT(*) AS n FROM news_item WHERE published >= %s "
        f"AND labelled_at IS NULL AND {scope_sql}", params)
    crawled = query_one("SELECT (SELECT MAX(last_ok_at) FROM news_source) AS last_ok, "
                        "(SELECT MAX(fetched_at) FROM news_item) AS last_item")

    parties = query(
        f"SELECT party, COUNT(*) AS items FROM news_item, UNNEST(parties) AS party "
        f"WHERE {base} GROUP BY party ORDER BY items DESC, party", params)
    mentions = sum(r["items"] for r in parties) or 1
    for r in parties:
        r["share_pct"] = round(100.0 * r["items"] / mentions, 1)

    party_weeks = query(
        f"SELECT date_trunc('week', published)::date AS week, party, COUNT(*) AS items "
        f"FROM news_item, UNNEST(parties) AS party "
        f"WHERE {base} GROUP BY week, party ORDER BY week, party", params)

    issues = query(
        f"SELECT issue, COUNT(*) AS items FROM news_item, UNNEST(issues) AS issue "
        f"WHERE {base} AND issue <> 'other' GROUP BY issue ORDER BY items DESC, issue", params)
    examples = query(
        f"SELECT issue, news_id, title, url, source, published FROM ("
        f"  SELECT issue, news_id, title, url, source, published, ROW_NUMBER() OVER ("
        f"    PARTITION BY issue ORDER BY relevance DESC NULLS LAST, published DESC, news_id DESC"
        f"  ) AS rank FROM news_item, UNNEST(issues) AS issue "
        f"  WHERE {base} AND issue <> 'other') ranked WHERE rank <= 2 "
        f"ORDER BY issue, rank", params)
    by_issue: dict[str, list] = {}
    for e in examples:
        by_issue.setdefault(e.pop("issue"), []).append(e)
    for r in issues:
        r["examples"] = by_issue.get(r["issue"], [])
    other = query_one(
        f"SELECT COUNT(*) AS n FROM news_item WHERE {base} AND issues = ARRAY['other']", params)

    # Where the news is from. In this constituency: its places, by the terms
    # the crawler matched. Across Jharkhand: our constituencies, plus the
    # items that name none of them.
    if scope == "ac":
        places = _places(ac)
        counts = {p["place"]: 0 for p in places}
        for row in query(f"SELECT matched_terms FROM news_item WHERE {base}", params):
            hit = set(row["matched_terms"] or [])
            for p in places:
                if hit.intersection(p["terms"]):
                    counts[p["place"]] += 1
        where = [{"place": p["place"], "name_en": p["name_en"], "name_hi": p["name_hi"],
                  "kind": p["kind"], "items": counts[p["place"]]}
                 for p in places if counts[p["place"]]]
        where.sort(key=lambda r: -r["items"])
    else:
        where = query(
            f"SELECT a.ac_number, a.name_en, a.name_hi, COUNT(*) AS items "
            f"FROM news_item n JOIN ac a ON a.ac_id = ANY(n.ac_ids) "
            f"WHERE {base} GROUP BY a.ac_number, a.name_en, a.name_hi "
            f"ORDER BY items DESC, a.ac_number", params)
        unseated = query_one(
            f"SELECT COUNT(*) AS n FROM news_item WHERE {base} AND cardinality(ac_ids) = 0",
            params)
        where.append({"ac_number": None, "name_en": "Rest of Jharkhand",
                      "name_hi": "शेष झारखंड", "items": unseated["n"]})

    top = query(
        f"SELECT {_ITEM_COLUMNS} FROM news_item WHERE {base} "
        f"ORDER BY relevance DESC NULLS LAST, published DESC NULLS LAST, news_id DESC LIMIT 8",
        params)

    return {
        "since": since, "days": days, "scope": scope,
        "totals": {**totals, "unlabelled": unlabelled["n"]},
        "last_crawled": crawled["last_ok"] or crawled["last_item"],
        "parties": parties,
        "party_weeks": party_weeks,
        "issues": issues,
        "other_items": other["n"],
        "places": where,
        "top": top,
    }


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
