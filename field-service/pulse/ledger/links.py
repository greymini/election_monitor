"""Spend linked to a report or an issue (slice 3), inside the caller's finance scope.

The total is computed in the same query as the scope (pulse/ledger/scope.py), over the same rows
as spend_total (pulse/metrics/spend.py spend_cte: non-void expenses). An issue's spend counts
expenses linked to it directly and expenses linked to any of its reports, at read time, so a report
linked to the issue later still counts. Finance scope none sees nothing (None).
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg

from pulse.ledger import scope
from pulse.metrics import spend as sm


def viewer_for(conn: psycopg.Connection, member_id: UUID) -> scope.Viewer | None:
    row = conn.execute(
        """SELECT member_id, ac_id, finance_scope, is_principal, can_give_advance, display_name
           FROM field.member WHERE member_id = %s AND active""", (member_id,)).fetchone()
    return scope.Viewer(**row) if row else None


REPORT_LINK = "t.report_id = %(l_id)s"
ISSUE_LINK = ("(t.issue_id = %(l_id)s OR t.report_id IN "
              "(SELECT lr.report_id FROM field.report lr WHERE lr.issue_id = %(l_id)s))")


def linked_spend(conn: psycopg.Connection, fv: scope.Viewer | None, *, report_id: UUID | None = None,
                 issue_id: UUID | None = None) -> dict[str, Any] | None:
    if fv is None or fv.finance_scope == "none":
        return None
    link, target = (REPORT_LINK, report_id) if report_id is not None else (ISSUE_LINK, issue_id)
    row = conn.execute(
        f"""WITH {sm.spend_cte(scope.txn_where(fv) + " AND " + link)}
            SELECT COALESCE(SUM(amount), 0) AS total, count(*) AS entries FROM spend""",
        {**scope.params(fv), "l_id": target}).fetchone()
    return {"total": row["total"], "entries": row["entries"], "scope": fv.finance_scope}
