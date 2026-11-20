"""Team API: members, scopes, Telegram link tokens and unlinking (admin only)."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from pulse.api.deps import AdminContext, get_state, require_admin
from pulse.api import idempotency as idem
from pulse.db import transaction
from pulse.settings_ac import audit
from pulse.team import csv_import, linking, service
from pulse.team.errors import Conflict, NotFound, TeamError

router = APIRouter(prefix="/acs/{ac_number}/field")

FinanceScope = Literal["none", "own", "team", "all"]


class ScopeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    block_id: str | None = None
    area_id: str | None = None


class MemberCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: str
    display_name: str = Field(min_length=1)
    parent_member_id: UUID | None = None
    is_principal: bool = False
    finance_scope: FinanceScope = "own"
    can_give_advance: bool = False
    is_coordinator: bool = False
    scopes: list[ScopeIn] = []


class MemberPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str | None = Field(default=None, min_length=1)
    parent_member_id: UUID | None = None
    finance_scope: FinanceScope | None = None
    can_give_advance: bool | None = None
    is_coordinator: bool | None = None
    active: bool | None = None
    scopes: list[ScopeIn] | None = None  # replaces the whole set


class ScopeOut(BaseModel):
    block_id: str | None
    area_id: str | None


class MemberOut(BaseModel):
    member_id: UUID
    ac_id: str
    user_id: str
    display_name: str
    parent_member_id: UUID | None
    is_principal: bool
    finance_scope: str
    can_give_advance: bool
    is_coordinator: bool
    active: bool
    created_at: datetime
    deactivated_at: datetime | None
    scopes: list[ScopeOut]
    telegram_linked: bool
    telegram_linked_at: datetime | None
    last_seen_at: datetime | None = None        # last message to the bot (Telegram)
    pin_set: bool = False
    pin_locked_at: datetime | None = None


class LinkTokenOut(BaseModel):
    token: str
    deep_link: str
    expires_at: datetime


def _out(row: dict) -> MemberOut:
    row = dict(row)
    pin_set = row.pop("pin_set_at", None) is not None
    return MemberOut(**row, telegram_linked=row["telegram_linked_at"] is not None, pin_set=pin_set)


def _scopes(items: list[ScopeIn]) -> list[service.Scope]:
    return [service.Scope(block_id=s.block_id, area_id=s.area_id) for s in items]


def _audit_member(conn, ctx: AdminContext, action: str, row: dict, data: dict) -> None:
    """admin_audit for team changes made through the API. Names are not copied into the audit data."""
    audit(conn, ctx.ac.ac_id, action, actor_user_id=ctx.user.user_id, target_member_id=row["member_id"],
          data={k: (str(v) if isinstance(v, UUID) else v) for k, v in data.items()})


def _created_data(row: dict, via: str) -> dict:
    return {"via": via, "user_id": row["user_id"], "parent_member_id": row["parent_member_id"],
            "is_principal": row["is_principal"], "finance_scope": row["finance_scope"],
            "can_give_advance": row["can_give_advance"], "is_coordinator": row["is_coordinator"],
            "scopes": row["scopes"]}


def _http(e: TeamError) -> HTTPException:
    status = 404 if isinstance(e, NotFound) else 409 if isinstance(e, Conflict) else 422
    return HTTPException(status, str(e))


@router.get("/members", response_model=list[MemberOut])
def list_members(ctx: AdminContext = Depends(require_admin), state=Depends(get_state)):
    with transaction(state.pool) as conn:
        return [_out(r) for r in service.list_members(conn, ctx.ac.ac_id)]


@router.post("/members", response_model=MemberOut, status_code=201)
def create_member(body: MemberCreate, ctx: AdminContext = Depends(require_admin), state=Depends(get_state),
                  key: str | None = Depends(idem.key_header)):
    who = dict(user_id=ctx.user.user_id, route="POST /members")
    try:
        with transaction(state.pool) as conn:
            replay = idem.claim(conn, key, body=body.model_dump(mode="json"), **who)
            if replay is not None:
                return replay
            row = service.create_member(
                conn,
                state.core,
                ctx.ac.ac_id,
                user_id=body.user_id,
                display_name=body.display_name,
                parent_member_id=body.parent_member_id,
                is_principal=body.is_principal,
                finance_scope=body.finance_scope,
                can_give_advance=body.can_give_advance,
                is_coordinator=body.is_coordinator,
                scopes=_scopes(body.scopes),
            )
            _audit_member(conn, ctx, "member_created", row, _created_data(row, "api"))
            return idem.store(conn, key, status_code=201, response=_out(row).model_dump(mode="json"), **who)
    except TeamError as e:
        raise _http(e) from None


class ImportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    csv: str = Field(min_length=1)


class _RollBack(Exception):
    def __init__(self, result: csv_import.ImportResult):
        self.result = result


@router.post("/members/import")
def import_members(body: ImportIn, dry_run: bool = True, ctx: AdminContext = Depends(require_admin),
                   state=Depends(get_state), key: str | None = Depends(idem.key_header)):
    """Create members from a CSV (columns in pulse/team/csv_import.py). All or nothing: a file with any
    bad row imports nothing, and every row comes back with its result. `dry_run` (the default) checks
    without keeping anything; send dry_run=false to import."""
    try:
        rows = csv_import.parse(body.csv)
    except csv_import.ImportFileError as e:
        raise HTTPException(422, str(e)) from None
    who = dict(user_id=ctx.user.user_id, route="POST /members/import")
    try:
        with transaction(state.pool) as conn:
            # a dry run rolls back, so it never uses up the key; only a real import is remembered
            replay = idem.claim(conn, key, body={"csv": body.csv, "dry_run": dry_run}, **who)
            if replay is not None:
                return replay
            result = csv_import.run(conn, state.core, ctx.ac.ac_id, rows)
            if dry_run or not result.ok:
                raise _RollBack(result)
            for r in result.rows:
                member = service.get_member(conn, ctx.ac.ac_id, UUID(r.member_id))
                _audit_member(conn, ctx, "member_created", member, _created_data(member, "csv"))
            audit(conn, ctx.ac.ac_id, "members_imported", actor_user_id=ctx.user.user_id,
                  data={"count": len(result.rows), "user_ids": [r.user_id for r in result.rows]})
            return idem.store(conn, key, status_code=200, response=csv_import.as_dict(result, imported=True), **who)
    except _RollBack as rb:
        return csv_import.as_dict(rb.result, imported=False)


@router.patch("/members/{member_id}", response_model=MemberOut)
def update_member(
    member_id: UUID, body: MemberPatch, ctx: AdminContext = Depends(require_admin), state=Depends(get_state)
):
    changes = {k: getattr(body, k) for k in body.model_fields_set}
    for k in ("display_name", "finance_scope", "can_give_advance", "is_coordinator", "active", "scopes"):
        if k in changes and changes[k] is None:
            raise HTTPException(422, f"{k} cannot be null")
    if "scopes" in changes:
        changes["scopes"] = _scopes(body.scopes)
    try:
        with transaction(state.pool) as conn:
            row = service.update_member(conn, state.core, ctx.ac.ac_id, member_id, changes)
            _audit_member(conn, ctx, "member_changed", row, {
                "fields": sorted(changes),
                **{k: v for k, v in changes.items() if k not in ("display_name", "scopes")},
                **({"scopes": row["scopes"]} if "scopes" in changes else {})})
    except TeamError as e:
        raise _http(e) from None
    return _out(row)


@router.post("/members/{member_id}/link-token", response_model=LinkTokenOut, status_code=201)
def create_link_token(member_id: UUID, ctx: AdminContext = Depends(require_admin), state=Depends(get_state)):
    try:
        with transaction(state.pool) as conn:
            issued = linking.create_link_token(
                conn, ctx.ac.ac_id, member_id, ttl=timedelta(hours=state.settings.link_token_ttl_hours)
            )
    except TeamError as e:
        raise _http(e) from None
    deep_link = f"https://t.me/{state.settings.telegram_bot_username}?start={issued.token}"
    return LinkTokenOut(token=issued.token, deep_link=deep_link, expires_at=issued.expires_at)


@router.delete("/members/{member_id}/link", status_code=204)
def unlink(member_id: UUID, ctx: AdminContext = Depends(require_admin), state=Depends(get_state)):
    try:
        with transaction(state.pool) as conn:
            linking.unlink(conn, ctx.ac.ac_id, member_id)
    except TeamError as e:
        raise _http(e) from None
    return Response(status_code=204)


@router.post("/members/{member_id}/pin/unlock", status_code=204)
def pin_unlock(member_id: UUID, ctx: AdminContext = Depends(require_admin), state=Depends(get_state)):
    """Lift a PIN lock (three wrong entries); the PIN stays. Audited."""
    from pulse.team import pin as pins

    with transaction(state.pool) as conn:
        if not pins.unlock(conn, ctx.ac.ac_id, member_id, actor_user_id=ctx.user.user_id):
            raise HTTPException(404, "member not found")
    return Response(status_code=204)


@router.post("/members/{member_id}/pin/reset", status_code=204)
def pin_reset(member_id: UUID, ctx: AdminContext = Depends(require_admin), state=Depends(get_state)):
    """Remove the PIN and lift any lock; the member sets a new PIN in the bot. Audited."""
    from pulse.team import pin as pins

    with transaction(state.pool) as conn:
        if not pins.reset(conn, ctx.ac.ac_id, member_id, actor_user_id=ctx.user.user_id):
            raise HTTPException(404, "member not found")
    return Response(status_code=204)
