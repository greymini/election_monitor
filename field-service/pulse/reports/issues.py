"""Issues (local stand-in for the core's public_issue / issue_event; see docs/MERGE.md).

An issue is the long-running thing ("water supply in panchayat A1"); a report is one observation of
it. An issue has a title, category, area, status (open | closed) and a timeline (field.issue_event).

Rules:
- the principal and coordinators open, rename, link, unlink and close issues;
- anyone who can see reports can read issues, except issues in a restricted category, which only the
  principal and coordinators see (as with reports);
- a report can be linked only to an *open* issue in the *same area*, and only while it has an area;
- report.issue_id is a plain uuid (no foreign key), set only here.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from pulse.core_adapter import CoreAdapter
from pulse.reports import access
from pulse.reports import service as reports

TITLE_MAX = 200
BOT_LIST = 5


class IssueError(reports.ReportError):
    pass


class IssueNotFound(reports.NotFound):
    pass


class Denied(reports.ReportError):
    pass


def may_manage(v: access.Viewer) -> bool:
    return v.sees_all


def visible_where(v: access.Viewer, alias: str = "i") -> str:
    """Bound parameters from access.params(v)."""
    base = f"{alias}.ac_id = %(rv_ac)s"
    if v.sees_all:
        return base
    return (f"{base} AND NOT EXISTS (SELECT 1 FROM field.report_category rc "
            f"WHERE rc.category_id = {alias}.category_id AND rc.restricted)")


def event(conn: psycopg.Connection, issue_id: UUID, kind: str, *, actor: UUID | None, now: datetime,
          report_id: UUID | None = None, note: str | None = None, data: dict | None = None) -> None:
    conn.execute(
        """INSERT INTO field.issue_event (issue_id, at, actor_member_id, kind, report_id, note, data)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        (issue_id, now, actor, kind, report_id, note, Jsonb(data) if data is not None else None))


def get(conn: psycopg.Connection, v: access.Viewer, issue_id: UUID, *, lock: bool = False) -> dict[str, Any]:
    row = conn.execute(f"SELECT i.* FROM field.issue i WHERE i.issue_id = %(iid)s AND {visible_where(v)}"
                       + (" FOR UPDATE" if lock else ""), {**access.params(v), "iid": issue_id}).fetchone()
    if row is None:
        raise IssueNotFound("issue not found")
    return row


def by_number(conn: psycopg.Connection, v: access.Viewer, n: int) -> dict[str, Any] | None:
    return conn.execute(
        f"SELECT i.* FROM field.issue i WHERE i.issue_no LIKE %(pat)s AND {visible_where(v)}",
        {**access.params(v), "pat": f"%-I-{n:06d}"}).fetchone()


def number(issue: dict[str, Any]) -> int:
    return int(issue["issue_no"].rsplit("-", 1)[1])


def _next_no(conn: psycopg.Connection, core: CoreAdapter, ac_id: str) -> str:
    ac = next((a for a in core.list_acs() if a.ac_id == ac_id), None)
    if ac is None:
        raise IssueError("unknown AC")
    n = conn.execute(
        """INSERT INTO field.issue_counter (ac_id, last_no) VALUES (%s, 1)
           ON CONFLICT (ac_id) DO UPDATE SET last_no = field.issue_counter.last_no + 1 RETURNING last_no""",
        (ac_id,)).fetchone()["last_no"]
    return f"{ac.ac_number}-I-{n:06d}"


def _area(core: CoreAdapter, ac_id: str, area_id: str):
    return next((a for a in core.list_areas(ac_id) if a.area_id == area_id), None)


def default_title(conn: psycopg.Connection, core: CoreAdapter, ac_id: str, area_id: str,
                  category_id: int | None) -> str:
    area = _area(core, ac_id, area_id)
    cat = conn.execute("SELECT name_hi FROM field.report_category WHERE category_id = %s",
                       (category_id,)).fetchone() if category_id else None
    parts = [cat["name_hi"] if cat else None, area.name_hi if area else area_id]
    return " — ".join(p for p in parts if p)[:TITLE_MAX]


def _clean_title(title: str | None) -> str | None:
    t = " ".join((title or "").split())
    if len(t) > TITLE_MAX:
        raise IssueError(f"title is longer than {TITLE_MAX} characters")
    reports.screen_typed("title", t)
    return t or None


def create(conn: psycopg.Connection, core: CoreAdapter, v: access.Viewer, *, now: datetime,
           title: str | None = None, area_id: str | None = None, category_id: int | None = None,
           from_report_id: UUID | None = None) -> dict[str, Any]:
    """Open an issue. From a report: its area and category, and the report is linked at once."""
    if not may_manage(v):
        raise Denied("open issue")
    report = None
    if from_report_id is not None:
        report = reports.get(conn, from_report_id, lock=True)
        if report is None or not access.can_see(conn, v, from_report_id):
            raise reports.NotFound("report not found")
        if report["area_id"] is None:
            raise IssueError("the report has no area; set its area first")
        area_id = area_id or report["area_id"]
        category_id = category_id if category_id is not None else report["category_id"]
    if not area_id:
        raise IssueError("area_id is required")
    area = _area(core, v.ac_id, area_id)
    if area is None:
        raise IssueError("unknown area for this AC")
    if category_id is not None and conn.execute(
            "SELECT 1 FROM field.report_category WHERE category_id = %s AND active", (category_id,)).fetchone() is None:
        raise IssueError("unknown category")
    title = _clean_title(title) or default_title(conn, core, v.ac_id, area_id, category_id)
    row = conn.execute(
        """INSERT INTO field.issue (issue_no, ac_id, title, category_id, block_id, area_id, opened_by, created_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING *""",
        (_next_no(conn, core, v.ac_id), v.ac_id, title, category_id, area.block_id, area_id, v.member_id, now),
    ).fetchone()
    event(conn, row["issue_id"], "opened", actor=v.member_id, now=now, data={"title": title})
    if report is not None:
        link(conn, v, row["issue_id"], report["report_id"], now=now)
    return row


