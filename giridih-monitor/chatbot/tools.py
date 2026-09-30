"""The four tools the assistant may call (LLD 8.2).

    run_sql(sql)                 read-only, guarded, row-limited, 5 s timeout
    search_news(query, ...)      pgvector similarity over labelled news
    get_booth_card(booth_uid)    precomputed per-booth summary
    make_chart(spec)             returns a Vega-Lite spec for the frontend

Tool definitions are built once and never reordered: the tool list is part of
the cached prompt prefix, so a different order silently costs a full cache miss
on every request.
"""

from __future__ import annotations

import json

from chatbot.sql_guard import SqlRejected, to_csv
from chatbot.sql_guard import run as run_guarded
from common.logging_setup import get_logger

log = get_logger(__name__)

TOOL_DEFS = [
    {
        "name": "run_sql",
        "description": (
            "Run one read-only SELECT against the election analytics database and get CSV back. "
            "Use this for every number you report. Only the tables in the schema documentation "
            "are readable; a LIMIT of 500 is imposed. Join booths across years on booth_uid, "
            "never on ps_number. If the query is rejected, the error says why - fix it and retry."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sql": {"type": "string", "description": "A single SELECT statement."},
            },
            "required": ["sql"],
            "additionalProperties": False,
        },
    },
    {
        "name": "search_news",
        "description": (
            "Semantic search over Giridih news articles and field reports from booth in-charges. "
            "Returns id, date, source, title and a Hindi summary. Use it for questions about "
            "issues, incidents, sentiment or what is being said locally - never for vote counts."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to look for, in Hindi or English."},
                "date_from": {"type": "string", "description": "YYYY-MM-DD, inclusive."},
                "date_to": {"type": "string", "description": "YYYY-MM-DD, inclusive."},
                "area_ids": {"type": "array", "items": {"type": "integer"},
                             "description": "Restrict to these panchayats or wards."},
                "issues": {"type": "array", "items": {"type": "string"},
                           "description": "Restrict to these issue tags."},
                "limit": {"type": "integer", "description": "Maximum items, default 8."},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_booth_card",
        "description": (
            "Everything known about one booth in a single call: results for every election, "
            "current electors, new voters, community estimates with confidence, priority score "
            "and crosswalk quality. Cheaper and more complete than several SQL queries."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "booth_uid": {"type": "string", "description": "Stable booth id, e.g. B0042."},
            },
            "required": ["booth_uid"],
            "additionalProperties": False,
        },
    },
    {
        "name": "make_chart",
        "description": (
            "Render a chart in the user's panel from rows you already retrieved. Pass a "
            "Vega-Lite spec with the data inline. Use it only when the shape of the data is the "
            "point; a short table is usually clearer."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "spec": {"type": "object", "description": "A Vega-Lite v5 spec with inline data."},
                "title": {"type": "string"},
            },
            "required": ["spec"],
            "additionalProperties": False,
        },
    },
]


# --------------------------------------------------------------------------
# Implementations
# --------------------------------------------------------------------------

def tool_run_sql(sql: str, **_) -> tuple[str, bool]:
    """(result text, is_error). A rejection is returned to the model, not raised."""
    try:
        rows, guarded = run_guarded(sql)
    except SqlRejected as exc:
        return f"Query rejected: {exc}", True
    except Exception as exc:
        log.warning("run_sql failed: %s", exc)
        return f"Query failed: {str(exc).splitlines()[0][:300]}", True

    note = ""
    if guarded.rewritten:
        note = f"\n(a LIMIT of {guarded.limit} was applied)"
    return f"{len(rows)} row(s) from {', '.join(guarded.tables)}{note}\n\n{to_csv(rows)}", False


def tool_search_news(query: str, date_from: str | None = None, date_to: str | None = None,
                     area_ids: list[int] | None = None, issues: list[str] | None = None,
                     limit: int = 8, **_) -> tuple[str, bool]:
    from common.db import query as db_query

    try:
        from news.embed import embed_query

        vector = embed_query(query)
    except Exception as exc:
        log.warning("embedding unavailable (%s) - falling back to text search", exc)
        vector = None

    clauses = ["labelled_at IS NOT NULL"]
    params: list = []
    if date_from:
        clauses.append("published >= %s")
        params.append(date_from)
    if date_to:
        clauses.append("published <= %s")
        params.append(date_to)
    if area_ids:
        clauses.append("area_ids && %s")
        params.append(list(area_ids))
    if issues:
        clauses.append("issues && %s")
        params.append(list(issues))
    where = " AND ".join(clauses)
    limit = max(1, min(int(limit or 8), 20))

    try:
        if vector is not None:
            params_v = [str(vector)] + params + [str(vector), limit]
            rows = db_query(
                f"SELECT news_id, published, source, title, summary_hi, issues, url, "
                f"1 - (embedding <=> %s::vector) AS similarity "
                f"FROM news_item WHERE {where} AND embedding IS NOT NULL "
                f"ORDER BY embedding <=> %s::vector LIMIT %s",
                params_v,
            )
        else:
            rows = db_query(
                f"SELECT news_id, published, source, title, summary_hi, issues, url, NULL AS similarity "
                f"FROM news_item WHERE {where} AND (title ILIKE %s OR body ILIKE %s) "
                f"ORDER BY published DESC LIMIT %s",
                params + [f"%{query}%", f"%{query}%", limit],
            )
    except Exception as exc:
        log.warning("search_news failed: %s", exc)
        return f"News search failed: {str(exc).splitlines()[0][:200]}", True

    if not rows:
        return "No matching news items. Say that no coverage was found for this period.", False

    lines = []
    for r in rows:
        sim = f" (similarity {r['similarity']:.2f})" if r.get("similarity") is not None else ""
        lines.append(
            f"[{r['news_id']}] {r['published']} · {r['source']}{sim}\n"
            f"  {r['title']}\n"
            f"  {(r['summary_hi'] or '')[:400]}\n"
            f"  issues: {', '.join(r['issues'] or [])}\n"
            f"  url: {r['url']}"
        )
    return "\n\n".join(lines), False


def tool_get_booth_card(booth_uid: str, **_) -> tuple[str, bool]:
    from api.booth_card import build_booth_card

    try:
        card = build_booth_card(booth_uid)
    except LookupError:
        return f"No booth {booth_uid!r}. Booth ids look like B0042.", True
    except Exception as exc:
        log.warning("get_booth_card failed: %s", exc)
        return f"Could not build the booth card: {str(exc).splitlines()[0][:200]}", True
    return json.dumps(card, ensure_ascii=False, default=str, indent=1), False


def tool_make_chart(spec: dict, title: str | None = None, **_) -> tuple[str, bool]:
    if not isinstance(spec, dict) or not spec:
        return "make_chart needs a Vega-Lite spec object.", True
    if title:
        spec.setdefault("title", title)
    spec.setdefault("$schema", "https://vega.github.io/schema/vega-lite/v5.json")
    return json.dumps({"chart": spec}, ensure_ascii=False), False


DISPATCH = {
    "run_sql": tool_run_sql,
    "search_news": tool_search_news,
    "get_booth_card": tool_get_booth_card,
    "make_chart": tool_make_chart,
}


def execute(name: str, arguments: dict) -> tuple[str, bool]:
    fn = DISPATCH.get(name)
    if fn is None:
        return f"Unknown tool {name!r}.", True
    try:
        return fn(**(arguments or {}))
    except TypeError as exc:
        return f"Bad arguments for {name}: {exc}", True
