"""Idempotency keys for web writes that create something (POST /txns, /reports, /issues, /members,
/members/import). The client sends `Idempotency-Key: <uuid>`; a repeat with the same key by the same
user on the same route returns the first result instead of creating another row.

The key is claimed inside the same transaction as the write:
- if the write fails, the claim rolls back with it and the key can be used again;
- a concurrent repeat waits on the unique index until the first commits, then replays its result;
- the same key with a different request body is refused (422), so a key can never hide a changed entry.

Stored responses are kept for RETENTION and then purged by the worker (`purge`). The key is optional for
clients that cannot send it (the bot has its own update_id deduplication).
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any

import psycopg
from fastapi import Header, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from psycopg.types.json import Jsonb

RETENTION = timedelta(hours=48)
MAX_KEY = 100


def key_header(idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")) -> str | None:
    if idempotency_key is None:
        return None
    k = idempotency_key.strip()
    if not k or len(k) > MAX_KEY:
        raise HTTPException(422, f"Idempotency-Key must be 1 to {MAX_KEY} characters")
    return k


def _hash(body: Any) -> str:
    return hashlib.sha256(json.dumps(jsonable_encoder(body), sort_keys=True, default=str).encode()).hexdigest()


def claim(conn: psycopg.Connection, key: str | None, *, user_id: str, route: str, body: Any) -> JSONResponse | None:
    """Claim the key for this request, or return the first request's stored response to replay."""
    if key is None:
        return None
    h = _hash(body)
    row = conn.execute(
        """INSERT INTO field.idempotency_key (user_id, route, key, request_hash) VALUES (%s, %s, %s, %s)
           ON CONFLICT (user_id, route, key) DO NOTHING RETURNING key""", (user_id, route, key, h)).fetchone()
    if row is not None:
        return None
    first = conn.execute("SELECT request_hash, status_code, response FROM field.idempotency_key "
                         "WHERE user_id = %s AND route = %s AND key = %s", (user_id, route, key)).fetchone()
    if first["request_hash"] != h:
        raise HTTPException(422, "this Idempotency-Key was already used for a different request")
    return JSONResponse(first["response"], status_code=first["status_code"], headers={"Idempotent-Replay": "true"})


def store(conn: psycopg.Connection, key: str | None, *, user_id: str, route: str, status_code: int, response: Any) -> Any:
    """Record the response for the claimed key (same transaction as the write). Returns the response."""
    if key is not None:
        conn.execute("UPDATE field.idempotency_key SET status_code = %s, response = %s "
                     "WHERE user_id = %s AND route = %s AND key = %s",
                     (status_code, Jsonb(jsonable_encoder(response)), user_id, route, key))
    return response


def purge(conn: psycopg.Connection, now: datetime) -> int:
    return conn.execute("DELETE FROM field.idempotency_key WHERE created_at < %s", (now - RETENTION,)).rowcount
