"""Ledger API (LLD §9): buckets, entries, state changes and the logged CSV export.

Authorisation: pulse/api/ledger_auth.py (member row and finance scope); rows are narrowed by
finance scope inside the SQL (pulse/ledger/scope.py); who may change what is decided by the ledger
service. Amounts go out as strings, never JSON floats.
"""
from __future__ import annotations

import csv
import io
from datetime import date
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from pulse.api import idempotency as idem
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from pulse import settings_ac
from pulse.api.deps import get_state
from pulse.api.ledger_auth import LedgerCaller, at_least
from pulse.bot import flow_ack
from pulse.bot.ui import Ctx
from pulse.common.clock import IST, utcnow
from pulse.db import transaction
from pulse.ledger import queries
from pulse.ledger import service as ledger
from pulse.ledger.errors import Forbidden, LedgerError, NotFound

router = APIRouter(prefix="/acs/{ac_number}/field")

Kind = Literal["expense", "advance", "return", "inflow", "adjustment"]
Money = Field(gt=0, max_digits=12, decimal_places=2)


def plain(value: Any) -> Any:
    """JSON-safe without floats: Decimal → str, recursively."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    return value


def http_error(e: LedgerError) -> HTTPException:
    status = 404 if isinstance(e, NotFound) else 403 if isinstance(e, Forbidden) else 422
    return HTTPException(status, str(e))


def ac_limits(state, conn, ac_id: str):
    """This AC's ledger limits: environment defaults overridden by field.ac_setting."""
    return settings_ac.effective(conn, ac_id, settings_ac.defaults_from(state.settings)).ledger


def _ctx(state, conn, ac_id: str) -> Ctx:
    return Ctx(conn=conn, client=state.telegram, core=state.core, limits=ac_limits(state, conn, ac_id), now=utcnow())


# ---- buckets ----------------------------------------------------------------------------------

@router.get("/buckets")
def list_buckets(caller: LedgerCaller = Depends(at_least("own")), state=Depends(get_state)):
    with transaction(state.pool) as conn:
        rows = conn.execute(
            """SELECT bucket_id, parent_id, code, name_hi, name_en, statutory_head, sensitive, ask_reach, sort
               FROM field.exp_bucket WHERE active AND (ac_id IS NULL OR ac_id = %s) ORDER BY sort, bucket_id""",
            (caller.ac.ac_id,),
        ).fetchall()
    tops = [dict(r, subheads=[]) for r in rows if r["parent_id"] is None]
    by_id = {t["bucket_id"]: t for t in tops}
    for r in rows:
        if r["parent_id"] is not None and r["parent_id"] in by_id:
            by_id[r["parent_id"]]["subheads"].append(dict(r))
    return tops


# ---- entries ----------------------------------------------------------------------------------

def _filters(date_from: date | None = None, date_to: date | None = None, kind: Kind | None = None,
             bucket_id: int | None = None, block_id: str | None = None, area_id: str | None = None,
             member_id: UUID | None = None,
             flag: Literal["large", "cash_over_limit", "sensitive_in_window", "backdated", "ack_overdue"] | None = None,
             status: Literal["posted", "pending_ack", "disputed", "void"] | None = None) -> queries.TxnFilter:
    return queries.TxnFilter(date_from=date_from, date_to=date_to, kind=kind, bucket_id=bucket_id,
                             block_id=block_id, area_id=area_id, member_id=member_id, flag=flag, status=status)


@router.get("/txns")
def list_txns(f: queries.TxnFilter = Depends(_filters), limit: int = Query(50, ge=1, le=queries.MAX_PAGE),
              offset: int = Query(0, ge=0), caller: LedgerCaller = Depends(at_least("own")), state=Depends(get_state)):
    with transaction(state.pool) as conn:
        rows, total = queries.list_txns(conn, caller.viewer, f, limit=limit, offset=offset)
    return {"items": plain(rows), "total": total, "limit": limit, "offset": offset}


@router.get("/txns/summary")
def txns_summary(f: queries.TxnFilter = Depends(_filters),
                 grouping: Literal["day", "bucket", "member"] = "day",
                 caller: LedgerCaller = Depends(at_least("own")), state=Depends(get_state)):
    """Figures for the ledger screen over every row the filter matches (GET /txns pages the rows):
    spend total and count, largest expense, spend by bucket, and rows and spend per group."""
    with transaction(state.pool) as conn:
        return plain(queries.ledger_summary(conn, caller.viewer, f, grouping))


