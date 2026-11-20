"""Finance scope as SQL, so authorisation happens inside the query (LLD §11).

    own   rows where the viewer is the payer, the receiver or the creator
    team  the same for anyone in the viewer's subtree, the viewer included
    all   every row of the AC
    none  nothing (callers refuse before querying)

Fragments use bound parameters %(v_ac)s and %(v_me)s from `params(viewer)`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID


@dataclass(frozen=True)
class Viewer:
    member_id: UUID
    ac_id: str
    finance_scope: str
    is_principal: bool
    can_give_advance: bool
    display_name: str


def params(v: Viewer) -> dict[str, Any]:
    return {"v_ac": v.ac_id, "v_me": v.member_id}


TEAM_MEMBERS = """(WITH RECURSIVE sub AS (
        SELECT %(v_me)s::uuid AS member_id
        UNION
        SELECT m.member_id FROM field.member m JOIN sub ON m.parent_member_id = sub.member_id)
    SELECT member_id FROM sub)"""


def visible_members(v: Viewer) -> str:
    """Subquery of member_ids whose money the viewer may see."""
    if v.finance_scope == "all":
        return "(SELECT member_id FROM field.member WHERE ac_id = %(v_ac)s)"
    if v.finance_scope == "team":
        return TEAM_MEMBERS
    if v.finance_scope == "own":
        return "(SELECT %(v_me)s::uuid)"
    return "(SELECT NULL::uuid WHERE FALSE)"


def txn_where(v: Viewer, alias: str = "t") -> str:
    a = alias
    if v.finance_scope == "all":
        return f"{a}.ac_id = %(v_ac)s"
    if v.finance_scope == "none":
        return "FALSE"
    members = visible_members(v)
    return (f"{a}.ac_id = %(v_ac)s AND ({a}.from_member_id IN {members} OR {a}.to_member_id IN {members} "
            f"OR {a}.created_by IN {members})")
