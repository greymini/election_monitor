"""Telegram link tokens, linking and unlinking (LLD §2.2).

Tokens are 32 random bytes, URL-safe (43 characters, within Telegram's 64-character start
parameter). Only their SHA-256 is stored; the raw token is returned once to the admin.
"""
from __future__ import annotations

import enum
import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import psycopg

from pulse.common.clock import utcnow
from pulse.team.errors import Invalid, NotFound


class LinkResult(enum.Enum):
    LINKED = "linked"
    INVALID = "invalid"            # unknown, expired, already used, or member inactive
    ACCOUNT_IN_USE = "account_in_use"  # this Telegram account is linked to another member


@dataclass(frozen=True)
class IssuedToken:
    token: str
    expires_at: datetime


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _now(now: datetime | None) -> datetime:
    return now or utcnow()


def create_link_token(
    conn: psycopg.Connection, ac_id: str, member_id: UUID, *, ttl: timedelta, now: datetime | None = None
) -> IssuedToken:
    row = conn.execute(
        "SELECT active FROM field.member WHERE ac_id = %s AND member_id = %s FOR UPDATE", (ac_id, member_id)
    ).fetchone()
    if row is None:
        raise NotFound("member not found")
    if not row["active"]:
        raise Invalid("member is not active")
    # a new token replaces any unused earlier one
    conn.execute("DELETE FROM field.tg_link_token WHERE member_id = %s AND used_at IS NULL", (member_id,))
    raw = secrets.token_urlsafe(32)
    t = _now(now)
    expires_at = t + ttl
    conn.execute(
        "INSERT INTO field.tg_link_token (token_hash, member_id, created_at, expires_at) VALUES (%s, %s, %s, %s)",
        (hash_token(raw), member_id, t, expires_at),
    )
    return IssuedToken(token=raw, expires_at=expires_at)


def redeem_link_token(
    conn: psycopg.Connection, raw: str, telegram_user_id: int, chat_id: int, *, now: datetime | None = None
) -> tuple[LinkResult, dict[str, Any] | None]:
    """Consume a token and link the Telegram account. Returns the member row on success."""
    t = _now(now)
    row = conn.execute(
        """SELECT t.member_id, m.display_name, m.active
           FROM field.tg_link_token t JOIN field.member m ON m.member_id = t.member_id
           WHERE t.token_hash = %s AND t.used_at IS NULL AND t.expires_at > %s
           FOR UPDATE OF t""",
        (hash_token(raw), t),
    ).fetchone()
    if row is None or not row["active"]:
        return LinkResult.INVALID, None
    member_id = row["member_id"]

    other = conn.execute(
        "SELECT member_id FROM field.tg_link WHERE telegram_user_id = %s AND revoked_at IS NULL FOR UPDATE",
        (telegram_user_id,),
    ).fetchone()
    if other is not None and other["member_id"] != member_id:
        return LinkResult.ACCOUNT_IN_USE, None

    # one Telegram account per member: a new link replaces the member's old one
    conn.execute(
        "UPDATE field.tg_link SET revoked_at = %s WHERE member_id = %s AND revoked_at IS NULL", (t, member_id)
    )
    conn.execute(
        "INSERT INTO field.tg_link (telegram_user_id, member_id, chat_id, linked_at) VALUES (%s, %s, %s, %s)",
        (telegram_user_id, member_id, chat_id, t),
    )
    conn.execute("UPDATE field.tg_link_token SET used_at = %s WHERE token_hash = %s", (t, hash_token(raw)))
    return LinkResult.LINKED, {"member_id": member_id, "display_name": row["display_name"]}


def unlink(conn: psycopg.Connection, ac_id: str, member_id: UUID) -> bool:
    """Revoke the member's active link; effective from the next update. Returns whether one existed."""
    if conn.execute("SELECT 1 FROM field.member WHERE ac_id = %s AND member_id = %s", (ac_id, member_id)).fetchone() is None:
        raise NotFound("member not found")
    cur = conn.execute(
        "UPDATE field.tg_link SET revoked_at = now() WHERE member_id = %s AND revoked_at IS NULL", (member_id,)
    )
    return cur.rowcount > 0


def active_member_for_telegram_user(conn: psycopg.Connection, telegram_user_id: int) -> dict[str, Any] | None:
    return conn.execute(
        """SELECT m.member_id, m.ac_id, m.display_name, m.is_principal, m.finance_scope, m.can_give_advance,
                  m.parent_member_id, m.is_coordinator, m.user_id
           FROM field.tg_link l JOIN field.member m ON m.member_id = l.member_id
           WHERE l.telegram_user_id = %s AND l.revoked_at IS NULL AND m.active""",
        (telegram_user_id,),
    ).fetchone()