EXPORT_COLUMNS = ("txn_no", "kind", "status", "amount", "txn_date", "from_name", "to_name", "top_bucket_code",
                  "bucket_code", "custom_label", "block_id", "area_id", "fund_source", "pay_mode", "payee_kind",
                  "payee_label", "reach_count", "reach_unit", "remark", "in_election_window", "counts_to_ceiling",
                  "suggest_source", "suggest_conf", "flagged", "void_reason", "created_by_name", "created_via",
                  "created_at_ist")


@router.get("/txns/export.csv")
def export_csv(reason: str = Query(..., min_length=3, max_length=500), f: queries.TxnFilter = Depends(_filters),
               caller: LedgerCaller = Depends(at_least("all")), state=Depends(get_state)):
    try:
        with transaction(state.pool) as conn:
            rows = queries.export_txns(conn, caller.viewer, f)
            filters = {k: (str(v) if v is not None else None) for k, v in vars(f).items()}
            ledger.record_export(conn, caller.viewer.member_id, reason=reason, filters=filters, row_count=len(rows))
    except LedgerError as e:
        raise http_error(e) from None
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(EXPORT_COLUMNS)
    for r in rows:
        r = dict(r, created_at_ist=r["created_at"].astimezone(IST).isoformat(), flagged=";".join(r["flagged"]))
        w.writerow(["" if r.get(c) is None else r[c] for c in EXPORT_COLUMNS])
    name = f"ledger-{caller.ac.ac_number}-{date.today().isoformat()}.csv"
    return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


class TxnCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Kind
    amount: Decimal = Money
    txn_date: date | None = None
    from_member_id: UUID | None = None       # expense on someone's behalf (finance all); adjustment
    to_member_id: UUID | None = None         # advance, return (optional: most recent giver), inflow, adjustment
    bucket_id: int | None = None
    custom_label: str | None = None
    block_id: str | None = None
    area_id: str | None = None
    fund_source: str | None = None
    pay_mode: str | None = None
    payee_kind: str | None = None
    payee_label: str | None = None
    reach_count: int | None = None
    reach_unit: str | None = None
    remark: str | None = None
    report_id: UUID | None = None            # expense only: the report or issue the spend was for
    issue_id: UUID | None = None


def _create(conn, state, caller: LedgerCaller, body: TxnCreate, limits) -> dict[str, Any]:
    me = caller.viewer.member_id
    if body.kind != "expense" and (body.report_id or body.issue_id):
        raise HTTPException(422, "report_id and issue_id apply to expenses only")
    if body.kind == "expense":
        if body.bucket_id is None:
            raise HTTPException(422, "bucket_id is required for an expense")
        return ledger.create_expense(conn, state.core, me, ledger.ExpenseInput(
            amount=body.amount, bucket_id=body.bucket_id, txn_date=body.txn_date, block_id=body.block_id,
            area_id=body.area_id, fund_source=body.fund_source, pay_mode=body.pay_mode,
            custom_label=body.custom_label, remark=body.remark, payee_kind=body.payee_kind,
            payee_label=body.payee_label, reach_count=body.reach_count, reach_unit=body.reach_unit,
            suggest_source="user", created_via="web", report_id=body.report_id, issue_id=body.issue_id),
            limits=limits, payer_member_id=body.from_member_id)
    if body.kind == "advance":
        if body.to_member_id is None:
            raise HTTPException(422, "to_member_id is required for an advance")
        return ledger.create_advance(conn, state.core, me, to_member_id=body.to_member_id, amount=body.amount,
                                     txn_date=body.txn_date, pay_mode=body.pay_mode, remark=body.remark,
                                     created_via="web", limits=limits)
    if body.kind == "return":
        return ledger.create_return(conn, state.core, me, amount=body.amount, to_member_id=body.to_member_id,
                                    remark=body.remark, pay_mode=body.pay_mode, created_via="web", limits=limits)
    if body.kind == "inflow":
        if body.to_member_id is None or body.fund_source is None:
            raise HTTPException(422, "to_member_id and fund_source are required for an inflow")
        return ledger.create_inflow(conn, state.core, me, to_member_id=body.to_member_id, amount=body.amount,
                                    fund_source=body.fund_source, txn_date=body.txn_date,
                                    pay_mode=body.pay_mode, remark=body.remark, limits=limits)
    return ledger.create_adjustment(conn, state.core, me, amount=body.amount, remark=body.remark or "",
                                    from_member_id=body.from_member_id, to_member_id=body.to_member_id,
                                    txn_date=body.txn_date, limits=limits)


