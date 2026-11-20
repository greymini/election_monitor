"""Members of the team tree and their scopes.

All core data (users, AC, blocks, areas) is validated through CoreAdapter. Every function takes a
connection inside the caller's transaction.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable
from uuid import UUID

import psycopg
from psycopg import errors as pg_errors

from pulse.common import pii
from pulse.common.clock import utcnow
from pulse.core_adapter import CoreAdapter
from pulse.team.errors import Conflict, Invalid, NotFound
from pulse.team.tree import is_in_subtree

FINANCE_SCOPES = ("none", "own", "team", "all")
UPDATABLE_FIELDS = {"display_name", "parent_member_id", "finance_scope", "can_give_advance", "is_coordinator",
                    "active", "scopes"}


@dataclass(frozen=True)
class Scope:
    block_id: str | None = None
    area_id: str | None = None


_MEMBER_SELECT = """
SELECT m.member_id, m.ac_id, m.user_id, m.display_name, m.parent_member_id, m.is_principal,
       m.finance_scope, m.can_give_advance, m.is_coordinator, m.active, m.created_at, m.deactivated_at,
       COALESCE((SELECT json_agg(json_build_object('block_id', s.block_id, 'area_id', s.area_id)
                                 ORDER BY s.block_id NULLS LAST, s.area_id NULLS FIRST)
                 FROM field.member_scope s WHERE s.member_id = m.member_id), '[]'::json) AS scopes,
       l.linked_at AS telegram_linked_at, l.last_seen_at, l.pin_set_at, m.pin_locked_at
