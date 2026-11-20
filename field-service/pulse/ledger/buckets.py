"""Read-only bucket lookups. A bucket visible to an AC is a shared preset (ac_id NULL) or the AC's own."""
from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg

OTHER_CODE = "OTHER"

_BUCKET_SELECT = """
SELECT b.bucket_id, b.code, b.name_hi, b.name_en, b.parent_id, b.active,
       t.bucket_id AS top_id, t.code AS top_code, t.name_hi AS top_name_hi,
       t.sensitive AS sensitive, t.ask_reach AS ask_reach,
       EXISTS (SELECT 1 FROM field.exp_bucket c WHERE c.parent_id = t.bucket_id AND c.active) AS top_has_subheads
FROM field.exp_bucket b
JOIN field.exp_bucket t ON t.bucket_id = COALESCE(b.parent_id, b.bucket_id)
"""


def get_bucket(conn: psycopg.Connection, bucket_id: int, ac_id: str) -> dict[str, Any] | None:
    """The bucket with its top-level bucket's code, name and flags; None if not visible or inactive."""
    return conn.execute(
        _BUCKET_SELECT + "WHERE b.bucket_id = %s AND b.active AND t.active AND (b.ac_id IS NULL OR b.ac_id = %s)",
        (bucket_id, ac_id),
    ).fetchone()


def bucket_by_code(conn: psycopg.Connection, code: str) -> dict[str, Any] | None:
    return conn.execute(_BUCKET_SELECT + "WHERE b.code = %s", (code,)).fetchone()


def top_buckets(conn: psycopg.Connection, ac_id: str) -> list[dict[str, Any]]:
    return conn.execute(
        """SELECT bucket_id, code, name_hi FROM field.exp_bucket
           WHERE parent_id IS NULL AND active AND (ac_id IS NULL OR ac_id = %s)
           ORDER BY sort, bucket_id""",
        (ac_id,),
    ).fetchall()


def subheads(conn: psycopg.Connection, top_id: int, ac_id: str) -> list[dict[str, Any]]:
    return conn.execute(
        """SELECT bucket_id, code, name_hi FROM field.exp_bucket
           WHERE parent_id = %s AND active AND (ac_id IS NULL OR ac_id = %s) ORDER BY sort, bucket_id""",
        (top_id, ac_id),
    ).fetchall()


def most_used(conn: psycopg.Connection, member_id: UUID, ac_id: str, n: int = 6) -> list[dict[str, Any]]:
    """The member's n most-used top-level buckets (excluding OTHER), filled up in preset order."""
    return conn.execute(
        """WITH usage AS (
               SELECT COALESCE(b.parent_id, b.bucket_id) AS top_id, count(*) AS uses, max(t.created_at) AS last_used
               FROM field.money_txn t JOIN field.exp_bucket b ON b.bucket_id = t.bucket_id
               WHERE t.kind = 'expense' AND t.status <> 'void' AND t.from_member_id = %(m)s
               GROUP BY 1)
           SELECT b.bucket_id, b.code, b.name_hi
           FROM field.exp_bucket b LEFT JOIN usage u ON u.top_id = b.bucket_id
           WHERE b.parent_id IS NULL AND b.active AND b.code <> %(other)s AND (b.ac_id IS NULL OR b.ac_id = %(ac)s)
           ORDER BY u.uses DESC NULLS LAST, u.last_used DESC NULLS LAST, b.sort, b.bucket_id
           LIMIT %(n)s""",
        {"m": member_id, "ac": ac_id, "other": OTHER_CODE, "n": n},
    ).fetchall()
