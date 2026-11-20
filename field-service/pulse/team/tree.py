"""Subtree helpers over field.member.parent_member_id.

`is_in_subtree(root, m)` is true only for strict descendants: a member is not in their own subtree.
UNION (not UNION ALL) makes the recursion terminate even if bad data ever contained a cycle.
"""
from __future__ import annotations

from uuid import UUID

import psycopg

_SUBTREE_SQL = """
WITH RECURSIVE sub AS (
    SELECT member_id FROM field.member WHERE parent_member_id = %(root)s
    UNION
    SELECT m.member_id FROM field.member m JOIN sub ON m.parent_member_id = sub.member_id
)
"""


def is_in_subtree(conn: psycopg.Connection, root_member_id: UUID, member_id: UUID) -> bool:
    row = conn.execute(
        _SUBTREE_SQL + "SELECT EXISTS (SELECT 1 FROM sub WHERE member_id = %(m)s) AS hit",
        {"root": root_member_id, "m": member_id},
    ).fetchone()
    return bool(row["hit"])


def subtree_ids(conn: psycopg.Connection, root_member_id: UUID) -> set[UUID]:
    rows = conn.execute(_SUBTREE_SQL + "SELECT member_id FROM sub", {"root": root_member_id}).fetchall()
    return {r["member_id"] for r in rows}
