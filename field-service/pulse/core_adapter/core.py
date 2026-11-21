"""CoreAdapter backed by the core platform's PostgreSQL (read-only).

Reads app_user, ac, block, area, booth, roll_revision and roll_snapshot. Writes nothing.
Ids: AC = "ac-<ac_number>"; block, area and user ids are the core's integers as strings.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import jwt
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from pulse.core_adapter.base import Ac, Area, Block, CoreUser

_AC_RE = re.compile(r"ac-([0-9]{1,5})")
_INT_MAX = 2**31 - 1
_ROLES = frozenset({"admin", "strategist", "block"})   # the core has no 'rep' role


def ac_id_for(ac_number: int) -> str:
    return f"ac-{ac_number}"


def _ac_number(ac_id: str) -> int | None:
    m = _AC_RE.fullmatch(ac_id) if isinstance(ac_id, str) else None
    return int(m.group(1)) if m else None


def _int_id(value: str) -> int | None:
    if isinstance(value, str) and value.isascii() and value.isdigit():
        n = int(value)
        if n <= _INT_MAX:
            return n
    return None


class CoreDbAdapter:
    def __init__(self, database_url: str, jwt_secret: str):
        if not database_url or not jwt_secret:
            raise ValueError("CoreDbAdapter needs database_url and jwt_secret")
        self._secret = jwt_secret
        self._pool = ConnectionPool(
            database_url, min_size=0, max_size=4, open=True,
            kwargs={"row_factory": dict_row, "autocommit": True,
                    "options": "-c default_transaction_read_only=on"},
        )

    def close(self) -> None:
        self._pool.close()

    def _rows(self, sql: str, params: tuple | None = None) -> list[dict]:
        with self._pool.connection() as conn:
            return conn.execute(sql, params).fetchall()

    def get_user(self, user_id: str) -> CoreUser | None:
        uid = _int_id(user_id)
        if uid is None:
            return None
        rows = self._rows(
            "SELECT user_id, name, role, block_id FROM app_user WHERE user_id = %s AND is_active", (uid,))
        if not rows:
            return None
        r = rows[0]
        if r["role"] not in _ROLES:
            return None
        if r["role"] == "block":
            if r["block_id"] is None:
                return None
            b = self._rows(
                "SELECT a.ac_number FROM block b JOIN ac a ON a.ac_id = b.ac_id "
                "WHERE b.block_id = %s AND a.is_active", (r["block_id"],))
            if not b:
                return None
            ac_ids = (ac_id_for(b[0]["ac_number"]),)
            block_ids = (str(r["block_id"]),)
        else:
            ac_ids = tuple(ac_id_for(x["ac_number"]) for x in self._rows(
                "SELECT ac_number FROM ac WHERE is_active ORDER BY ac_number"))
            block_ids = ()
        return CoreUser(user_id=str(r["user_id"]), display_name=r["name"], role=r["role"],
                        ac_ids=ac_ids, block_ids=block_ids)

    def verify_token(self, token: str) -> CoreUser | None:
        if not token or not isinstance(token, str):
            return None
        try:
            claims = jwt.decode(token, self._secret, algorithms=["HS256"],
                                options={"require": ["sub", "exp"]})
        except (jwt.PyJWTError, ValueError):
            return None
        return self.get_user(str(claims["sub"]))

    def list_acs(self) -> list[Ac]:
        return [Ac(ac_id=ac_id_for(r["ac_number"]), ac_number=r["ac_number"],
                   name_en=r["name_en"], name_hi=r["name_hi"])
                for r in self._rows("SELECT ac_number, name_en, name_hi FROM ac "
                                    "WHERE is_active ORDER BY ac_number")]

    def list_blocks(self, ac_id: str) -> list[Block]:
        n = _ac_number(ac_id)
        if n is None:
            return []
        return [Block(block_id=str(r["block_id"]), ac_id=ac_id, name_en=r["name_en"], name_hi=r["name_hi"])
                for r in self._rows(
                    "SELECT b.block_id, b.name_en, b.name_hi FROM block b JOIN ac a ON a.ac_id = b.ac_id "
                    "WHERE a.ac_number = %s AND a.is_active ORDER BY b.block_id", (n,))]

    def list_areas(self, ac_id: str, block_id: str | None = None) -> list[Area]:
        n = _ac_number(ac_id)
        if n is None:
            return []
        sql = ("SELECT ar.area_id, ar.block_id, ar.kind, ar.name_en, ar.name_hi "
               "FROM area ar JOIN ac a ON a.ac_id = ar.ac_id "
               "JOIN block b ON b.block_id = ar.block_id AND b.ac_id = ar.ac_id "
               "WHERE a.ac_number = %s AND a.is_active")
        params: tuple = (n,)
        if block_id is not None:
            bid = _int_id(block_id)
            if bid is None:
                return []
            sql += " AND ar.block_id = %s"
            params = (n, bid)
        sql += " ORDER BY ar.block_id, ar.kind, ar.area_id"
        return [Area(area_id=str(r["area_id"]), ac_id=ac_id, block_id=str(r["block_id"]), kind=r["kind"],
                     name_en=r["name_en"], name_hi=r["name_hi"]) for r in self._rows(sql, params)]

    def electors_for_area(self, area_id: str) -> int | None:
        aid = _int_id(area_id)
        if aid is None:
            return None
        rows = self._rows(
            "WITH ab AS (SELECT booth_uid FROM booth WHERE area_id = %s AND is_active), "
            "n AS (SELECT count(*) AS c FROM ab) "
            "SELECT SUM(rs.electors)::int AS electors "
            "FROM roll_snapshot rs JOIN ab USING (booth_uid) JOIN roll_revision rr USING (revision_id) "
            "GROUP BY rr.revision_id, rr.revision_date "
            "HAVING COUNT(*) = (SELECT c FROM n) AND (SELECT c FROM n) > 0 "
            "ORDER BY rr.revision_date DESC, rr.revision_id DESC LIMIT 1", (aid,))
        return int(rows[0]["electors"]) if rows else None


def conformance_factory():
    """For: PULSE_CONFORMANCE_TARGET=pulse.core_adapter.core:conformance_factory
    Needs CORE_DATABASE_URL and CORE_JWT_SECRET; CONFORMANCE_USER_ID is optional."""
    import os

    from pulse.core_adapter.conformance import Target

    url, secret = os.environ["CORE_DATABASE_URL"], os.environ["CORE_JWT_SECRET"]
    adapter = CoreDbAdapter(url, secret)
    uid = os.environ.get("CONFORMANCE_USER_ID", "").strip()
    if not uid:
        rows = adapter._rows("SELECT user_id FROM app_user WHERE is_active ORDER BY user_id LIMIT 1")
        if not rows:
            raise RuntimeError("no active user in the core database; create one first")
        uid = str(rows[0]["user_id"])

    def token_for(user_id: str) -> str:
        exp = datetime.now(timezone.utc) + timedelta(minutes=30)
        return jwt.encode({"sub": user_id, "exp": exp}, secret, algorithm="HS256")

    return Target(adapter=adapter, user_id=uid, token_for=token_for)
