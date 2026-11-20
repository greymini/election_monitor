"""PINs for money actions in the bot (LLD §11).

The PIN is stored only as a bcrypt hash on the member's active Telegram link. Everything about an
open PIN prompt (which step, when it expires, the hash of a first entry awaiting confirmation) is
on tg_link too, never in tg_session. Three wrong entries lock the member's bot money actions until
an admin unlocks or resets through the team API. Set, change, lock, unlock and reset are audited
in field.admin_audit, without the PIN or its hash.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import bcrypt
import psycopg

from pulse.common.text import fold_digits
from pulse.settings_ac import audit

ROUNDS = int(os.environ.get("BCRYPT_ROUNDS", "12"))
PROMPT_TTL = timedelta(minutes=2)
LATE_WINDOW = timedelta(minutes=8)        # after expiry: a PIN-looking message is still swallowed (10 min in all)
MAX_FAILURES = 3
_PIN_RE = re.compile(r"^\d{4,6}$")


def normalise(text: str) -> str:
    return fold_digits((text or "").strip()).replace(" ", "")


def looks_like_pin(text: str) -> bool:
    return bool(_PIN_RE.match(normalise(text)))


def acceptable(pin: str) -> bool:
    """4-6 digits, not all the same, not a straight run up or down."""
    if not _PIN_RE.match(pin):
        return False
    if len(set(pin)) == 1:
        return False
    steps = {int(b) - int(a) for a, b in zip(pin, pin[1:])}
    return steps not in ({1}, {-1})


def _hash(pin: str) -> str:
    return bcrypt.hashpw(pin.encode("ascii"), bcrypt.gensalt(ROUNDS)).decode("ascii")


def _matches(pin: str, hashed: str | None) -> bool:
    return bool(hashed) and bcrypt.checkpw(pin.encode("ascii"), hashed.encode("ascii"))


def link(conn: psycopg.Connection, member_id: UUID) -> dict[str, Any] | None:
    return conn.execute("SELECT * FROM field.tg_link WHERE member_id = %s AND revoked_at IS NULL FOR UPDATE",
                        (member_id,)).fetchone()


def is_own_pin(conn: psycopg.Connection, member_id: UUID, text: str) -> bool:
    """Whether a message is exactly the member's PIN (checked against the hash; no failure is counted).
    Used outside a PIN step, so a PIN sent twice or too late is deleted instead of read as an amount."""
    if not looks_like_pin(text):
        return False
    row = link(conn, member_id)
    return bool(row) and _matches(normalise(text), row["pin_hash"])


def has_pin(conn: psycopg.Connection, member_id: UUID) -> bool:
    row = link(conn, member_id)
    return bool(row and row["pin_hash"])


def is_locked(conn: psycopg.Connection, member_id: UUID) -> bool:
    row = conn.execute("SELECT pin_locked_at FROM field.member WHERE member_id = %s", (member_id,)).fetchone()
    return bool(row and row["pin_locked_at"])


def start(conn: psycopg.Connection, member_id: UUID, stage: str, now: datetime) -> None:
    conn.execute(
        """UPDATE field.tg_link SET pin_stage = %s, pin_stage_expires_at = %s,
               pin_pending_hash = CASE WHEN %s = 'confirm' THEN pin_pending_hash ELSE NULL END
           WHERE member_id = %s AND revoked_at IS NULL""",
        (stage, now + PROMPT_TTL, stage, member_id),
    )


def clear(conn: psycopg.Connection, member_id: UUID) -> None:
    conn.execute("""UPDATE field.tg_link SET pin_stage = NULL, pin_stage_expires_at = NULL, pin_pending_hash = NULL
                    WHERE member_id = %s AND revoked_at IS NULL""", (member_id,))


def stage(conn: psycopg.Connection, member_id: UUID, now: datetime) -> tuple[str | None, str]:
    """(stage, state) where state is 'open', 'late' (expired within LATE_WINDOW) or 'none'."""
    row = link(conn, member_id)
    if row is None or row["pin_stage"] is None:
        return None, "none"
    if now <= row["pin_stage_expires_at"]:
        return row["pin_stage"], "open"
    if now <= row["pin_stage_expires_at"] + LATE_WINDOW:
        return row["pin_stage"], "late"
    clear(conn, member_id)
    return None, "none"


def hold_first_entry(conn: psycopg.Connection, member_id: UUID, pin: str, now: datetime) -> None:
    conn.execute("UPDATE field.tg_link SET pin_pending_hash = %s WHERE member_id = %s AND revoked_at IS NULL",
                 (_hash(pin), member_id))
    start(conn, member_id, "confirm", now)


def confirm_matches(conn: psycopg.Connection, member_id: UUID, pin: str) -> bool:
    row = link(conn, member_id)
    return bool(row) and _matches(pin, row["pin_pending_hash"])


def save(conn: psycopg.Connection, member: dict[str, Any], pin: str, now: datetime) -> None:
    """Store the (already confirmed) PIN on the active link."""
    had = has_pin(conn, member["member_id"])
    conn.execute(
        """UPDATE field.tg_link SET pin_hash = %s, pin_set_at = %s, pin_stage = NULL, pin_stage_expires_at = NULL,
               pin_pending_hash = NULL WHERE member_id = %s AND revoked_at IS NULL""",
        (_hash(pin), now, member["member_id"]),
    )
    audit(conn, member["ac_id"], "pin_changed" if had else "pin_set", actor_member_id=member["member_id"],
          target_member_id=member["member_id"])


def verify(conn: psycopg.Connection, member: dict[str, Any], pin: str, now: datetime) -> str:
    """'ok', 'wrong' (attempts left) or 'locked' (this was the third wrong entry, or already locked)."""
    m = conn.execute("SELECT pin_failures, pin_locked_at FROM field.member WHERE member_id = %s FOR UPDATE",
                     (member["member_id"],)).fetchone()
    if m["pin_locked_at"] is not None:
        return "locked"
    row = link(conn, member["member_id"])
    if row is not None and _matches(pin, row["pin_hash"]):
        conn.execute("UPDATE field.member SET pin_failures = 0 WHERE member_id = %s", (member["member_id"],))
        return "ok"
    failures = m["pin_failures"] + 1
    if failures >= MAX_FAILURES:
        conn.execute("UPDATE field.member SET pin_failures = %s, pin_locked_at = %s WHERE member_id = %s",
                     (failures, now, member["member_id"]))
        clear(conn, member["member_id"])
        audit(conn, member["ac_id"], "pin_locked", target_member_id=member["member_id"],
              data={"failures": failures})
        return "locked"
    conn.execute("UPDATE field.member SET pin_failures = %s WHERE member_id = %s", (failures, member["member_id"]))
    return "wrong"


def attempts_left(conn: psycopg.Connection, member_id: UUID) -> int:
    row = conn.execute("SELECT pin_failures FROM field.member WHERE member_id = %s", (member_id,)).fetchone()
    return max(MAX_FAILURES - row["pin_failures"], 0)


def unlock(conn: psycopg.Connection, ac_id: str, member_id: UUID, *, actor_user_id: str) -> bool:
    row = conn.execute("""UPDATE field.member SET pin_failures = 0, pin_locked_at = NULL
                          WHERE member_id = %s AND ac_id = %s RETURNING member_id""", (member_id, ac_id)).fetchone()
    if row:
        audit(conn, ac_id, "pin_unlocked", actor_user_id=actor_user_id, target_member_id=member_id)
    return row is not None


def reset(conn: psycopg.Connection, ac_id: str, member_id: UUID, *, actor_user_id: str) -> bool:
    """Remove the PIN (the member sets a new one) and unlock."""
    row = conn.execute("""UPDATE field.member SET pin_failures = 0, pin_locked_at = NULL
                          WHERE member_id = %s AND ac_id = %s RETURNING member_id""", (member_id, ac_id)).fetchone()
    if row is None:
        return False
    conn.execute("""UPDATE field.tg_link SET pin_hash = NULL, pin_set_at = NULL, pin_stage = NULL,
                        pin_stage_expires_at = NULL, pin_pending_hash = NULL
                    WHERE member_id = %s AND revoked_at IS NULL""", (member_id,))
    audit(conn, ac_id, "pin_reset", actor_user_id=actor_user_id, target_member_id=member_id)
    return True