@router.post("/txns", status_code=201)
def create_txn(body: TxnCreate, caller: LedgerCaller = Depends(at_least("own")), state=Depends(get_state),
               key: str | None = Depends(idem.key_header)):
    who = dict(user_id=caller.user.user_id, route="POST /txns")
    try:
        with transaction(state.pool) as conn:
            replay = idem.claim(conn, key, body=body.model_dump(mode="json"), **who)
            if replay is not None:
                return replay
            row = _create(conn, state, caller, body, ac_limits(state, conn, caller.viewer.ac_id))
            notified = None
            if row["kind"] in ("advance", "return"):
                notified = flow_ack.request_ack(_ctx(state, conn, caller.viewer.ac_id), row)
            # "queued": the acknowledgement card will be sent by the worker; "not_linked": nobody to send it to
            return idem.store(conn, key, status_code=201, response={"txn": plain(row), "receiver_message": notified}, **who)
    except LedgerError as e:
        raise http_error(e) from None


class _Preview(Exception):
    def __init__(self, row: dict[str, Any]):
        self.row = row


@router.post("/txns/preview")
def preview_txn(body: TxnCreate, caller: LedgerCaller = Depends(at_least("own")), state=Depends(get_state)):
    """The same checks and flags as saving, without saving: the web form shows the bot's warnings
    (sensitive bucket in the election window, cash over the window's limit, large amount) before the
    one extra confirm. Runs the real service in a savepoint and rolls it back; nobody is notified."""
    try:
        with transaction(state.pool) as conn:
            try:
                with conn.transaction():
                    raise _Preview(_create(conn, state, caller, body, ac_limits(state, conn, caller.viewer.ac_id)))
            except _Preview as p:
                row = p.row
    except LedgerError as e:
        raise http_error(e) from None
    flags = list(row["flagged"] or [])
    return plain({"flags": flags, "warnings": [f for f in flags if f in ledger.WARNING_FLAGS],
                  "in_election_window": row["in_election_window"], "status": row["status"],
                  "fund_source": row["fund_source"], "pay_mode": row["pay_mode"], "txn_date": row["txn_date"]})


@router.get("/txns/{txn_id}")
def get_txn(ac_number: int, txn_id: UUID, caller: LedgerCaller = Depends(at_least("own")), state=Depends(get_state)):
    """One entry with everything the drawer shows: fields, audit trail, follow-up, linked report and
    issue, and a signed 5-minute link to its receipt. Finance scope applies in the query."""
    from pulse.api import media_tokens

    with transaction(state.pool) as conn:
        row = queries.get_txn(conn, caller.viewer, txn_id)
        if row is None:
            raise HTTPException(404, "entry not found")
        audit = conn.execute(
            """SELECT a.at, a.action, a.old, a.new, a.note, m.display_name AS actor_name
               FROM field.txn_audit a LEFT JOIN field.member m ON m.member_id = a.actor_member_id
               WHERE a.txn_id = %s ORDER BY a.audit_id""", (txn_id,)).fetchall()
        followup = conn.execute(
            "SELECT due_at, asked_at, reminded_at, answer, answered_at, photo_asset_id IS NOT NULL AS has_photo "
            "FROM field.txn_followup WHERE txn_id = %s", (txn_id,)).fetchone()
        report = conn.execute("SELECT report_id, report_no, title, status FROM field.report WHERE report_id = %s",
                              (row["report_id"],)).fetchone() if row["report_id"] else None
        issue = conn.execute("SELECT issue_id, issue_no, title, status FROM field.issue WHERE issue_id = %s",
                             (row["issue_id"],)).fetchone() if row["issue_id"] else None
        receipt = None
        if row["receipt_asset_id"]:
            a = conn.execute("SELECT asset_id, kind, mime, status FROM field.media_asset WHERE asset_id = %s",
                             (row["receipt_asset_id"],)).fetchone()
            if a is not None:
                receipt = dict(a)
                if a["status"] == "stored":
                    token = media_tokens.issue(state.settings.jwt_secret, caller.viewer.member_id, txn_id, a["asset_id"],
                                               now=utcnow())
                    receipt["url"] = f"/acs/{ac_number}/field/txns/{txn_id}/receipt/{a['asset_id']}?token={token}"
    return plain({"txn": row, "audit": audit, "followup": followup, "report": report, "issue": issue,
                  "receipt": receipt})


