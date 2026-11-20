"""Who may see and act on a report. Visibility is SQL, applied inside every query (LLD §11).

See:     principal and coordinators: every filed report of the AC. Anyone else: reports they filed,
         reports assigned to them, reports filed by anyone in their subtree, and reports whose area
         lies in a scope held by them or anyone in their subtree. Restricted categories: principal,
         coordinators and the reporter only.
Triage:  acknowledge, assign, merge, set category or urgency, close without an assignee:
         principal, coordinators, and members above the reporter in the tree or who lead a team and
         whose scope covers the report's area. Never the reporter on their own report. Restricted:
         principal and coordinators only.
Work:    the assignee starts and closes their assignment.
Reopen:  principal and coordinators.
Drafts are never visible here: they belong to the reporter until filed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

import psycopg


@dataclass(frozen=True)
class Viewer:
    member_id: UUID
    ac_id: str
    is_principal: bool
    is_coordinator: bool
    display_name: str = ""

    @property
    def sees_all(self) -> bool:
        return self.is_principal or self.is_coordinator


def viewer_for(conn: psycopg.Connection, member_id: UUID) -> Viewer | None:
    row = conn.execute(
        """SELECT member_id, ac_id, is_principal, is_coordinator, display_name FROM field.member
           WHERE member_id = %s AND active""",
        (member_id,),
    ).fetchone()
    return Viewer(**row) if row else None


_SUBTREE = """(WITH RECURSIVE sub AS (
        SELECT %(rv_me)s::uuid AS member_id
        UNION
        SELECT m.member_id FROM field.member m JOIN sub ON m.parent_member_id = sub.member_id)
    SELECT member_id FROM sub)"""


def params(v: Viewer) -> dict[str, Any]:
    return {"rv_me": v.member_id, "rv_ac": v.ac_id}


def visible_where(v: Viewer, alias: str = "r") -> str:
    a = alias
    base = f"{a}.ac_id = %(rv_ac)s AND {a}.status <> 'draft'"
    if v.sees_all:
        return base
    covers = (f"EXISTS (SELECT 1 FROM field.member_scope s WHERE s.member_id IN {_SUBTREE} "
              f"AND (s.area_id = {a}.area_id OR (s.area_id IS NULL AND s.block_id = {a}.block_id)))")
    return (f"{base} AND ({a}.reporter_member_id = %(rv_me)s OR (NOT {a}.restricted AND ("
            f"{a}.assigned_to = %(rv_me)s OR {a}.reporter_member_id IN {_SUBTREE} OR {covers})))")


def can_see(conn: psycopg.Connection, v: Viewer, report_id: UUID) -> bool:
    return conn.execute(f"SELECT 1 FROM field.report r WHERE r.report_id = %(rid)s AND {visible_where(v)}",
                        {**params(v), "rid": report_id}).fetchone() is not None


def may_triage(conn: psycopg.Connection, v: Viewer, report: dict[str, Any]) -> bool:
    if report["ac_id"] != v.ac_id or report["status"] == "draft":
        return False
    if v.sees_all:
        return True
    if report["restricted"] or report["reporter_member_id"] == v.member_id:
        return False
    row = conn.execute(
        f"""SELECT
              EXISTS (SELECT 1 FROM {_SUBTREE} t WHERE t.member_id = %(rep)s AND t.member_id <> %(rv_me)s) AS above,
              EXISTS (SELECT 1 FROM field.member c WHERE c.parent_member_id = %(rv_me)s AND c.active) AS leads,
              EXISTS (SELECT 1 FROM field.member_scope s WHERE s.member_id IN {_SUBTREE}
                      AND (s.area_id = %(area)s OR (s.area_id IS NULL AND s.block_id = %(block)s))) AS covers""",
        {**params(v), "rep": report["reporter_member_id"], "area": report["area_id"], "block": report["block_id"]},
    ).fetchone()
    return bool(row["above"] or (row["leads"] and row["covers"]))


def may_work(v: Viewer, report: dict[str, Any]) -> bool:
    return report["assigned_to"] == v.member_id


def may_reopen(v: Viewer, report: dict[str, Any]) -> bool:
    return v.sees_all and report["ac_id"] == v.ac_id
