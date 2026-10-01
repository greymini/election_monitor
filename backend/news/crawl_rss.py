"""News crawler (LLD 6.1). Runs every 4 hours.

Only items matching a Giridih geographic keyword are kept - a Jharkhand-wide
feed is mostly irrelevant here, and every item kept is an item that later costs
money to label.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from typing import Any

from common.config import get_settings
from common.db import query, query_one
from common.jobs import job_context
from common.logging_setup import get_logger
from common.textnorm import fold, normalize_text
from news.dedupe import find_duplicate, simhash, title_hash, to_signed_64, url_hash

log = get_logger(__name__)

# An item must mention at least one of these to be kept. Panchayat and ward
# names from the database are appended at run time.
GEO_KEYWORDS = [
    "गिरिडीह", "giridih",
    "पीरटांड", "पीरटांड़", "pirtand", "pirtanr",
    "पारसनाथ", "parasnath", "मधुबन", "madhuban",
    "डुमरी", "दुमरी", "dumri",
    "सरिया", "बगोदर", "बेंगाबाद", "गांडेय", "जमुआ", "तिसरी", "देवरी",
    "ac-32", "एसी-32", "मरांग बुरु", "marang buru",
]


def geo_keywords() -> list[str]:
    keys = [fold(k) for k in GEO_KEYWORDS]
    try:
        for row in query("SELECT name_en, name_hi FROM area"):
            for name in (row["name_en"], row["name_hi"]):
                folded = fold(name)
                # Skip 'Ward 7' and friends - far too generic to filter on.
                if folded and len(folded) > 4 and not folded.startswith(("ward", "वार्ड")):
                    keys.append(folded)
    except Exception as exc:
        log.warning("could not load area names for geo filtering (%s)", exc)
    return sorted(set(k for k in keys if k))


def is_relevant(title: str, summary: str, keywords: list[str]) -> bool:
    haystack = fold(f"{title} {summary}")
    return any(k in haystack for k in keywords)


def fetch_feed(url: str, timeout: int, user_agent: str) -> list[dict[str, Any]]:
    import feedparser
    import httpx

    try:
        response = httpx.get(url, timeout=timeout, follow_redirects=True,
                             headers={"User-Agent": user_agent})
        response.raise_for_status()
    except Exception as exc:
        log.warning("feed failed %s: %s", url, exc)
        raise

    parsed = feedparser.parse(response.content)
    items = []
    for entry in parsed.entries:
        published = None
        for field in ("published_parsed", "updated_parsed"):
            value = getattr(entry, field, None)
            if value:
                published = datetime(*value[:6], tzinfo=UTC).date()
                break
        items.append({
            "url": getattr(entry, "link", "") or "",
            "title": normalize_text(getattr(entry, "title", "")),
            "summary": normalize_text(
                getattr(entry, "summary", "") or getattr(entry, "description", "")
            ),
            "published": published,
        })
    return items


def crawl(limit: int | None = None) -> dict:
    settings = get_settings()
    keywords = geo_keywords()
    sources = query("SELECT source_id, name, url, kind, lang FROM news_source WHERE is_active")
    stats = {"sources": len(sources), "fetched": 0, "relevant": 0,
             "inserted": 0, "duplicates": 0, "failed_sources": 0}
    budget = limit or settings.news_max_items

    # Recent simhashes to compare against - a duplicate older than this is not
    # worth the memory.
    existing = [(r["news_id"], r["simhash"]) for r in query(
        "SELECT news_id, simhash FROM news_item "
        "WHERE simhash IS NOT NULL AND fetched_at > now() - interval '30 days'"
    )]

    for source in sources:
        try:
            items = fetch_feed(source["url"], settings.news_timeout, settings.news_user_agent)
        except Exception as exc:
            stats["failed_sources"] += 1
            query_one("UPDATE news_source SET last_error = %s WHERE source_id = %s RETURNING source_id",
                      (str(exc)[:500], source["source_id"]))
            continue

        query_one("UPDATE news_source SET last_ok_at = now(), last_error = NULL "
                  "WHERE source_id = %s RETURNING source_id", (source["source_id"],))

        for item in items:
            stats["fetched"] += 1
            if stats["inserted"] >= budget:
                break
            if not item["url"] or not item["title"]:
                continue
            if not is_relevant(item["title"], item["summary"], keywords):
                continue
            stats["relevant"] += 1

            if find_duplicate(item["title"], existing) is not None:
                stats["duplicates"] += 1
                continue

            h = simhash(item["title"])
            row = query_one(
                "INSERT INTO news_item (url, url_hash, title_hash, simhash, published, source, "
                "title, body) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (url) DO NOTHING RETURNING news_id",
                (item["url"], url_hash(item["url"]), title_hash(item["title"]),
                 to_signed_64(h), item["published"], source["name"],
                 item["title"], item["summary"]),
            )
            if row:
                stats["inserted"] += 1
                existing.append((row["news_id"], to_signed_64(h)))
            else:
                stats["duplicates"] += 1
    return stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Crawl configured news sources")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args(argv)

    with job_context("news.crawl") as job:
        stats = crawl(args.limit)
        job.set(**stats)
        job.log_line(
            f"{stats['sources']} source(s), {stats['fetched']} item(s) seen, "
            f"{stats['relevant']} Giridih-relevant, {stats['inserted']} new, "
            f"{stats['duplicates']} duplicate, {stats['failed_sources']} source(s) failed"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
