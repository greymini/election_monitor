"""Local stand-in for the core platform, backed by `seed.json` (dummy data only).

Bearer tokens are HS256 JWTs signed with JWT_SECRET, carrying `sub` = user_id. The core's real
token layout is not known here; only this adapter depends on it.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt

from pulse.config import DEFAULT_CORE_SEED
from pulse.core_adapter.base import Ac, Area, Block, CoreUser


class StubCoreAdapter:
    def __init__(self, seed_path: Path = DEFAULT_CORE_SEED, jwt_secret: str | None = None):
        data = json.loads(Path(seed_path).read_text(encoding="utf-8"))
        self._jwt_secret = jwt_secret
        self._acs = [Ac(**a) for a in data["acs"]]
        self._blocks = [Block(**b) for b in data["blocks"]]
        self._areas = [Area(**{k: v for k, v in a.items() if k != "electors"}) for a in data["areas"]]
        self._electors = {a["area_id"]: a.get("electors") for a in data["areas"]}
        self._users = {
            u["user_id"]: CoreUser(
                user_id=u["user_id"],
                display_name=u["display_name"],
                role=u["role"],
                ac_ids=tuple(u["ac_ids"]),
                block_ids=tuple(u.get("block_ids", ())),
            )
            for u in data["users"]
        }

    def get_user(self, user_id: str) -> CoreUser | None:
        return self._users.get(user_id)

    def verify_token(self, token: str) -> CoreUser | None:
        if not self._jwt_secret or not token:
            return None
        try:
            claims = jwt.decode(token, self._jwt_secret, algorithms=["HS256"], options={"require": ["sub", "exp"]})
        except jwt.PyJWTError:
            return None
        return self.get_user(str(claims["sub"]))

    def issue_token(self, user_id: str, ttl: timedelta = timedelta(hours=12)) -> str:
        """Stub-only helper for local development and tests."""
        if not self._jwt_secret:
            raise RuntimeError("StubCoreAdapter needs jwt_secret to issue tokens")
        if user_id not in self._users:
            raise KeyError(user_id)
        exp = datetime.now(timezone.utc) + ttl
        return jwt.encode({"sub": user_id, "exp": exp}, self._jwt_secret, algorithm="HS256")

    def list_acs(self) -> list[Ac]:
        return list(self._acs)

    def list_blocks(self, ac_id: str) -> list[Block]:
        return [b for b in self._blocks if b.ac_id == ac_id]

    def list_areas(self, ac_id: str, block_id: str | None = None) -> list[Area]:
        return [a for a in self._areas if a.ac_id == ac_id and (block_id is None or a.block_id == block_id)]

    def electors_for_area(self, area_id: str) -> int | None:
        return self._electors.get(area_id)
