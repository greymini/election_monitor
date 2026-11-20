"""tg_session access: one row per chat holding the open flow's state and payload."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import psycopg
from psycopg.types.json import Jsonb


@dataclass
class Session:
    chat_id: int
    flow: str
    state: str
    payload: dict[str, Any]
    expires_at: datetime

    def expired(self, now: datetime) -> bool:
        return self.expires_at <= now


def load(conn: psycopg.Connection, chat_id: int) -> Session | None:
    """Locks the row for the rest of the transaction, so two taps on one card are handled one after the other."""
    row = conn.execute(
        "SELECT chat_id, flow, state, payload, expires_at FROM field.tg_session WHERE chat_id = %s FOR UPDATE",
        (chat_id,),
    ).fetchone()
    return Session(**row) if row else None


def save(conn: psycopg.Connection, chat_id: int, flow: str, state: str, payload: dict[str, Any], *,
         ttl: timedelta, now: datetime) -> None:
    conn.execute(
        """INSERT INTO field.tg_session (chat_id, flow, state, payload, updated_at, expires_at)
           VALUES (%s, %s, %s, %s, %s, %s)
           ON CONFLICT (chat_id) DO UPDATE SET flow = EXCLUDED.flow, state = EXCLUDED.state,
               payload = EXCLUDED.payload, updated_at = EXCLUDED.updated_at, expires_at = EXCLUDED.expires_at""",
        (chat_id, flow, state, Jsonb(payload), now, now + ttl),
    )


def clear(conn: psycopg.Connection, chat_id: int) -> None:
    conn.execute("DELETE FROM field.tg_session WHERE chat_id = %s", (chat_id,))
