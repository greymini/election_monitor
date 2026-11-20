"""Possible duplicates: when a report is filed or gets its category, earlier reports with the same
area and category from the 24 hours before it are stored as suggestions. Merging is a status change
(pulse/reports/triage.py); media and events stay on both reports."""
from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import UUID

import psycopg

WINDOW = timedelta(hours=24)
REASON = "same_area_category_24h"


def suggest(conn: psycopg.Connection, report_id: UUID) -> list[UUID]:
    r = conn.execute("SELECT * FROM field.report WHERE report_id = %s", (report_id,)).fetchone()
    if r is None or r["area_id"] is None or r["category_id"] is None or r["filed_at"] is None:
        return []
    rows = conn.execute(
        """INSERT INTO field.report_duplicate (report_id, suggested_id, reason)
           SELECT %(rid)s, x.report_id, %(reason)s FROM field.report x
           WHERE x.ac_id = %(ac)s AND x.area_id = %(area)s AND x.category_id = %(cat)s AND x.report_id <> %(rid)s
             AND x.status NOT IN ('draft', 'duplicate')
             AND x.filed_at BETWEEN %(since)s AND %(until)s
           ON CONFLICT DO NOTHING
           RETURNING suggested_id""",
        {"rid": report_id, "reason": REASON, "ac": r["ac_id"], "area": r["area_id"], "cat": r["category_id"],
         "since": r["filed_at"] - WINDOW, "until": r["filed_at"]},
    ).fetchall()
    return [x["suggested_id"] for x in rows]


def suggestions(conn: psycopg.Connection, report_id: UUID) -> list[dict[str, Any]]:
    """Suggested originals still open to merge into, oldest first."""
    return conn.execute(
        """SELECT x.report_id, x.report_no, x.status, d.reason FROM field.report_duplicate d
           JOIN field.report x ON x.report_id = d.suggested_id
           WHERE d.report_id = %s AND x.status <> 'duplicate' ORDER BY x.filed_at, x.report_no""",
        (report_id,),
    ).fetchall()


def canonical(conn: psycopg.Connection, report_id: UUID) -> UUID:
    """Follow duplicate_of to the report everything was merged into."""
    seen = set()
    current = report_id
    while current not in seen:
        seen.add(current)
        row = conn.execute("SELECT duplicate_of FROM field.report WHERE report_id = %s", (current,)).fetchone()
        if row is None or row["duplicate_of"] is None:
            return current
        current = row["duplicate_of"]
    return current
