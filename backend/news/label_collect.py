"""Collect batch results and write news_item labels (LLD 6.4), at 07:00.

Batch results arrive in any order, so everything is keyed by custom_id. Area
names are resolved through area_alias; a name that does not resolve goes to
review_queue rather than being dropped, because an unrecognised panchayat name
is usually a spelling variant worth adding to the alias table.
"""

from __future__ import annotations

import argparse
import json
import sys

from chatbot.budget import Usage, record
from chatbot.llm import get_client
from common.config import get_settings
from common.db import execute, query, query_one
from common.jobs import job_context
from common.logging_setup import get_logger
from common.textnorm import alias_key

log = get_logger(__name__)

MIN_RELEVANCE = 0.25


def open_batches() -> list[str]:
    rows = query(
        "SELECT DISTINCT batch_id FROM news_item WHERE batch_id IS NOT NULL AND labelled_at IS NULL"
    )
    return [r["batch_id"] for r in rows]


def resolve_areas(names: list[str]) -> tuple[list[int], list[str]]:
    """(resolved area_ids, names that did not resolve)."""
    resolved, unknown = [], []
    for name in names or []:
        key = alias_key(name)
        if not key:
            continue
        row = query_one("SELECT area_id FROM area_alias WHERE alias = %s", (key,))
        if row:
            resolved.append(row["area_id"])
        else:
            unknown.append(name)
    return sorted(set(resolved)), unknown


def _parse_payload(text: str) -> dict | None:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        return json.loads(text[start: end + 1])
    except json.JSONDecodeError:
        return None


def collect(batch_id: str) -> dict:
    settings = get_settings()
    client = get_client()

    batch = client.messages.batches.retrieve(batch_id)
    if batch.processing_status != "ended":
        log.info("batch %s is %s - leaving it for the next run", batch_id, batch.processing_status)
        return {"batch_id": batch_id, "status": batch.processing_status, "labelled": 0}

    stats = {"batch_id": batch_id, "status": "ended", "labelled": 0,
             "errored": 0, "unparsed": 0, "low_relevance": 0, "unknown_areas": 0}
    unknown_names: set[str] = set()

    for result in client.messages.batches.results(batch_id):
        news_id = int(result.custom_id.removeprefix("news-"))
        kind = result.result.type

        if kind != "succeeded":
            stats["errored"] += 1
            log.warning("news %s: batch result %s", news_id, kind)
            # Clear batch_id so the next run can retry it.
            execute("UPDATE news_item SET batch_id = NULL WHERE news_id = %s", (news_id,))
            continue

        message = result.result.message
        record(
            Usage(
                model=settings.model_label, purpose="news.label",
                input_tokens=getattr(message.usage, "input_tokens", 0) or 0,
                cached_tokens=getattr(message.usage, "cache_read_input_tokens", 0) or 0,
                output_tokens=getattr(message.usage, "output_tokens", 0) or 0,
                is_batch=True, request_id=message.id,
            ),
            user_id=None,
        )

        text = "".join(b.text for b in message.content if b.type == "text")
        payload = _parse_payload(text)
        if payload is None:
            stats["unparsed"] += 1
            execute("UPDATE news_item SET batch_id = NULL WHERE news_id = %s", (news_id,))
            continue

        if float(payload.get("relevance", 1.0) or 0) < MIN_RELEVANCE:
            stats["low_relevance"] += 1

        area_ids, unknown = resolve_areas(payload.get("area_names", []))
        unknown_names.update(unknown)

        sentiment_by_party = payload.get("sentiment_by_party") or {}
        values = [v for v in sentiment_by_party.values() if isinstance(v, (int, float))]
        overall = int(round(sum(values) / len(values))) if values else None

        execute(
            "UPDATE news_item SET summary_hi = %s, summary_en = %s, issues = %s, parties = %s, "
            "persons = %s, sentiment = %s, sentiment_by_party = %s, area_ids = %s, "
            "area_names_raw = %s, labelled_by = %s, labelled_at = now() WHERE news_id = %s",
            (payload.get("summary_hi"), payload.get("summary_en"),
             payload.get("issues") or [], payload.get("parties") or [],
             payload.get("persons") or [], overall,
             json.dumps(sentiment_by_party, ensure_ascii=False), area_ids,
             payload.get("area_names") or [], settings.model_label, news_id),
        )
        stats["labelled"] += 1

    if unknown_names:
        stats["unknown_areas"] = len(unknown_names)
        execute(
            "INSERT INTO review_queue (kind, ref, payload, note) VALUES ('area_alias', %s, %s, %s) "
            "ON CONFLICT (kind, ref) DO UPDATE SET payload = EXCLUDED.payload, note = EXCLUDED.note",
            (f"batch:{batch_id}",
             json.dumps({"names": sorted(unknown_names)}, ensure_ascii=False),
             f"{len(unknown_names)} place name(s) in the news could not be matched to an area - "
             f"add the spelling variants to area_alias"),
        )
    return stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Collect finished news labelling batches")
    ap.add_argument("--batch-id", help="collect only this batch")
    args = ap.parse_args(argv)

    with job_context("news.label_collect") as job:
        batches = [args.batch_id] if args.batch_id else open_batches()
        if not batches:
            job.log_line("no open batches")
            return 0
        totals = {"labelled": 0, "errored": 0, "unparsed": 0}
        for batch_id in batches:
            stats = collect(batch_id)
            for key in totals:
                totals[key] += stats.get(key, 0)
            job.log_line(json.dumps(stats))
        job.set(**totals, batches=len(batches))

    if totals["labelled"]:
        from news.embed import main as embed_main

        embed_main([])
    return 0


if __name__ == "__main__":
    sys.exit(main())
