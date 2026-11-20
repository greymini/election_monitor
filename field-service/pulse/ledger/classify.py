"""Bucket suggestion from the typed description (LLD §5.1).

Normalise the text, score every top-level bucket by the summed weights of its keywords found in
the text (exact token or contiguous phrase, each keyword counted once), then suggest the top
bucket only if its score clears THRESHOLD and leads the second by MARGIN. Otherwise: no suggestion,
and the person picks. The result is labelled machine output (suggest_source = 'rule').
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, Mapping

import psycopg

from pulse.common.text import tokens

THRESHOLD = Decimal("1.0")
MARGIN = Decimal("0.5")
_CONF_Q = Decimal("0.001")


@dataclass(frozen=True)
class Suggestion:
    bucket_id: int
    score: Decimal
    conf: Decimal       # top score / sum of all bucket scores, 0..1


def _contains(haystack: list[str], needle: list[str]) -> bool:
    n = len(needle)
    return any(haystack[i:i + n] == needle for i in range(len(haystack) - n + 1))


def score(text: str, keywords: Iterable[tuple[str, int, Decimal]]) -> dict[int, Decimal]:
    """keywords: (normalised keyword, bucket_id, weight). Returns {bucket_id: score} for matches only."""
    toks = tokens(text)
    scores: dict[int, Decimal] = {}
    for kw, bucket_id, weight in keywords:
        if _contains(toks, kw.split()):
            scores[bucket_id] = scores.get(bucket_id, Decimal(0)) + Decimal(weight)
    return scores


def decide(scores: Mapping[int, Decimal], *, threshold: Decimal = THRESHOLD, margin: Decimal = MARGIN) -> Suggestion | None:
    if not scores:
        return None
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    top_id, top = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else Decimal(0)
    if top < threshold or top - second < margin:
        return None
    conf = (top / sum(scores.values())).quantize(_CONF_Q)
    return Suggestion(bucket_id=top_id, score=top, conf=conf)


def load_keywords(conn: psycopg.Connection, ac_id: str) -> list[tuple[str, int, Decimal]]:
    rows = conn.execute(
        """SELECT k.keyword, k.bucket_id, k.weight FROM field.exp_keyword k
           JOIN field.exp_bucket b ON b.bucket_id = k.bucket_id
           WHERE b.active AND b.parent_id IS NULL AND (b.ac_id IS NULL OR b.ac_id = %s)""",
        (ac_id,),
    ).fetchall()
    return [(r["keyword"], r["bucket_id"], r["weight"]) for r in rows]


def suggest(conn: psycopg.Connection, ac_id: str, text: str) -> Suggestion | None:
    if not text or not text.strip():
        return None
    return decide(score(text, load_keywords(conn, ac_id)))
