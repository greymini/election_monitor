"""Which unacknowledged advances and returns need a reminder or a flag (LLD §5.2: 24 h, then 48 h).

Plain queries; the writes go through pulse/ledger/service.py. Run once by scripts/run_jobs.py;
the worker (slice 2b) will schedule them.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import psycopg

from pulse.config import LedgerLimits

_PENDING = """
SELECT t.txn_id, t.txn_no, t.kind, t.amount, t.created_at, t.from_member_id, t.to_member_id, t.remark,
       g.display_name AS giver_name
FROM field.money_txn t JOIN field.member g ON g.member_id = t.from_member_id
WHERE t.kind IN ('advance', 'return') AND t.status = 'pending_ack' AND t.created_at <= %(cutoff)s
"""


_AC = " AND (%(ac)s::text IS NULL OR t.ac_id = %(ac)s)"


def advances_needing_reminder(conn: psycopg.Connection, now: datetime, limits: LedgerLimits,
                              ac_id: str | None = None) -> list[dict[str, Any]]:
    """Pending for at least ack_remind_hours and not reminded yet (one reminder each)."""
    return conn.execute(
        _PENDING + _AC + """ AND NOT EXISTS (SELECT 1 FROM field.txn_audit a WHERE a.txn_id = t.txn_id
                                             AND a.action = 'remind') ORDER BY t.created_at""",
        {"cutoff": now - timedelta(hours=limits.ack_remind_hours), "ac": ac_id},
    ).fetchall()


def advances_to_flag(conn: psycopg.Connection, now: datetime, limits: LedgerLimits,
                     ac_id: str | None = None) -> list[dict[str, Any]]:
    """Pending for at least ack_flag_hours and not flagged yet."""
    return conn.execute(
        _PENDING + _AC + " AND NOT ('ack_overdue' = ANY (t.flagged)) ORDER BY t.created_at",
        {"cutoff": now - timedelta(hours=limits.ack_flag_hours), "ac": ac_id},
    ).fetchall()
