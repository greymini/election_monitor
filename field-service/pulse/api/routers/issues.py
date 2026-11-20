"""Issues API (slice 3): create, list, detail with timeline, rename, link and unlink reports, close.

Read: any team member who can see reports (restricted categories: principal and coordinators only).
Changes: the principal and coordinators (pulse/reports/issues.py). Linked spend is shown only within
the caller's finance scope, and not at all for finance scope none.
"""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from pulse.api import idempotency as idem
from pulse.api.deps import get_state
from pulse.api.routers.ledger import plain
from pulse.api.routers.reports import _error, report_viewer
from pulse.common.clock import utcnow
from pulse.db import transaction
from pulse.ledger import links
from pulse.reports import access, issues
from pulse.reports import service as reports

router = APIRouter(prefix="/acs/{ac_number}/field")


def _err(e: reports.ReportError) -> HTTPException:
    if isinstance(e, issues.Denied):
        return HTTPException(403, "not allowed")
    if isinstance(e, issues.IssueNotFound):
        return HTTPException(404, "issue not found")
    if isinstance(e, issues.IssueError):
        return HTTPException(409 if "closed" in str(e) or "linked" in str(e) else 422, str(e))
    return _error(e)


def _run(state, fn):
    try:
        with transaction(state.pool) as conn:
            return fn(conn)
    except reports.ReportError as e:
        raise _err(e) from None


class IssueCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, max_length=issues.TITLE_MAX)
    area_id: str | None = None
    category_id: int | None = None
    report_id: UUID | None = None          # start the issue from this report (its area and category)


class IssuePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=issues.TITLE_MAX)


class LinkBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    report_id: UUID


class LinkIssueBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    issue_id: UUID


class CloseBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str | None = Field(default=None, max_length=1000)


def _detail(conn, v: access.Viewer, issue_id: UUID) -> dict:
    issue = issues.get(conn, v, issue_id)
    out = {"issue": issue, "reports": issues.reports_of(conn, v, issue_id), "timeline": issues.timeline(conn, issue_id)}
    spend = links.linked_spend(conn, links.viewer_for(conn, v.member_id), issue_id=issue_id)
    if spend is not None:
        out["linked_spend"] = spend
    return out


@router.get("/issues")
def list_issues(status: Literal["open", "closed"] | None = None, area_id: str | None = None,
                limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                v: access.Viewer = Depends(report_viewer), state=Depends(get_state)):
    where, p = [issues.visible_where(v)], access.params(v)
    if status:
        where.append("i.status = %(f_status)s"); p["f_status"] = status
    if area_id:
        where.append("i.area_id = %(f_area)s"); p["f_area"] = area_id
    clause = " AND ".join(where)
    with transaction(state.pool) as conn:
        total = conn.execute(f"SELECT count(*) AS n FROM field.issue i WHERE {clause}", p).fetchone()["n"]
        rows = conn.execute(
            f"""SELECT i.*, (SELECT count(*) FROM field.report r WHERE r.issue_id = i.issue_id) AS report_count
                FROM field.issue i WHERE {clause} ORDER BY (i.status = 'open') DESC, i.created_at DESC
                LIMIT %(limit)s OFFSET %(offset)s""", {**p, "limit": limit, "offset": offset}).fetchall()
    return plain({"items": rows, "total": total, "limit": limit, "offset": offset})


@router.post("/issues", status_code=201)
def create_issue(body: IssueCreate, v: access.Viewer = Depends(report_viewer), state=Depends(get_state),
                 key: str | None = Depends(idem.key_header)):
    who = dict(user_id=str(v.member_id), route="POST /issues")

    def do(conn):
        replay = idem.claim(conn, key, body=body.model_dump(mode="json"), **who)
        if replay is not None:
            return replay
        row = issues.create(conn, state.core, v, now=utcnow(), title=body.title, area_id=body.area_id,
                            category_id=body.category_id, from_report_id=body.report_id)
        return idem.store(conn, key, status_code=201, response=plain(_detail(conn, v, row["issue_id"])), **who)
    return _run(state, do)


@router.get("/issues/{issue_id}")
def get_issue(issue_id: UUID, v: access.Viewer = Depends(report_viewer), state=Depends(get_state)):
    return plain(_run(state, lambda conn: _detail(conn, v, issue_id)))


@router.patch("/issues/{issue_id}")
def rename_issue(issue_id: UUID, body: IssuePatch, v: access.Viewer = Depends(report_viewer), state=Depends(get_state)):
    def do(conn):
        issues.rename(conn, v, issue_id, body.title, now=utcnow())
        return _detail(conn, v, issue_id)
    return plain(_run(state, do))


@router.post("/issues/{issue_id}/reports")
def link_report(issue_id: UUID, body: LinkBody, v: access.Viewer = Depends(report_viewer), state=Depends(get_state)):
    def do(conn):
        changed = issues.link(conn, v, issue_id, body.report_id, now=utcnow())
        return {**_detail(conn, v, issue_id), "changed": changed}
    return plain(_run(state, do))


@router.delete("/issues/{issue_id}/reports/{report_id}", status_code=204)
def unlink_report(issue_id: UUID, report_id: UUID, v: access.Viewer = Depends(report_viewer),
                  state=Depends(get_state)):
    _run(state, lambda conn: issues.unlink(conn, v, issue_id, report_id, now=utcnow()))
    return Response(status_code=204)


@router.post("/issues/{issue_id}/close")
def close_issue(issue_id: UUID, body: CloseBody, v: access.Viewer = Depends(report_viewer), state=Depends(get_state)):
    def do(conn):
        issues.close(conn, v, issue_id, now=utcnow(), note=body.note)
        return _detail(conn, v, issue_id)
    return plain(_run(state, do))


@router.post("/reports/{report_id}/link-issue")
def link_issue(report_id: UUID, body: LinkIssueBody, v: access.Viewer = Depends(report_viewer),
               state=Depends(get_state)):
    """LLD §9 route: the same as POST /issues/{issue_id}/reports."""
    def do(conn):
        changed = issues.link(conn, v, body.issue_id, report_id, now=utcnow())
        return {**_detail(conn, v, body.issue_id), "changed": changed}
    return plain(_run(state, do))
