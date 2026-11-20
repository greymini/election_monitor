"""Category suggestion and urgency hint for a report, from its typed notes and captions.

Same scorer and decision rule as expense buckets (pulse/ledger/classify.py): suggest only when the
top category clears the threshold and leads the second by the margin. Machine output: a suggestion
is shown first and labelled; it never sets the category by itself.
"""
from __future__ import annotations

from decimal import Decimal

import psycopg

from pulse.ledger.classify import Suggestion, decide, score


def _keywords(conn: psycopg.Connection, urgent_only: bool = False) -> list[tuple[str, int, Decimal]]:
    rows = conn.execute(
        """SELECT k.keyword, k.category_id, k.weight FROM field.report_keyword k
           JOIN field.report_category c ON c.category_id = k.category_id
           WHERE c.active AND (NOT %s OR k.urgency_hint = 'urgent')""",
        (urgent_only,),
    ).fetchall()
    return [(r["keyword"], r["category_id"], r["weight"]) for r in rows]


def suggest_category(conn: psycopg.Connection, text: str | None) -> Suggestion | None:
    if not text or not text.strip():
        return None
    return decide(score(text, _keywords(conn)))


def urgency_hint(conn: psycopg.Connection, text: str | None) -> bool:
    """True when any keyword marked as an urgency hint appears in the text."""
    if not text or not text.strip():
        return False
    return bool(score(text, _keywords(conn, urgent_only=True)))
