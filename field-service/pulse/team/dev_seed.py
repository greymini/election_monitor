"""Create the development team tree (db/seeds/dev_team.json) through the team service.

Idempotent: members whose user is already a member are left as they are.
"""
from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

import psycopg

from pulse.config import REPO_ROOT
from pulse.core_adapter import CoreAdapter
from pulse.team.service import Scope, create_member

DEV_TEAM_PATH = REPO_ROOT / "db" / "seeds" / "dev_team.json"


def seed_team(conn: psycopg.Connection, core: CoreAdapter, path: Path = DEV_TEAM_PATH) -> dict[str, UUID]:
    """Return {seed key: member_id} for every member in the file."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    ac_id = data["ac_id"]
    ids: dict[str, UUID] = {}
    for m in data["members"]:  # parents are listed before children
        existing = conn.execute("SELECT member_id FROM field.member WHERE user_id = %s", (m["user_id"],)).fetchone()
        if existing:
            ids[m["key"]] = existing["member_id"]
            continue
        row = create_member(
            conn,
            core,
            ac_id,
            user_id=m["user_id"],
            display_name=m["display_name"],
            parent_member_id=ids[m["parent"]] if m["parent"] else None,
            is_principal=m.get("is_principal", False),
            finance_scope=m["finance_scope"],
            can_give_advance=m["can_give_advance"],
            scopes=[Scope(**s) for s in m["scopes"]],
        )
        ids[m["key"]] = row["member_id"]
    return ids