@router.get("/txns/{txn_id}/receipt/{asset_id}")
def get_receipt(ac_number: int, txn_id: UUID, asset_id: UUID, token: str, state=Depends(get_state)):
    """The receipt photo, through a signed 5-minute link (images cannot send a bearer header). The
    viewer must still see the entry under their finance scope."""
    from pulse.api import media_tokens
    from pulse.core_adapter import ac_by_number
    from pulse.ledger import links

    try:
        member_id = media_tokens.verify(state.settings.jwt_secret, token, txn_id, asset_id)
    except media_tokens.BadToken:
        raise HTTPException(403, "link invalid or expired") from None
    with transaction(state.pool) as conn:
        viewer = links.viewer_for(conn, member_id)
        ac = ac_by_number(state.core, ac_number)
        row = queries.get_txn(conn, viewer, txn_id) if viewer and ac and viewer.ac_id == ac.ac_id else None
        if row is None or row["receipt_asset_id"] != asset_id:
            raise HTTPException(404, "not found")
        a = conn.execute("SELECT * FROM field.media_asset WHERE asset_id = %s", (asset_id,)).fetchone()
    if a is None or a["status"] != "stored":
        raise HTTPException(410 if a is not None and a["status"] == "purged" else 404, "not found")
    return Response(state.storage.get(a["storage_key"]), media_type=a["mime"] or "application/octet-stream",
                    headers={"Cache-Control": "private, no-store", "Content-Disposition": "inline",
                             "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff"})


class Note(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str | None = None


class DisputeBody(Note):
    reason: Literal["amount_differs", "not_received"]


class VoidBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str | None = None


class ReclassifyBody(Note):
    bucket_id: int
    custom_label: str | None = None


class CeilingBody(Note):
    counts_to_ceiling: bool | None


def _act(state, caller: LedgerCaller, fn):
    try:
        with transaction(state.pool) as conn:
            row = fn(conn)
    except LedgerError as e:
        raise http_error(e) from None
    return {"txn": plain(row)}


@router.post("/txns/{txn_id}/ack")
def ack(txn_id: UUID, body: Note | None = None, caller: LedgerCaller = Depends(at_least("own")), state=Depends(get_state)):
    def do(conn):
        row = ledger.acknowledge(conn, txn_id, caller.viewer.member_id, note=body.note if body else None)
        flow_ack.tell_ack(_ctx(state, conn, caller.viewer.ac_id), row, caller.viewer.member_id)
        return row
    return _act(state, caller, do)


@router.post("/txns/{txn_id}/dispute")
def dispute(txn_id: UUID, body: DisputeBody, caller: LedgerCaller = Depends(at_least("own")), state=Depends(get_state)):
    def do(conn):
        row = ledger.dispute(conn, txn_id, caller.viewer.member_id, body.reason, note=body.note)
        flow_ack.tell_dispute(_ctx(state, conn, caller.viewer.ac_id), row, body.reason, caller.viewer.member_id)
        return row
    return _act(state, caller, do)


@router.post("/txns/{txn_id}/void")
def void(txn_id: UUID, body: VoidBody, caller: LedgerCaller = Depends(at_least("own")), state=Depends(get_state)):
    return _act(state, caller, lambda conn: ledger.void(conn, txn_id, caller.viewer.member_id, body.reason,
                                                        limits=ac_limits(state, conn, caller.viewer.ac_id)))


@router.post("/txns/{txn_id}/reclassify")
def reclassify(txn_id: UUID, body: ReclassifyBody, caller: LedgerCaller = Depends(at_least("own")),
               state=Depends(get_state)):
    return _act(state, caller, lambda conn: ledger.reclassify(conn, txn_id, caller.viewer.member_id, body.bucket_id,
                                                              body.custom_label, limits=ac_limits(state, conn, caller.viewer.ac_id),
                                                              note=body.note))


@router.post("/txns/{txn_id}/ceiling")
def ceiling(txn_id: UUID, body: CeilingBody, caller: LedgerCaller = Depends(at_least("all")), state=Depends(get_state)):
    return _act(state, caller, lambda conn: ledger.set_ceiling(conn, txn_id, caller.viewer.member_id,
                                                               body.counts_to_ceiling, note=body.note))
