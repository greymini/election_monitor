"""Election register (LLD slice 4): windows, statutory-head mapping, register by head, review list
and the logged export. Finance scope `all` throughout (the treasurer and the principal)."""
from __future__ import annotations

import csv
import io
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from pulse.api.deps import get_state
from pulse.api.ledger_auth import LedgerCaller, at_least
from pulse.api.routers.ledger import http_error, plain
from pulse.db import transaction
from pulse.ledger import register as reg
from pulse.ledger import service as ledger
from pulse.ledger.errors import LedgerError

router = APIRouter(prefix="/acs/{ac_number}/field")


class WindowBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    starts_on: date
    ends_on: date
    ceiling_amount: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=2)
    cash_limit: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=2)
    event_id: str | None = Field(default=None, max_length=100)


class HeadBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    statutory_head: str | None = Field(default=None, max_length=reg.HEAD_MAX)


def _run(state, fn):
    try:
        with transaction(state.pool) as conn:
            return plain(fn(conn))
    except LedgerError as e:
        raise http_error(e) from None


@router.get("/election-windows")
def list_windows(caller: LedgerCaller = Depends(at_least("all")), state=Depends(get_state)):
    return _run(state, lambda conn: {"windows": reg.windows(conn, caller.ac.ac_id)})


@router.post("/election-windows", status_code=201)
def create_window(body: WindowBody, caller: LedgerCaller = Depends(at_least("all")), state=Depends(get_state)):
    return _run(state, lambda conn: reg.create_window(conn, caller.ac.ac_id, **body.model_dump(),
                                                      actor_user_id=caller.user.user_id,
                                                      actor_member_id=caller.viewer.member_id))


@router.put("/election-windows/{window_id}")
def update_window(window_id: int, body: WindowBody, caller: LedgerCaller = Depends(at_least("all")),
                  state=Depends(get_state)):
    return _run(state, lambda conn: reg.update_window(conn, caller.ac.ac_id, window_id, **body.model_dump(),
                                                      actor_user_id=caller.user.user_id,
                                                      actor_member_id=caller.viewer.member_id))


@router.delete("/election-windows/{window_id}", status_code=204)
def delete_window(window_id: int, caller: LedgerCaller = Depends(at_least("all")), state=Depends(get_state)):
    _run(state, lambda conn: reg.delete_window(conn, caller.ac.ac_id, window_id, actor_user_id=caller.user.user_id,
                                               actor_member_id=caller.viewer.member_id))
    return Response(status_code=204)


@router.get("/statutory-heads")
def list_heads(caller: LedgerCaller = Depends(at_least("all")), state=Depends(get_state)):
    """Every bucket with its statutory head; NULL means not mapped (the presets ship unmapped)."""
    return _run(state, lambda conn: {"buckets": reg.heads(conn)})


@router.put("/buckets/{bucket_id}/statutory-head")
def set_head(bucket_id: int, body: HeadBody, caller: LedgerCaller = Depends(at_least("all")),
             state=Depends(get_state)):
    if caller.user.role != "admin":
        raise HTTPException(403, "the statutory mapping is shared by every AC: admin only")
    return _run(state, lambda conn: reg.set_head(conn, caller.ac.ac_id, bucket_id, body.statutory_head,
                                                 actor_user_id=caller.user.user_id,
                                                 actor_member_id=caller.viewer.member_id))


@router.get("/spend/register")
def register(window_id: int, caller: LedgerCaller = Depends(at_least("all")), state=Depends(get_state)):
    return _run(state, lambda conn: reg.register(conn, caller.ac.ac_id, window_id))


@router.get("/spend/register/review")
def review(window_id: int, caller: LedgerCaller = Depends(at_least("all")), state=Depends(get_state)):
    return _run(state, lambda conn: {"items": reg.review(conn, caller.ac.ac_id, window_id)})


@router.get("/spend/register/export.csv")
def export(window_id: int, reason: str = Query(..., min_length=3, max_length=500),
           caller: LedgerCaller = Depends(at_least("all")), state=Depends(get_state)):
    """By statutory head; entries without a mapping are exported under 'unmapped', never dropped.
    Logged in field.txn_audit with the reason, like the ledger export."""
    try:
        with transaction(state.pool) as conn:
            rows = reg.export_rows(conn, caller.ac.ac_id, window_id)
            ledger.record_export(conn, caller.viewer.member_id, reason=reason,
                                 filters={"register_window_id": window_id}, row_count=len(rows))
    except LedgerError as e:
        raise http_error(e) from None
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(reg.EXPORT_COLUMNS)
    for r in rows:
        r = dict(r, flagged=";".join(r["flagged"]))
        w.writerow(["" if r.get(c) is None else r[c] for c in reg.EXPORT_COLUMNS])
    name = f"register-{caller.ac.ac_number}-window-{window_id}.csv"
    return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})
