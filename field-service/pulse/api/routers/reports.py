"""Reports API (LLD §9): inbox, detail, signed media, web entry, triage actions, summary.

Authorisation: core token → active member row in this AC. Visibility is applied inside every query
(pulse/reports/access.py); who may change what is decided in pulse/reports/triage.py.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from pulse.api import idempotency as idem
from pulse.api import media_tokens
from pulse.api.deps import current_user, get_state
from pulse.api.routers.ledger import plain
from pulse.common.clock import utcnow
from pulse.core_adapter import CoreUser, ac_by_number
from pulse.db import transaction
from pulse.metrics import reports as rm
from pulse.metrics import spend as sm
from pulse.bot import texts_hi as T
from pulse.ledger import links
from pulse.reports import title as report_title
from pulse.reports import access, duplicates, issues, lifecycle, transcripts, triage
from pulse.reports import service as reports

router = APIRouter(prefix="/acs/{ac_number}/field")

Status = Literal["new", "acknowledged", "assigned", "in_progress", "resolved", "closed_no_action", "duplicate"]
_DAY = f"((r.filed_at AT TIME ZONE {sm.IST_SQL})::date)"


def report_viewer(ac_number: int, user: CoreUser = Depends(current_user), state=Depends(get_state)) -> access.Viewer:
    ac = ac_by_number(state.core, ac_number)
    if ac is None:
        raise HTTPException(404, "unknown AC")
    if ac.ac_id not in user.ac_ids:
        raise HTTPException(403, "no access to this AC")
    with state.pool.connection() as conn:
        row = conn.execute(
            """SELECT member_id, ac_id, is_principal, is_coordinator, display_name FROM field.member
               WHERE user_id = %s AND ac_id = %s AND active""", (user.user_id, ac.ac_id)).fetchone()
    if row is None:
        raise HTTPException(403, "not a team member in this AC")
    return access.Viewer(**row)


def _error(e: reports.ReportError) -> HTTPException:
    if isinstance(e, (triage.Denied, transcripts.Denied)):
        return HTTPException(403, "not allowed")
    if isinstance(e, lifecycle.TransitionError):
        return HTTPException(409, str(e))
    if isinstance(e, reports.NotFound):
        return HTTPException(404, "report not found")
    return HTTPException(422, str(e))


_LIST = """SELECT r.report_id, r.report_no, r.title, r.title_source, r.status, r.urgency, r.restricted, r.category_id, c.code AS category_code,
       c.name_hi AS category_name_hi, r.block_id, r.area_id, r.reporter_member_id, m.display_name AS reporter_name,
       r.assigned_to, r.filed_at, r.ack_at, r.resolved_at, r.escalation_level, r.note_text, r.loc_source,
       r.lat, r.lon, r.issue_id, r.transcript_status,
       (SELECT a.asset_id FROM field.report_media rm JOIN field.media_asset a ON a.asset_id = rm.asset_id
        WHERE rm.report_id = r.report_id AND rm.role = 'report' AND a.status = 'stored'
          AND (a.kind = 'photo' OR a.mime LIKE 'image/%%') ORDER BY rm.seq LIMIT 1) AS thumb_asset_id,
       (SELECT count(*) FROM field.report_media rm JOIN field.media_asset a ON a.asset_id = rm.asset_id
        WHERE rm.report_id = r.report_id AND a.kind = 'voice') AS voice_count
