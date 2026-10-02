"""Local embeddings for news and ground reports (LLD 0).

`intfloat/multilingual-e5-small` on CPU: about 120 MB, Hindi-capable, and free,
which is the whole point - an embedding API bill would dwarf the rest of the
running cost for this volume of text.

e5 models need the `query:` / `passage:` prefixes. Omitting them measurably
degrades retrieval, so both helpers are explicit about which is which.
"""

from __future__ import annotations

import argparse
import functools
import sys

from common.config import get_settings
from common.db import execute, query
from common.jobs import job_context
from common.logging_setup import get_logger

log = get_logger(__name__)

BATCH = 32


@functools.lru_cache(maxsize=1)
def get_model():
    from sentence_transformers import SentenceTransformer

    settings = get_settings()
    log.info("loading %s on %s", settings.embed_model, settings.embed_device)
    return SentenceTransformer(settings.embed_model, device=settings.embed_device)


def embed_passages(texts: list[str]) -> list[list[float]]:
    model = get_model()
    vectors = model.encode([f"passage: {t}" for t in texts],
                           normalize_embeddings=True, batch_size=BATCH,
                           show_progress_bar=False)
    return [v.tolist() for v in vectors]


def embed_query(text: str) -> list[float]:
    model = get_model()
    vector = model.encode(f"query: {text}", normalize_embeddings=True, show_progress_bar=False)
    return vector.tolist()


def embed_news(limit: int = 500) -> int:
    rows = query(
        "SELECT news_id, title, summary_hi, summary_en, body FROM news_item "
        "WHERE embedding IS NULL AND labelled_at IS NOT NULL "
        "ORDER BY news_id DESC LIMIT %s",
        (limit,),
    )
    if not rows:
        return 0
    texts = [
        " ".join(filter(None, [r["title"], r["summary_hi"], r["summary_en"],
                               (r["body"] or "")[:1000]]))
        for r in rows
    ]
    for i in range(0, len(rows), BATCH):
        chunk, vectors = rows[i: i + BATCH], embed_passages(texts[i: i + BATCH])
        for row, vector in zip(chunk, vectors, strict=True):
            execute("UPDATE news_item SET embedding = %s WHERE news_id = %s",
                    (str(vector), row["news_id"]))
    return len(rows)


def embed_ground_reports(limit: int = 500) -> int:
    rows = query(
        "SELECT report_id, text FROM ground_report WHERE embedding IS NULL "
        "ORDER BY report_id DESC LIMIT %s",
        (limit,),
    )
    if not rows:
        return 0
    for i in range(0, len(rows), BATCH):
        chunk = rows[i: i + BATCH]
        vectors = embed_passages([r["text"] for r in chunk])
        for row, vector in zip(chunk, vectors, strict=True):
            execute("UPDATE ground_report SET embedding = %s WHERE report_id = %s",
                    (str(vector), row["report_id"]))
    return len(rows)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Embed unembedded news and ground reports")
    ap.add_argument("--limit", type=int, default=500)
    args = ap.parse_args(argv)

    with job_context("news.embed") as job:
        news_n = embed_news(args.limit)
        ground_n = embed_ground_reports(args.limit)
        job.set(news=news_n, ground_reports=ground_n)
        job.log_line(f"embedded {news_n} article(s) and {ground_n} ground report(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