def rename(conn: psycopg.Connection, v: access.Viewer, issue_id: UUID, title: str, *, now: datetime) -> dict[str, Any]:
    if not may_manage(v):
        raise Denied("rename issue")
    issue = get(conn, v, issue_id, lock=True)
    new = _clean_title(title)
    if new is None:
        raise IssueError("title is empty")
    row = conn.execute("UPDATE field.issue SET title = %s WHERE issue_id = %s RETURNING *", (new, issue_id)).fetchone()
    event(conn, issue_id, "renamed", actor=v.member_id, now=now, data={"old": issue["title"], "new": new})
    return row


def link(conn: psycopg.Connection, v: access.Viewer, issue_id: UUID, report_id: UUID, *, now: datetime,
         except_alert: int | None = None) -> bool:
    """Attach a report to an open issue in the same area. False when it was already attached."""
    if not may_manage(v):
        raise Denied("link report")
    issue = get(conn, v, issue_id, lock=True)
    report = reports.get(conn, report_id, lock=True)
    if report is None or not access.can_see(conn, v, report_id):
        raise reports.NotFound("report not found")
    if issue["status"] != "open":
        raise IssueError("the issue is closed")
    if report["area_id"] is None:
        raise IssueError("the report has no area; set its area first")
    if report["area_id"] != issue["area_id"]:
        raise IssueError("the report is in another area than the issue")
    if report["issue_id"] == issue_id:
        return False
    if report["issue_id"] is not None:
        raise IssueError("the report is linked to another issue; unlink it first")
    conn.execute("UPDATE field.report SET issue_id = %s WHERE report_id = %s", (issue_id, report_id))
    event(conn, issue_id, "report_linked", actor=v.member_id, now=now, report_id=report_id,
          data={"report_no": report["report_no"]})
    reports.event(conn, report_id, "issue_linked", actor=v.member_id, now=now,
                  data={"issue_id": str(issue_id), "issue_no": issue["issue_no"]})
    _redraw(conn, report, except_alert)
    return True


def unlink(conn: psycopg.Connection, v: access.Viewer, issue_id: UUID, report_id: UUID, *, now: datetime) -> bool:
    if not may_manage(v):
        raise Denied("unlink report")
    issue = get(conn, v, issue_id, lock=True)
    report = reports.get(conn, report_id, lock=True)
    if report is None or report["issue_id"] != issue_id:
        raise reports.NotFound("report not linked to this issue")
    conn.execute("UPDATE field.report SET issue_id = NULL WHERE report_id = %s", (report_id,))
    event(conn, issue_id, "report_unlinked", actor=v.member_id, now=now, report_id=report_id,
          data={"report_no": report["report_no"]})
    reports.event(conn, report_id, "issue_unlinked", actor=v.member_id, now=now,
                  data={"issue_id": str(issue_id), "issue_no": issue["issue_no"]})
    _redraw(conn, report, None)
    return True


def close(conn: psycopg.Connection, v: access.Viewer, issue_id: UUID, *, now: datetime,
          note: str | None = None) -> dict[str, Any]:
    if not may_manage(v):
        raise Denied("close issue")
    issue = get(conn, v, issue_id, lock=True)
    if issue["status"] == "closed":
        raise IssueError("the issue is already closed")
    note = (note or "").strip()[:1000] or None
    reports.screen_typed("note", note)
    row = conn.execute(
        """UPDATE field.issue SET status = 'closed', closed_at = %s, closed_by = %s, close_note = %s
           WHERE issue_id = %s RETURNING *""", (now, v.member_id, note, issue_id)).fetchone()
    event(conn, issue_id, "closed", actor=v.member_id, now=now, note=note)
    return row


def open_in_area(conn: psycopg.Connection, v: access.Viewer, area_id: str, limit: int = BOT_LIST) -> list[dict[str, Any]]:
    return conn.execute(
        f"""SELECT i.* FROM field.issue i WHERE {visible_where(v)} AND i.status = 'open' AND i.area_id = %(area)s
            ORDER BY i.created_at DESC LIMIT %(n)s""", {**access.params(v), "area": area_id, "n": limit}).fetchall()


def reports_of(conn: psycopg.Connection, v: access.Viewer, issue_id: UUID) -> list[dict[str, Any]]:
    """Linked reports the viewer can see."""
    return conn.execute(
        f"""SELECT r.report_id, r.report_no, r.status, r.urgency, r.filed_at, r.category_id FROM field.report r
            WHERE r.issue_id = %(iid)s AND {access.visible_where(v)} ORDER BY r.filed_at""",
        {**access.params(v), "iid": issue_id}).fetchall()


def timeline(conn: psycopg.Connection, issue_id: UUID) -> list[dict[str, Any]]:
    return conn.execute(
        """SELECT e.at, e.kind, e.report_id, e.note, e.data, e.actor_member_id, m.display_name AS actor_name
           FROM field.issue_event e LEFT JOIN field.member m ON m.member_id = e.actor_member_id
           WHERE e.issue_id = %s ORDER BY e.event_id""", (issue_id,)).fetchall()


def _redraw(conn, report: dict[str, Any], except_alert: int | None) -> None:
    if report["status"] != "draft":
        from pulse.notify import outbox
        outbox.refresh_report_cards(conn, report["report_id"], except_alert=except_alert)