FROM field.member m
LEFT JOIN field.tg_link l ON l.member_id = m.member_id AND l.revoked_at IS NULL
"""


def _now() -> datetime:
    return utcnow()


def _check_name(name: str | None) -> None:
    """A display name is required and may not carry a phone, Aadhaar or voter-id number."""
    if not name or not name.strip():
        raise Invalid("display_name is required")
    kinds = pii.screen(name)
    if kinds:
        raise Invalid(f"display_name contains an identifier ({', '.join(kinds)}); use the name only")


def get_member(conn: psycopg.Connection, ac_id: str, member_id: UUID) -> dict[str, Any]:
    row = conn.execute(_MEMBER_SELECT + "WHERE m.ac_id = %s AND m.member_id = %s", (ac_id, member_id)).fetchone()
    if row is None:
        raise NotFound("member not found")
    return row


def list_members(conn: psycopg.Connection, ac_id: str) -> list[dict[str, Any]]:
    return conn.execute(
        _MEMBER_SELECT + "WHERE m.ac_id = %s ORDER BY m.is_principal DESC, m.display_name, m.member_id", (ac_id,)
    ).fetchall()


def _validate_scopes(core: CoreAdapter, ac_id: str, scopes: Iterable[Scope]) -> list[Scope]:
    blocks = {b.block_id for b in core.list_blocks(ac_id)}
    areas = {a.area_id: a for a in core.list_areas(ac_id)}
    out: list[Scope] = []
    for s in scopes:
        if s.block_id is None and s.area_id is None:
            raise Invalid("a scope needs a block_id or an area_id")
        if s.block_id is not None and s.block_id not in blocks:
            raise Invalid(f"unknown block {s.block_id!r} for this AC")
        if s.area_id is not None:
            area = areas.get(s.area_id)
            if area is None:
                raise Invalid(f"unknown area {s.area_id!r} for this AC")
            if s.block_id is not None and area.block_id != s.block_id:
                raise Invalid(f"area {s.area_id!r} is not in block {s.block_id!r}")
        if s not in out:
            out.append(s)
    return out


def _write_scopes(conn: psycopg.Connection, member_id: UUID, scopes: list[Scope]) -> None:
    conn.execute("DELETE FROM field.member_scope WHERE member_id = %s", (member_id,))
    for s in scopes:
        conn.execute(
            "INSERT INTO field.member_scope (member_id, block_id, area_id) VALUES (%s, %s, %s)",
            (member_id, s.block_id, s.area_id),
        )


def _check_parent(conn: psycopg.Connection, ac_id: str, parent_member_id: UUID) -> None:
    row = conn.execute(
        "SELECT active FROM field.member WHERE member_id = %s AND ac_id = %s", (parent_member_id, ac_id)
    ).fetchone()
    if row is None:
        raise Invalid("parent member not found in this AC")
    if not row["active"]:
        raise Invalid("parent member is not active")


def create_member(
    conn: psycopg.Connection,
    core: CoreAdapter,
    ac_id: str,
    *,
    user_id: str,
    display_name: str,
    parent_member_id: UUID | None = None,
    is_principal: bool = False,
    finance_scope: str = "own",
    can_give_advance: bool = False,
    is_coordinator: bool = False,
    scopes: Iterable[Scope] = (),
) -> dict[str, Any]:
    user = core.get_user(user_id)
    if user is None:
        raise Invalid(f"unknown user {user_id!r}")
    if ac_id not in user.ac_ids:
        raise Invalid("user does not belong to this AC")
    _check_name(display_name)
    if finance_scope not in FINANCE_SCOPES:
        raise Invalid(f"finance_scope must be one of {FINANCE_SCOPES}")
    if is_principal and parent_member_id is not None:
        raise Invalid("the principal reports to no one")
    if not is_principal:
        if parent_member_id is None:
            raise Invalid("parent_member_id is required for everyone except the principal")
        _check_parent(conn, ac_id, parent_member_id)
    clean_scopes = _validate_scopes(core, ac_id, scopes)

    if conn.execute("SELECT 1 FROM field.member WHERE user_id = %s", (user_id,)).fetchone():
        raise Conflict("this user is already a member")
    if is_principal and conn.execute(
        "SELECT 1 FROM field.member WHERE ac_id = %s AND is_principal", (ac_id,)
    ).fetchone():
        raise Conflict("this AC already has a principal")

    try:
        row = conn.execute(
            """INSERT INTO field.member (ac_id, user_id, display_name, parent_member_id, is_principal,
                                         finance_scope, can_give_advance, is_coordinator)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING member_id""",
            (ac_id, user_id, display_name.strip(), parent_member_id, is_principal, finance_scope, can_give_advance,
             is_coordinator),
        ).fetchone()
    except pg_errors.UniqueViolation as e:  # lost a race with a concurrent insert
        raise Conflict("member or principal already exists") from e
    _write_scopes(conn, row["member_id"], clean_scopes)
    return get_member(conn, ac_id, row["member_id"])


def update_member(
    conn: psycopg.Connection, core: CoreAdapter, ac_id: str, member_id: UUID, changes: dict[str, Any]
) -> dict[str, Any]:
    unknown = set(changes) - UPDATABLE_FIELDS
    if unknown:
        raise Invalid(f"cannot change: {sorted(unknown)}")
    current = conn.execute(
        "SELECT * FROM field.member WHERE ac_id = %s AND member_id = %s FOR UPDATE", (ac_id, member_id)
    ).fetchone()
    if current is None:
        raise NotFound("member not found")

    sets: dict[str, Any] = {}
    if "display_name" in changes:
        name = changes["display_name"]
        _check_name(name)
        sets["display_name"] = name.strip()
    if "finance_scope" in changes:
        if changes["finance_scope"] not in FINANCE_SCOPES:
            raise Invalid(f"finance_scope must be one of {FINANCE_SCOPES}")
        sets["finance_scope"] = changes["finance_scope"]
    if "can_give_advance" in changes:
        sets["can_give_advance"] = bool(changes["can_give_advance"])
    if "is_coordinator" in changes:
        sets["is_coordinator"] = bool(changes["is_coordinator"])
    if "parent_member_id" in changes:
        parent = changes["parent_member_id"]
        if current["is_principal"]:
            if parent is not None:
                raise Invalid("the principal reports to no one")
        else:
            if parent is None:
                raise Invalid("parent_member_id is required for everyone except the principal")
            if parent == member_id or is_in_subtree(conn, member_id, parent):
                raise Invalid("that parent would create a loop in the team tree")
            _check_parent(conn, ac_id, parent)
            sets["parent_member_id"] = parent
    if "active" in changes and bool(changes["active"]) != current["active"]:
        if changes["active"]:
            sets["active"], sets["deactivated_at"] = True, None
        else:
            sets["active"], sets["deactivated_at"] = False, _now()
            _revoke_access(conn, member_id)

    if sets:
        cols = ", ".join(f"{k} = %({k})s" for k in sets)
        conn.execute(f"UPDATE field.member SET {cols} WHERE member_id = %(member_id)s", {**sets, "member_id": member_id})
    if "scopes" in changes:
        _write_scopes(conn, member_id, _validate_scopes(core, ac_id, changes["scopes"]))
    return get_member(conn, ac_id, member_id)


def _revoke_access(conn: psycopg.Connection, member_id: UUID) -> None:
    """Deactivation revokes the Telegram link and drops unused link tokens."""
    conn.execute("UPDATE field.tg_link SET revoked_at = now() WHERE member_id = %s AND revoked_at IS NULL", (member_id,))
    conn.execute("DELETE FROM field.tg_link_token WHERE member_id = %s AND used_at IS NULL", (member_id,))
