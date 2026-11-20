"""Triage actions on filed reports: acknowledge, assign, start, close, merge, reopen, category,
urgency. Each checks who may act (access), applies the change (lifecycle), redraws every card about
the report and tells the reporter where LLD §5.3 says so. Used by the bot and the API alike.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import psycopg

from pulse.notify import alerts, outbox
from pulse.reports import access, duplicates, lifecycle
from pulse.reports import service as reports


class Denied(reports.ReportError):
    pass


@dataclass(frozen=True)
class Done:
    report: dict[str, Any]
    changed: bool = True


def _load(conn: psycopg.Connection, v: access.Viewer, report_id: UUID) -> dict[str, Any]:
    r = reports.get(conn, report_id, lock=True)
    if r is None or r["ac_id"] != v.ac_id or not access.can_see(conn, v, report_id):
        raise reports.NotFound("report not found")
    return r


def _after(conn: psycopg.Connection, r: dict[str, Any], except_alert: int | None) -> None:
    outbox.refresh_report_cards(conn, r["report_id"], except_alert=except_alert)


def acknowledge(conn: psycopg.Connection, v: access.Viewer, report_id: UUID, *, now: datetime,
                except_alert: int | None = None) -> Done:
    """First acknowledgement wins; a later one changes nothing and says who was first."""
    r = _load(conn, v, report_id)
    if not access.may_triage(conn, v, r):
        raise Denied("not allowed to acknowledge")
    if r["status"] != "new":
        return Done(r, changed=False)
    r = lifecycle.change(conn, report_id, "acknowledged", actor=v.member_id, now=now)
    alerts.feedback(conn, r, "acknowledged")
    _after(conn, r, except_alert)
    return Done(r)


def assignable(conn: psycopg.Connection, v: access.Viewer, report: dict[str, Any]) -> list[dict[str, Any]]:
    """Members whose scope covers the report's area first, then the rest (by name). A restricted
    report can only go to the principal or a coordinator."""
    rows = conn.execute(
        """SELECT m.member_id, m.display_name,
                  EXISTS (SELECT 1 FROM field.member_scope s WHERE s.member_id = m.member_id
                          AND (s.area_id = %(area)s OR (s.area_id IS NULL AND s.block_id = %(block)s))) AS covers
           FROM field.member m
           WHERE m.ac_id = %(ac)s AND m.active AND (NOT %(restricted)s OR m.is_principal OR m.is_coordinator)
           ORDER BY covers DESC, m.display_name, m.member_id""",
        {"area": report["area_id"], "block": report["block_id"], "ac": report["ac_id"], "restricted": report["restricted"]},
    ).fetchall()
    return rows


def assign(conn: psycopg.Connection, v: access.Viewer, report_id: UUID, assignee: UUID, *, now: datetime,
           except_alert: int | None = None) -> Done:
    r = _load(conn, v, report_id)
    if not access.may_triage(conn, v, r):
        raise Denied("not allowed to assign")
    if assignee not in {m["member_id"] for m in assignable(conn, v, r)}:
        raise reports.ReportError("that member cannot be assigned this report")
    was_new = r["status"] == "new"
    r = lifecycle.change(conn, report_id, "assigned", actor=v.member_id, now=now, assignee=assignee)
    if was_new:
        alerts.feedback(conn, r, "acknowledged")
    alerts.assignee_card(conn, r)
    _after(conn, r, except_alert)
    return Done(r)


def start(conn: psycopg.Connection, v: access.Viewer, report_id: UUID, *, now: datetime,
          except_alert: int | None = None) -> Done:
    r = _load(conn, v, report_id)
    if not access.may_work(v, r):
        raise Denied("only the assignee starts the work")
    r = lifecycle.change(conn, report_id, "in_progress", actor=v.member_id, now=now)
    _after(conn, r, except_alert)
    return Done(r)


def close(conn: psycopg.Connection, v: access.Viewer, report_id: UUID, status: str, *, now: datetime,
          note: str | None = None, photo_asset_id: UUID | None = None, except_alert: int | None = None) -> Done:
    """resolved or closed_no_action, by the assignee or anyone who may triage; note and photo optional."""
    if status not in ("resolved", "closed_no_action"):
        raise reports.ReportError("close to resolved or closed_no_action")
    r = _load(conn, v, report_id)
    if not (access.may_work(v, r) or access.may_triage(conn, v, r)):
        raise Denied("not allowed to close")
    note = reports.mask_note(note)
    r = lifecycle.change(conn, report_id, status, actor=v.member_id, now=now, note=note)
    if photo_asset_id is not None:
        reports.add_media(conn, report_id, photo_asset_id, actor=v.member_id, now=now, role="closing")
    alerts.feedback(conn, r, status)
    _after(conn, r, except_alert)
    return Done(r)


def merge(conn: psycopg.Connection, v: access.Viewer, report_id: UUID, into: UUID, *, now: datetime,
          except_alert: int | None = None) -> Done:
    r = _load(conn, v, report_id)
    if not access.may_triage(conn, v, r):
        raise Denied("not allowed to merge")
    target_id = duplicates.canonical(conn, into)
    target = reports.get(conn, target_id)
    if target is None or target_id == report_id or target["status"] == "draft" or target["ac_id"] != r["ac_id"] \
            or not access.can_see(conn, v, target_id):
        raise reports.ReportError("cannot merge into that report")
    r = lifecycle.change(conn, report_id, "duplicate", actor=v.member_id, now=now, duplicate_of=target_id)
    reports.event(conn, report_id, "merged_into", actor=v.member_id, now=now, data={"into": str(target_id)})
    reports.event(conn, target_id, "merged_from", actor=v.member_id, now=now, data={"from": str(report_id)})
    alerts.feedback(conn, r, "duplicate", into=target_id)
    _after(conn, r, except_alert)
    return Done(r)


def reopen(conn: psycopg.Connection, v: access.Viewer, report_id: UUID, *, now: datetime) -> Done:
    r = _load(conn, v, report_id)
    if not access.may_reopen(v, r):
        raise Denied("only the principal or a coordinator reopens")
    r = lifecycle.change(conn, report_id, "acknowledged", actor=v.member_id, now=now)
    _after(conn, r, None)
    return Done(r)


def set_category(conn: psycopg.Connection, v: access.Viewer, report_id: UUID, category_id: int, *, now: datetime,
                 except_alert: int | None = None) -> Done:
    r = _load(conn, v, report_id)
    if not access.may_triage(conn, v, r):
        raise Denied("not allowed to change the category")
    r = reports.set_category(conn, report_id, category_id, actor=v.member_id, now=now, except_alert=except_alert)
    return Done(r)


def set_urgency(conn: psycopg.Connection, v: access.Viewer, report_id: UUID, urgency: str, *, now: datetime) -> Done:
    r = _load(conn, v, report_id)
    if not access.may_triage(conn, v, r):
        raise Denied("not allowed to change the urgency")
    r = reports.set_urgency(conn, report_id, urgency, source="user", actor=v.member_id, now=now)
    _after(conn, r, None)
    return Done(r)


