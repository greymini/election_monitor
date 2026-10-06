#!/usr/bin/env python3
"""Upsert the single admin user from APP_USERNAME / APP_PASSWORD; deactivate others."""

from __future__ import annotations

import os
import sys

from api.deps import hash_password
from common.config import get_settings
from common.db import cursor, query_one
from common.logging_setup import get_logger

log = get_logger(__name__)


def main() -> int:
    if os.environ.get("ALLOW_MULTI_USER", "").lower() in ("1", "true", "yes"):
        log.info("ALLOW_MULTI_USER set; skipping single-user bootstrap")
        return 0
    settings = get_settings()
    username = (settings.app_username or "").strip()
    password = (settings.app_password or "").strip()
    if not username or not password:
        log.error("APP_USERNAME and APP_PASSWORD must be set")
        return 1
    if len(password) < 8:
        log.error("APP_PASSWORD must be at least 8 characters")
        return 1
    digest = hash_password(password)
    with cursor() as cur:
        cur.execute(
            "UPDATE app_user SET is_active = false WHERE username IS DISTINCT FROM %s",
            (username,),
        )
        cur.execute(
            "SELECT user_id FROM app_user WHERE username = %s",
            (username,),
        )
        row = cur.fetchone()
        if row:
            cur.execute(
                "UPDATE app_user SET password_hash = %s, role = 'admin', is_active = true, "
                "name = COALESCE(name, %s) WHERE user_id = %s",
                (digest, username, row["user_id"]),
            )
        else:
            cur.execute(
                "INSERT INTO app_user (phone, username, name, role, password_hash, is_active) "
                "VALUES (%s, %s, %s, 'admin', %s, true) RETURNING user_id",
                (f"user:{username}", username, username, digest),
            )
    user = query_one("SELECT user_id, username FROM app_user WHERE username = %s AND is_active", (username,))
    log.info("single user ready: %s (user_id=%s)", user["username"], user["user_id"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