FROM field.report r
LEFT JOIN field.report_category c ON c.category_id = r.category_id
JOIN field.member m ON m.member_id = r.reporter_member_id"""


@router.get("/reports")
def list_reports(ac_number: int, status: Status | None = None, category_id: int | None = None,
                 urgency: Literal["normal", "urgent"] | None = None, block_id: str | None = None,
                 area_id: str | None = None, reporter_member_id: UUID | None = None, date_from: date | None = None,
                 date_to: date | None = None, unsorted: bool = False, open: bool = False,
                 located: bool = False, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                 v: access.Viewer = Depends(report_viewer), state=Depends(get_state)):
    """The inbox: urgent first, then unsorted, then oldest. `open` keeps new, acknowledged, assigned and
    in-progress reports; `located` keeps those with a shared location (the map). Each row carries a
    signed 5-minute link to its first photo."""
    where = [access.visible_where(v)]
    p = access.params(v)
    if open:
        where.append("r.status = ANY (%(open_statuses)s)"); p["open_statuses"] = list(lifecycle.OPEN)
    if located:
        where.append("r.lat IS NOT NULL AND r.lon IS NOT NULL")
    for col, val in (("status", status), ("category_id", category_id), ("urgency", urgency), ("block_id", block_id),
                     ("area_id", area_id), ("reporter_member_id", reporter_member_id)):
        if val is not None:
            where.append(f"r.{col} = %(f_{col})s")
            p[f"f_{col}"] = val
    if date_from:
        where.append(f"{_DAY} >= %(f_from)s"); p["f_from"] = date_from
    if date_to:
        where.append(f"{_DAY} <= %(f_to)s"); p["f_to"] = date_to
    if unsorted:
        where.append("r.category_id IS NULL")
    clause = " AND ".join(where)
    with transaction(state.pool) as conn:
        total = conn.execute(f"SELECT count(*) AS n FROM field.report r WHERE {clause}", p).fetchone()["n"]
        rows = conn.execute(
            f"""{_LIST} WHERE {clause}
                ORDER BY (r.urgency = 'urgent') DESC, (r.category_id IS NULL) DESC, r.filed_at, r.report_no
                LIMIT %(limit)s OFFSET %(offset)s""", {**p, "limit": limit, "offset": offset}).fetchall()
        unlocated = conn.execute(
            f"SELECT count(*) AS n FROM field.report r WHERE {' AND '.join(w for w in where if 'r.lat' not in w)} "
            f"AND (r.lat IS NULL OR r.lon IS NULL)", p).fetchone()["n"] if located else None
    now = utcnow()
    items = []
    for r in rows:
        r = dict(r)
        r["thumb_url"] = None
        if r["thumb_asset_id"] is not None:
            token = media_tokens.issue(state.settings.jwt_secret, v.member_id, r["report_id"], r["thumb_asset_id"], now=now)
            r["thumb_url"] = f"/acs/{ac_number}/field/reports/{r['report_id']}/media/{r['thumb_asset_id']}?token={token}"
        items.append(r)
    out = {"items": items, "total": total, "limit": limit, "offset": offset}
    if located:
        out["without_location"] = unlocated
    return plain(out)


@router.get("/reports/summary")
def summary(date_from: date | None = None, date_to: date | None = None,
            v: access.Viewer = Depends(report_viewer), state=Depends(get_state)):
    where = [access.visible_where(v)]
    p = access.params(v)
    if date_from:
        where.append(f"{_DAY} >= %(f_from)s"); p["f_from"] = date_from
    if date_to:
        where.append(f"{_DAY} <= %(f_to)s"); p["f_to"] = date_to
    clause = " AND ".join(where)

    def counts(expr: str, label: str):
        return conn.execute(f"SELECT {expr} AS {label}, count(*) AS n FROM field.report r WHERE {clause} "
                            f"GROUP BY 1 ORDER BY 2 DESC, 1 NULLS LAST", p).fetchall()

    with transaction(state.pool) as conn:
        out = {
            "by_area": counts("r.area_id", "area_id"),
            "by_category": counts("r.category_id", "category_id"),
            "by_status": counts("r.status", "status"),
            "by_week": counts(f"date_trunc('week', {_DAY})::date", "week"),
            "by_week_category": conn.execute(
                f"""SELECT date_trunc('week', {_DAY})::date AS week, r.category_id, count(*) AS n
                    FROM field.report r WHERE {clause} GROUP BY 1, 2 ORDER BY 1, 2 NULLS LAST""", p).fetchall(),
            "open": conn.execute(
                f"""SELECT count(*) FILTER (WHERE r.urgency = 'urgent') AS urgent,
                           count(*) FILTER (WHERE r.category_id IS NULL) AS unsorted, count(*) AS total
                    FROM field.report r WHERE {clause} AND r.status = ANY (%(open_statuses)s)""",
                {**p, "open_statuses": list(lifecycle.OPEN)}).fetchone(),
            "medians": conn.execute(rm.medians_sql(clause), p).fetchone(),
            "categories": conn.execute(
                "SELECT category_id, code, name_hi, name_en, restricted FROM field.report_category "
                "WHERE active ORDER BY sort, category_id").fetchall(),
        }
    names = {a.area_id: a for a in state.core.list_areas(v.ac_id)}
    out["by_area"] = [dict(r, name_hi=names[r["area_id"]].name_hi if r["area_id"] in names else None,
                           name_en=names[r["area_id"]].name_en if r["area_id"] in names else None)
                      for r in out["by_area"]]
    return plain(out)


def _visible(conn, v: access.Viewer, report_id: UUID) -> dict:
    if not access.can_see(conn, v, report_id):
        raise HTTPException(404, "report not found")
    return reports.get(conn, report_id)


@router.get("/reports/{report_id}")
def get_report(ac_number: int, report_id: UUID, v: access.Viewer = Depends(report_viewer), state=Depends(get_state)):
    secret = state.settings.jwt_secret
    with transaction(state.pool) as conn:
        r = _visible(conn, v, report_id)
        media_rows = conn.execute(
            """SELECT m.seq, m.role, a.asset_id, a.kind, a.tg_kind, a.mime, a.status, a.bytes, a.duration_s
               FROM field.report_media m JOIN field.media_asset a ON a.asset_id = m.asset_id
               WHERE m.report_id = %s ORDER BY m.seq""", (report_id,)).fetchall()
        events = conn.execute(
            """SELECT e.at, e.kind, e.note, e.data, e.actor_member_id, m.display_name AS actor_name
               FROM field.report_event e LEFT JOIN field.member m ON m.member_id = e.actor_member_id
               WHERE e.report_id = %s ORDER BY e.event_id""", (report_id,)).fetchall()
        dups = [d for d in duplicates.suggestions(conn, report_id) if access.can_see(conn, v, d["report_id"])]
        parts = conn.execute(
            """SELECT t.asset_id, t.status, t.text, t.confidence, t.engine, t.converted, t.done_at
               FROM field.report_transcript t JOIN field.report_media m
                 ON m.report_id = t.report_id AND m.asset_id = t.asset_id
               WHERE t.report_id = %s ORDER BY m.seq""", (report_id,)).fetchall()
        spend = links.linked_spend(conn, links.viewer_for(conn, v.member_id), report_id=report_id)
        issue = None
        if r["issue_id"] is not None:
            issue = conn.execute(
                f"SELECT i.issue_id, i.issue_no, i.title, i.status FROM field.issue i "
                f"WHERE i.issue_id = %(iid)s AND {issues.visible_where(v)}",
                {**access.params(v), "iid": r["issue_id"]}).fetchone()
    now = utcnow()
    items = []
    for m in media_rows:
        item = dict(m)
        if m["status"] == "stored":
            token = media_tokens.issue(secret, v.member_id, report_id, m["asset_id"], now=now)
            item["url"] = f"/acs/{ac_number}/field/reports/{report_id}/media/{m['asset_id']}?token={token}"
        items.append(item)
    shown = transcripts.display(r)
    transcript = {"status": r["transcript_status"], "text": shown.text, "source": shown.source,
                  "label": T.MACHINE_WRITTEN if shown.source == "machine" else None,
                  "engine": shown.engine, "confidence": shown.confidence,
                  "machine_text": r["transcript_hi"], "human_text": r["transcript_human"],
                  "human_at": r["transcript_human_at"], "parts": parts}
    suggestion = ({"category_id": r["suggested_category_id"], "urgency": r["suggested_urgency"],
                   "source": r["suggested_source"]} if r["suggested_source"] else None)
    with transaction(state.pool) as conn:
        can = {"triage": access.may_triage(conn, v, r), "work": access.may_work(v, r),
               "reopen": access.may_reopen(v, r), "correct": transcripts.may_correct(v, r),
               "manage_issues": issues.may_manage(v)}
    out = {"report": r, "media": items, "events": events, "duplicate_suggestions": dups,
           "transcript": transcript, "suggestion": suggestion, "issue": issue, "can": can}
    if spend is not None:                    # finance scope none: no money shown at all
        out["linked_spend"] = spend
    return plain(out)


@router.get("/reports/{report_id}/assignable")
def assignable(report_id: UUID, v: access.Viewer = Depends(report_viewer), state=Depends(get_state)):
    """Who this report can be assigned to: members covering its area first (restricted reports only to
    the principal or a coordinator). Empty unless the viewer may triage it."""
    with transaction(state.pool) as conn:
        r = _visible(conn, v, report_id)
        if not access.may_triage(conn, v, r):
            return {"members": []}
        return plain({"members": triage.assignable(conn, v, r)})


@router.get("/reports/{report_id}/media/{asset_id}")
def get_media(ac_number: int, report_id: UUID, asset_id: UUID, token: str, state=Depends(get_state)):
    """Streamed through the API with a signed 5-minute link; no bearer header (images and audio players
    cannot send one). A view by anyone but the reporter is audited."""
    try:
        member_id = media_tokens.verify(state.settings.jwt_secret, token, report_id, asset_id)
    except media_tokens.BadToken:
        raise HTTPException(403, "link invalid or expired") from None
    with transaction(state.pool) as conn:
        v = access.viewer_for(conn, member_id)
        ac = ac_by_number(state.core, ac_number)
        if v is None or ac is None or v.ac_id != ac.ac_id or not access.can_see(conn, v, report_id):
            raise HTTPException(404, "not found")
        a = conn.execute(
            """SELECT a.* FROM field.report_media m JOIN field.media_asset a ON a.asset_id = m.asset_id
               WHERE m.report_id = %s AND m.asset_id = %s""", (report_id, asset_id)).fetchone()
        if a is None:
            raise HTTPException(404, "not found")
        if a["status"] == "purged":
            raise HTTPException(410, "deleted after the retention period")
        if a["status"] != "stored":
            raise HTTPException(404, "not found")
        r = reports.get(conn, report_id)
        if r["reporter_member_id"] != member_id:
            reports.event(conn, report_id, "media_viewed", actor=member_id, now=utcnow(), data={"asset_id": str(asset_id)})
    data = state.storage.get(a["storage_key"])
    return Response(data, media_type=a["mime"] or "application/octet-stream",
                    headers={"Cache-Control": "private, no-store", "Content-Disposition": "inline",
                             "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff"})


class ReportCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note_text: str = Field(min_length=1, max_length=10_000)
    category_id: int | None = None
    urgency: Literal["normal", "urgent"] = "normal"
    block_id: str | None = None
    area_id: str | None = None
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)
    occurred_at: datetime | None = None


@router.post("/reports", status_code=201)
def create_report(body: ReportCreate, v: access.Viewer = Depends(report_viewer), state=Depends(get_state),
                  key: str | None = Depends(idem.key_header)):
    now = utcnow()
    who = dict(user_id=str(v.member_id), route="POST /reports")
    try:
        with transaction(state.pool) as conn:
            replay = idem.claim(conn, key, body=body.model_dump(mode="json"), **who)
            if replay is not None:
                return replay
            member = conn.execute("SELECT * FROM field.member WHERE member_id = %s", (v.member_id,)).fetchone()
            r = reports.open_draft(conn, state.core, member, now=now, created_via="web")
            rid = r["report_id"]
            reports.add_note(conn, rid, body.note_text, actor=v.member_id, now=now)
            if body.block_id or body.area_id:
                reports.set_area(conn, state.core, rid, body.block_id, body.area_id, "chosen", actor=v.member_id, now=now)
            if body.lat is not None and body.lon is not None:
                reports.set_location(conn, rid, body.lat, body.lon, actor=v.member_id, now=now)
            if body.category_id is not None:
                reports.set_category(conn, rid, body.category_id, actor=v.member_id, now=now)
            reports.set_urgency(conn, rid, body.urgency, source="user", actor=v.member_id, now=now)
            if body.occurred_at is not None:
                reports.set_occurred_at(conn, rid, body.occurred_at, actor=v.member_id, now=now)
            r = reports.file(conn, state.core, rid, how="web", actor=v.member_id, now=now)
            return idem.store(conn, key, status_code=201, response=plain({"report": r}), **who)
    except reports.ReportError as e:
        raise _error(e) from None


class AssignBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    member_id: UUID


class StatusBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["acknowledged", "in_progress", "resolved", "closed_no_action"]
    note: str | None = Field(default=None, max_length=1000)


class MergeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    into_report_id: UUID


class CategoryBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category_id: int


class UrgencyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    urgency: Literal["normal", "urgent"]


def _act(state, fn):
    try:
        with transaction(state.pool) as conn:
            done = fn(conn)
    except reports.ReportError as e:
        raise _error(e) from None
    return plain({"report": done.report, "changed": done.changed})


class TranscriptBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=transcripts.HUMAN_MAX)


@router.post("/reports/{report_id}/transcript")
def correct_transcript(report_id: UUID, body: TranscriptBody, v: access.Viewer = Depends(report_viewer),
                       state=Depends(get_state)):
    """A person's correction of the machine transcript: kept apart from it, PII-masked, preferred
    for display. Principal, coordinators and the assignee."""
    try:
        with transaction(state.pool) as conn:
            row = transcripts.correct(conn, v, report_id, body.text, now=utcnow())
    except reports.ReportError as e:
        raise _error(e) from None
    return plain({"report_id": row["report_id"], "human_text": row["transcript_human"],
                  "human_at": row["transcript_human_at"], "machine_text": row["transcript_hi"]})


@router.post("/reports/{report_id}/ack")
def ack(report_id: UUID, v: access.Viewer = Depends(report_viewer), state=Depends(get_state)):
    return _act(state, lambda conn: triage.acknowledge(conn, v, report_id, now=utcnow()))


@router.post("/reports/{report_id}/assign")
def assign(report_id: UUID, body: AssignBody, v: access.Viewer = Depends(report_viewer), state=Depends(get_state)):
    return _act(state, lambda conn: triage.assign(conn, v, report_id, body.member_id, now=utcnow()))


@router.post("/reports/{report_id}/status")
def set_status(report_id: UUID, body: StatusBody, v: access.Viewer = Depends(report_viewer), state=Depends(get_state)):
    def do(conn):
        current = reports.get(conn, report_id)
        if current is None:
            raise reports.NotFound("report not found")
        if body.status == "in_progress":
            return triage.start(conn, v, report_id, now=utcnow())
        if body.status in ("resolved", "closed_no_action"):
            return triage.close(conn, v, report_id, body.status, now=utcnow(), note=body.note)
        if current["status"] in lifecycle.CLOSED:
            return triage.reopen(conn, v, report_id, now=utcnow())
        return triage.acknowledge(conn, v, report_id, now=utcnow())
    return _act(state, do)


@router.post("/reports/{report_id}/merge")
def merge(report_id: UUID, body: MergeBody, v: access.Viewer = Depends(report_viewer), state=Depends(get_state)):
    return _act(state, lambda conn: triage.merge(conn, v, report_id, body.into_report_id, now=utcnow()))


class TitleBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, max_length=200)     # empty or null: back to the automatic title


@router.post("/reports/{report_id}/title")
def set_title(report_id: UUID, body: TitleBody, v: access.Viewer = Depends(report_viewer), state=Depends(get_state)):
    """Rename a report. Whoever may triage it (or its assignee) may; the automatic title is labelled
    title_source 'auto' and never replaces one a person set."""
    try:
        with transaction(state.pool) as conn:
            r = _visible(conn, v, report_id)
            if not (access.may_triage(conn, v, r) or access.may_work(v, r)):
                raise HTTPException(403, "not allowed to rename this report")
            row = report_title.set_by_person(conn, report_id, body.title, actor=v.member_id, now=utcnow())
    except reports.ReportError as e:
        raise _error(e) from None
    return plain({"report": row})


@router.post("/reports/{report_id}/category")
def category(report_id: UUID, body: CategoryBody, v: access.Viewer = Depends(report_viewer), state=Depends(get_state)):
    return _act(state, lambda conn: triage.set_category(conn, v, report_id, body.category_id, now=utcnow()))


@router.post("/reports/{report_id}/urgency")
def urgency(report_id: UUID, body: UrgencyBody, v: access.Viewer = Depends(report_viewer), state=Depends(get_state)):
    return _act(state, lambda conn: triage.set_urgency(conn, v, report_id, body.urgency, now=utcnow()))
