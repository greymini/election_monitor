"""The report status lifecycle (LLD §5.3): the one place a report's status changes after filing.

    new            → acknowledged · assigned (records the acknowledgement too) · duplicate
    acknowledged   → assigned · resolved · closed_no_action · duplicate
    assigned       → in_progress · assigned (reassign) · resolved · closed_no_action · duplicate
    in_progress    → resolved · closed_no_action · assigned (reassign)
    resolved, closed_no_action, duplicate → acknowledged (reopen)

Who may make a change is decided by pulse/reports/access.py and checked by pulse/reports/triage.py;
this module only knows which changes exist. Every change writes a report_event.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

import psycopg

from pulse.reports import service as reports

OPEN = ("new", "acknowledged", "assigned", "in_progress")
CLOSED = ("resolved", "closed_no_action", "duplicate")
TRANSITIONS: dict[str, frozenset[str]] = {
    "new": frozenset({"acknowledged", "assigned", "duplicate"}),
    "acknowledged": frozenset({"assigned", "resolved", "closed_no_action", "duplicate"}),
    "assigned": frozenset({"in_progress", "assigned", "resolved", "closed_no_action", "duplicate"}),
    "in_progress": frozenset({"resolved", "closed_no_action", "assigned"}),
    "resolved": frozenset({"acknowledged"}),
    "closed_no_action": frozenset({"acknowledged"}),
    "duplicate": frozenset({"acknowledged"}),
}


class TransitionError(reports.ReportError):
    def __init__(self, frm: str, to: str):
        super().__init__(f"{frm} → {to} is not allowed")
        self.frm, self.to = frm, to


def allowed(frm: str, to: str) -> bool:
    return to in TRANSITIONS.get(frm, frozenset())


def change(conn: psycopg.Connection, report_id: UUID, to: str, *, actor: UUID | None, now: datetime,
           note: str | None = None, assignee: UUID | None = None, duplicate_of: UUID | None = None) -> dict[str, Any]:
    r = reports.get(conn, report_id, lock=True)
    frm = r["status"]
    if not allowed(frm, to):
        raise TransitionError(frm, to)
    reopen = frm in CLOSED and to == "acknowledged"
    sets: dict[str, Any] = {"status": to, "last_activity_at": now}
    implicit_ack = False
    if to == "acknowledged" and not reopen:
        sets.update(ack_by=actor, ack_at=now)
    elif reopen:
        sets.update(resolved_at=None, resolution_note=None, duplicate_of=None)
    elif to == "assigned":
        if assignee is None:
            raise reports.ReportError("assigning needs an assignee")
        sets["assigned_to"] = assignee
        if r["ack_at"] is None:                         # assigning straight from new also acknowledges
            sets.update(ack_by=actor, ack_at=now)
            implicit_ack = True
    elif to in ("resolved", "closed_no_action"):
        sets.update(resolved_at=now, resolution_note=(note or "").strip() or None)
    elif to == "duplicate":
        if duplicate_of is None:
            raise reports.ReportError("a duplicate needs the report it duplicates")
        sets["duplicate_of"] = duplicate_of
    cols = ", ".join(f"{k} = %({k})s" for k in sets)
    row = conn.execute(f"UPDATE field.report SET {cols} WHERE report_id = %(rid)s RETURNING *",
                       {**sets, "rid": report_id}).fetchone()
    reports.event(conn, report_id, "status_changed", actor=actor, now=now, note=sets.get("resolution_note"),
                  data={"from": frm, "to": to, "reopen": reopen, "implicit_ack": implicit_ack,
                        "assignee": str(assignee) if assignee else None,
                        "duplicate_of": str(duplicate_of) if duplicate_of else None})
    return row
