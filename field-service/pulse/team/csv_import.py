"""Team import from CSV: many members in one go, all or nothing.

Columns (header row required, order free, names case-insensitive):

    user_id, display_name, parent_user_id, finance_scope, can_give_advance, is_coordinator, areas, blocks

- `parent_user_id`: the core user id of the person this member reports to. It must be an existing member
  or a row earlier in the same file. The principal is not imported: create them first on the Team screen.
- `finance_scope`: none | own | team | all (blank = own).
- `can_give_advance`, `is_coordinator`: yes/no, true/false, 1/0 (blank = no).
- `areas`, `blocks`: core ids separated by ';' (blank = no area).

Every row goes through `service.create_member`, so the rules are exactly those of the Team screen. A file
with any bad row imports nothing; the result lists each row with its error.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from typing import Any

import psycopg

from pulse.core_adapter import CoreAdapter
from pulse.team import service
from pulse.team.errors import TeamError

COLUMNS = ("user_id", "display_name", "parent_user_id", "finance_scope", "can_give_advance", "is_coordinator",
           "areas", "blocks")
REQUIRED = ("user_id", "display_name", "parent_user_id")
MAX_ROWS = 500
MAX_BYTES = 256 * 1024
_TRUE = {"yes", "y", "true", "1"}
_FALSE = {"no", "n", "false", "0", ""}


class ImportFileError(ValueError):
    """The file as a whole cannot be read (no header, unknown column, too big)."""


@dataclass
class RowResult:
    row: int                       # line number in the file (header is line 1)
    user_id: str
    display_name: str
    ok: bool
    error: str | None = None
    member_id: str | None = None


@dataclass
class ImportResult:
    rows: list[RowResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.rows) and all(r.ok for r in self.rows)

    @property
    def errors(self) -> int:
        return sum(not r.ok for r in self.rows)


def parse(text: str) -> list[tuple[int, dict[str, str]]]:
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise ImportFileError(f"file larger than {MAX_BYTES // 1024} KB")
    text = text.lstrip("﻿")                      # Excel writes a byte-order mark
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ImportFileError("the file is empty")
    header = [(h or "").strip().lower() for h in reader.fieldnames]
    unknown = [h for h in header if h not in COLUMNS]
    if unknown:
        raise ImportFileError(f"unknown column(s): {', '.join(unknown)}; expected {', '.join(COLUMNS)}")
    missing = [c for c in REQUIRED if c not in header]
    if missing:
        raise ImportFileError(f"missing column(s): {', '.join(missing)}")
    reader.fieldnames = header
    rows = []
    for raw in reader:
        if None in raw:
            raise ImportFileError(f"line {reader.line_num}: more values than columns")
        row = {k: (v or "").strip() for k, v in raw.items()}
        if not any(row.values()):
            continue
        rows.append((reader.line_num, row))
    if not rows:
        raise ImportFileError("no member rows under the header")
    if len(rows) > MAX_ROWS:
        raise ImportFileError(f"more than {MAX_ROWS} rows")
    return rows


def _flag(value: str, name: str) -> bool:
    v = value.lower()
    if v in _TRUE:
        return True
    if v in _FALSE:
        return False
    raise ValueError(f"{name} must be yes or no, not {value!r}")


def _ids(value: str) -> list[str]:
    return [x.strip() for x in value.split(";") if x.strip()]


def run(conn: psycopg.Connection, core: CoreAdapter, ac_id: str, rows: list[tuple[int, dict[str, str]]]) -> ImportResult:
    """Create every row inside the caller's transaction. The caller commits only if `result.ok`."""
    result = ImportResult()
    by_user = {m["user_id"]: m["member_id"] for m in service.list_members(conn, ac_id)}
    for line, r in rows:
        out = RowResult(row=line, user_id=r.get("user_id", ""), display_name=r.get("display_name", ""), ok=False)
        result.rows.append(out)
        try:
            if not out.user_id:
                raise ValueError("user_id is empty")
            parent_user = r.get("parent_user_id", "")
            if not parent_user:
                raise ValueError("parent_user_id is empty (the principal is created on the Team screen, not imported)")
            if parent_user not in by_user:
                raise ValueError(f"parent {parent_user!r} is not a member yet (put their row first)")
            scopes = [service.Scope(area_id=a) for a in _ids(r.get("areas", ""))]
            scopes += [service.Scope(block_id=b) for b in _ids(r.get("blocks", ""))]
            with conn.transaction():                  # savepoint: a bad row leaves no trace, the rest still run
                row = service.create_member(
                    conn, core, ac_id,
                    user_id=out.user_id,
                    display_name=out.display_name,
                    parent_member_id=by_user[parent_user],
                    finance_scope=r.get("finance_scope") or "own",
                    can_give_advance=_flag(r.get("can_give_advance", ""), "can_give_advance"),
                    is_coordinator=_flag(r.get("is_coordinator", ""), "is_coordinator"),
                    scopes=scopes,
                )
        except (TeamError, ValueError) as e:
            out.error = str(e)
            continue
        out.ok, out.member_id = True, str(row["member_id"])
        by_user[out.user_id] = row["member_id"]
    return result


def as_dict(result: ImportResult, *, imported: bool) -> dict[str, Any]:
    return {"ok": result.ok, "imported": imported, "total": len(result.rows), "errors": result.errors,
            "rows": [r.__dict__ for r in result.rows]}
