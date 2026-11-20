"""Impact follow-up (LLD §7, layer 2): for ASSIST and WORKS expenses, ask the member who entered it
whether the work was done after followup_days (default 14, per AC). One ask, one reminder three days later,
then nothing: an unanswered follow-up stays NULL, never "no".

Rows in field.txn_followup are created, cancelled and re-opened only by pulse/ledger/service.py
(create, void, reclassify) through this module; answers come from the bot (flow_followup).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import psycopg

FOLLOWUP_CODES = ("ASSIST", "WORKS")          # top-level bucket codes
REMIND_AFTER = timedelta(days=3)
ANSWERS = ("done", "partial", "not_done")


def applies(top_code: str | None) -> bool:
    return top_code in FOLLOWUP_CODES


def create_for(conn: psycopg.Connection, txn: dict[str, Any], *, days: int) -> None:
    """Called in the same transaction as the expense insert. Asks whoever entered it."""
    conn.execute(
        """INSERT INTO field.txn_followup (txn_id, ac_id, member_id, due_at, created_at)
           VALUES (%s, %s, %s, %s, %s) ON CONFLICT (txn_id) DO UPDATE SET cancelled_at = NULL
           WHERE field.txn_followup.answer IS NULL""",
        (txn["txn_id"], txn["ac_id"], txn["created_by"], txn["created_at"] + timedelta(days=days), txn["created_at"]))


def cancel(conn: psycopg.Connection, txn_id: UUID, *, now: datetime) -> None:
    """The entry was voided or moved out of ASSIST / WORKS: never asked (answers already given stay)."""
    conn.execute("UPDATE field.txn_followup SET cancelled_at = %s WHERE txn_id = %s AND answer IS NULL "
                 "AND cancelled_at IS NULL", (now, txn_id))


def get(conn: psycopg.Connection, txn_id: UUID, *, lock: bool = False) -> dict[str, Any] | None:
    return conn.execute("SELECT * FROM field.txn_followup WHERE txn_id = %s" + (" FOR UPDATE" if lock else ""),
                        (txn_id,)).fetchone()


_OPEN = "f.cancelled_at IS NULL AND f.answer IS NULL"


def due_to_ask(conn: psycopg.Connection, ac_id: str, now: datetime) -> list[dict[str, Any]]:
    return conn.execute(
        f"""SELECT f.* FROM field.txn_followup f JOIN field.money_txn t ON t.txn_id = f.txn_id
            WHERE f.ac_id = %s AND {_OPEN} AND f.asked_at IS NULL AND f.due_at <= %s AND t.status <> 'void'
            ORDER BY f.due_at FOR UPDATE OF f SKIP LOCKED""", (ac_id, now)).fetchall()


def due_to_remind(conn: psycopg.Connection, ac_id: str, now: datetime) -> list[dict[str, Any]]:
    return conn.execute(
        f"""SELECT f.* FROM field.txn_followup f JOIN field.money_txn t ON t.txn_id = f.txn_id
            WHERE f.ac_id = %s AND {_OPEN} AND f.asked_at <= %s AND f.reminded_at IS NULL AND t.status <> 'void'
            ORDER BY f.asked_at FOR UPDATE OF f SKIP LOCKED""", (ac_id, now - REMIND_AFTER)).fetchall()


def mark_asked(conn: psycopg.Connection, txn_id: UUID, *, now: datetime) -> None:
    conn.execute("UPDATE field.txn_followup SET asked_at = %s WHERE txn_id = %s", (now, txn_id))


def mark_reminded(conn: psycopg.Connection, txn_id: UUID, *, now: datetime) -> None:
    conn.execute("UPDATE field.txn_followup SET reminded_at = %s WHERE txn_id = %s", (now, txn_id))


def answer(conn: psycopg.Connection, txn_id: UUID, member_id: UUID, value: str, *, now: datetime) -> str:
    """'answered', 'already' (the first answer stands), or 'not_yours' / 'closed'."""
    if value not in ANSWERS:
        raise ValueError(value)
    f = get(conn, txn_id, lock=True)
    if f is None or f["member_id"] != member_id:
        return "not_yours"
    if f["answer"] is not None:
        return "already"
    if f["cancelled_at"] is not None:
        return "closed"
    conn.execute("UPDATE field.txn_followup SET answer = %s, answered_at = %s WHERE txn_id = %s",
                 (value, now, txn_id))
    return "answered"


def attach_photo(conn: psycopg.Connection, txn_id: UUID, member_id: UUID, asset_id: UUID) -> bool:
    row = conn.execute(
        """UPDATE field.txn_followup SET photo_asset_id = %s
           WHERE txn_id = %s AND member_id = %s AND answer IS NOT NULL RETURNING txn_id""",
        (asset_id, txn_id, member_id)).fetchone()
    return row is not None
