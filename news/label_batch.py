"""Submit unlabelled articles to the Batch API (LLD 6.3), nightly at 01:00.

Batch is half price and these articles are not latency-sensitive, so every
offline labelling job goes through it. The system prompt is identical for every
request in the batch and carries a cache breakpoint, so the shared prefix is
billed once at cache rates rather than per article.

Budget check (LLD 13): ~100 articles/day at roughly 1,800 in / 300 out tokens
on Haiku at batch rates is about $0.17/day.
"""

from __future__ import annotations

import argparse
import json
import sys

from chatbot.llm import get_client
from common.config import get_settings
from common.db import execute, query
from common.jobs import job_context
from common.logging_setup import get_logger

log = get_logger(__name__)

ISSUE_ENUM = [
    "water", "roads", "electricity", "health", "education", "employment/migration",
    "mining/coal", "Parasnath/Marang Buru", "law-and-order", "welfare-schemes",
    "corruption", "candidate/organisation", "alliance", "other",
]

LABEL_SYSTEM = """You label local news from Giridih district, Jharkhand, for an
election-analysis system covering the Giridih assembly constituency (AC-32).

Return ONLY a JSON object, no prose, no code fence:

{
  "summary_hi": "2-3 sentence summary in Hindi",
  "summary_en": "2-3 sentence summary in English",
  "issues": ["..."],
  "parties": ["JMM", "BJP", "AJSU", "JLKM", "INC", "RJD", "..."],
  "persons": ["names of politicians or officials mentioned"],
  "sentiment_by_party": {"JMM": 0, "BJP": -1},
  "area_names": ["panchayat, ward, village or town names mentioned"],
  "relevance": 0.0
}

Rules:
- "issues" values must come from this list exactly: __ISSUE_LIST__
- sentiment_by_party is -2 (very negative for that party) to +2 (very positive).
  Include a party only if the article actually reflects on it.
- "persons" is for public figures acting in a public capacity. Do not list
  private individuals, victims, accused persons, or anyone named only as a
  resident.
- "area_names" copies place names as printed; do not translate or guess.
- "relevance" is 0.0-1.0: how relevant this is to AC-32 Giridih politics
  specifically. A Jharkhand-wide story that barely touches Giridih is 0.2.
- Report only what the article says. Do not infer, and do not add background.
""".replace("__ISSUE_LIST__", ", ".join(ISSUE_ENUM))

MAX_BODY_CHARS = 6000


def pending(limit: int) -> list[dict]:
    return query(
        "SELECT news_id, published, source, title, body FROM news_item "
        "WHERE labelled_at IS NULL AND batch_id IS NULL "
        "ORDER BY published DESC NULLS LAST, news_id DESC LIMIT %s",
        (limit,),
    )


def build_requests(rows: list[dict], model: str) -> list:
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    requests = []
    for row in rows:
        article = (
            f"Source: {row['source']}\n"
            f"Date: {row['published']}\n"
            f"Headline: {row['title']}\n\n"
            f"{(row['body'] or '')[:MAX_BODY_CHARS]}"
        )
        requests.append(Request(
            custom_id=f"news-{row['news_id']}",
            params=MessageCreateParamsNonStreaming(
                model=model,
                max_tokens=800,
                temperature=0,
                system=[{"type": "text", "text": LABEL_SYSTEM,
                         "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": article}],
            ),
        ))
    return requests


def submit(limit: int = 500) -> dict:
    settings = get_settings()
    rows = pending(limit)
    if not rows:
        return {"submitted": 0, "batch_id": None}

    client = get_client()
    batch = client.messages.batches.create(requests=build_requests(rows, settings.model_label))

    execute(
        "UPDATE news_item SET batch_id = %s WHERE news_id = ANY(%s)",
        (batch.id, [r["news_id"] for r in rows]),
    )
    log.info("submitted batch %s with %d article(s)", batch.id, len(rows))
    return {"submitted": len(rows), "batch_id": batch.id,
            "status": batch.processing_status}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Submit unlabelled news to the Batch API")
    ap.add_argument("--limit", type=int, default=500)
    args = ap.parse_args(argv)

    with job_context("news.label_batch") as job:
        stats = submit(args.limit)
        job.set(**stats, idempotency_key=stats.get("batch_id") or "none")
        job.log_line(json.dumps(stats))
        if not stats["submitted"]:
            job.log_line("nothing to label")
    return 0


if __name__ == "__main__":
    sys.exit(main())
