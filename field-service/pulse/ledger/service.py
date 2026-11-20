"""The ledger service: the only code that writes field.money_txn (LLD §3.2).

Append-only. An entry is never edited: it is voided with a reason. The one permitted change is
bucket re-classification, written to field.txn_audit. Every function runs inside the caller's
transaction.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from pulse.common import pii
from pulse.common.clock import today_ist, utcnow
from pulse.common.text import normalise
from pulse.config import LedgerLimits
from pulse.core_adapter import CoreAdapter
from pulse.ledger import buckets, followup
from pulse.ledger.errors import Forbidden, Invalid, NotFound, PiiRejected
from pulse.ledger.parse_amount import MAX_AMOUNT
from pulse.metrics.ledger import member_balance_sql
from pulse.team.tree import is_in_subtree

FUND_SOURCES = ("own", "family_business", "party", "supporter_cash", "supporter_in_kind", "advance",
                "own_pocket_reimbursable", "other")
PAY_MODES = ("cash", "upi", "bank_transfer", "cheque", "credit", "in_kind")
PAYEE_KINDS = ("vendor", "individual", "institution", "group")
REACH_UNITS = ("person", "family", "village", "event")
SUGGEST_SOURCES = ("user", "rule", "llm")
CREATED_VIA = ("telegram", "web")
FLAG_ORDER = ("large", "cash_over_limit", "sensitive_in_window", "backdated")
WARNING_FLAGS = ("large", "cash_over_limit", "sensitive_in_window")   # shown before saving
CUSTOM_LABEL_MAX = 40
SERIES = {"expense": "E", "advance": "A", "return": "L", "inflow": "I", "adjustment": "J"}
_CENT = Decimal("0.01")


@dataclass(frozen=True)
class ExpenseInput:
    amount: Decimal
    bucket_id: int
    suggest_source: str = "user"
    suggest_conf: Decimal | None = None
    txn_date: date | None = None             # None = today (Asia/Kolkata)
    block_id: str | None = None
    area_id: str | None = None
    fund_source: str | None = None           # None = default by member
    pay_mode: str | None = None              # None = cash
    custom_label: str | None = None          # required for OTHER
    remark: str | None = None
    payee_kind: str | None = None
    payee_label: str | None = None
    reach_count: int | None = None
    reach_unit: str | None = None
    receipt_asset_id: UUID | None = None     # a stored or pending photo/PDF from field.media_asset
    created_via: str = "telegram"
    report_id: UUID | None = None            # the ground report this spend was for (impact, LLD §7)
    issue_id: UUID | None = None             # the issue this spend was for


@dataclass(frozen=True)
class Prepared:
    inp: ExpenseInput                        # with defaults filled in
    actor: dict[str, Any]
    bucket: dict[str, Any]
    window: dict[str, Any] | None            # the election window covering txn_date, if any
    flags: list[str]
    payer: dict[str, Any] | None = None      # from_member; the actor unless entered on someone's behalf

    @property
    def in_election_window(self) -> bool:
        return self.window is not None

    @property
    def warnings(self) -> list[str]:
        return [f for f in self.flags if f in WARNING_FLAGS]


# ---- helpers ----------------------------------------------------------------------------------

def _actor(conn: psycopg.Connection, member_id: UUID) -> dict[str, Any]:
    row = conn.execute(
        """SELECT member_id, ac_id, display_name, parent_member_id, is_principal, finance_scope, can_give_advance, active
           FROM field.member WHERE member_id = %s""",
        (member_id,),
    ).fetchone()
    if row is None:
        raise NotFound("member not found")
    if not row["active"]:
        raise Forbidden("member is not active")
    return row


def _screen(field: str, value: str | None) -> None:
    kinds = pii.screen(value)
    if kinds:
        raise PiiRejected(field, kinds)


def _clean_amount(amount: Any) -> Decimal:
    if isinstance(amount, float):
        raise Invalid("amount must be Decimal, never float")
    try:
        value = Decimal(amount)
    except (InvalidOperation, TypeError):
        raise Invalid("amount is not a number") from None
    if value <= 0:
        raise Invalid("amount must be greater than zero")
    if value != value.quantize(_CENT):
        raise Invalid("amount has more than two decimals")
    if value > MAX_AMOUNT:
        raise Invalid("amount too large")
    return value.quantize(_CENT)


def _election_window(conn: psycopg.Connection, ac_id: str, d: date) -> dict[str, Any] | None:
    return conn.execute(
        "SELECT * FROM field.ac_election_window WHERE ac_id = %s AND starts_on <= %s AND ends_on >= %s",
        (ac_id, d, d),
    ).fetchone()


def compute_flags(*, amount: Decimal, txn_date: date, today: date, pay_mode: str, sensitive: bool,
                  window: dict[str, Any] | None, limits: LedgerLimits) -> list[str]:
    flags: set[str] = set()
    if amount >= limits.large_entry:
        flags.add("large")
    if (today - txn_date).days > limits.backdated_days:
        flags.add("backdated")
    if window is not None:
        if pay_mode == "cash" and window["cash_limit"] is not None and amount > window["cash_limit"]:
            flags.add("cash_over_limit")
        if sensitive:
            flags.add("sensitive_in_window")
    return [f for f in FLAG_ORDER if f in flags]


def _check_area(core: CoreAdapter, ac_id: str, block_id: str | None, area_id: str | None) -> None:
    if area_id is not None:
        area = next((a for a in core.list_areas(ac_id) if a.area_id == area_id), None)
        if area is None:
            raise Invalid("unknown area for this AC")
        if block_id is not None and area.block_id != block_id:
            raise Invalid("area is not in that block")
    elif block_id is not None and block_id not in {b.block_id for b in core.list_blocks(ac_id)}:
        raise Invalid("unknown block for this AC")


def _clean_label(bucket: dict[str, Any], label: str | None) -> str | None:
    if bucket["top_code"] == buckets.OTHER_CODE:
        label = (label or "").strip()
        if not label:
            raise Invalid("custom_label is required for OTHER")
        if len(label) > CUSTOM_LABEL_MAX:
            raise Invalid(f"custom_label is longer than {CUSTOM_LABEL_MAX} characters")
        _screen("custom_label", label)
        return label
    if label:
        raise Invalid("custom_label is only for OTHER")
    return None


def _count_custom_label(conn: psycopg.Connection, ac_id: str, label: str, now: datetime) -> None:
    conn.execute(
        """INSERT INTO field.exp_custom_label (ac_id, label_norm, count, last_seen) VALUES (%s, %s, 1, %s)
           ON CONFLICT (ac_id, label_norm) DO UPDATE
           SET count = field.exp_custom_label.count + 1, last_seen = EXCLUDED.last_seen""",
        (ac_id, normalise(label), now),
    )


def next_txn_no(conn: psycopg.Connection, core: CoreAdapter, ac_id: str, kind: str) -> str:
    """Per-AC, per-series counter. The row lock taken by ON CONFLICT DO UPDATE serialises writers."""
    ac = next((a for a in core.list_acs() if a.ac_id == ac_id), None)
    if ac is None:
        raise Invalid("unknown AC")
    series = SERIES[kind]
    row = conn.execute(
        """INSERT INTO field.txn_counter (ac_id, series, last_no) VALUES (%s, %s, 1)
           ON CONFLICT (ac_id, series) DO UPDATE SET last_no = field.txn_counter.last_no + 1
           RETURNING last_no""",
        (ac_id, series),
    ).fetchone()
    return f"{ac.ac_number}-{series}-{row['last_no']:06d}"


def _may_change(actor: dict[str, Any], txn: dict[str, Any], limits: LedgerLimits, now: datetime) -> bool:
    """Creator within the undo window, or finance scope `all` in the same AC."""
    if actor["ac_id"] != txn["ac_id"]:
        return False
    if txn["created_by"] == actor["member_id"] and now - txn["created_at"] <= timedelta(minutes=limits.undo_minutes):
        return True
    return actor["finance_scope"] == "all"


def _lock_txn(conn: psycopg.Connection, txn_id: UUID) -> dict[str, Any]:
    txn = conn.execute("SELECT * FROM field.money_txn WHERE txn_id = %s FOR UPDATE", (txn_id,)).fetchone()
    if txn is None:
        raise NotFound("entry not found")
    return txn


# ---- expenses ---------------------------------------------------------------------------------

def prepare_expense(conn: psycopg.Connection, core: CoreAdapter, actor_member_id: UUID, inp: ExpenseInput, *,
                    limits: LedgerLimits, now: datetime | None = None, payer_member_id: UUID | None = None) -> Prepared:
    """Validate, fill defaults and compute flags without writing. The bot uses it to warn first.

    The payer is the actor, unless an actor with finance scope `all` enters it on someone's behalf
    (the office entering in bulk); created_by is always the actor.
    """
    now = now or utcnow()
    actor = _actor(conn, actor_member_id)
    if actor["finance_scope"] == "none":
        raise Forbidden("finance scope none cannot record expenses")
    payer = actor
    if payer_member_id is not None and payer_member_id != actor_member_id:
        if actor["finance_scope"] != "all":
            raise Forbidden("only finance scope all may enter an expense on someone else's behalf")
        payer = _actor(conn, payer_member_id)
        if payer["ac_id"] != actor["ac_id"]:
            raise NotFound("member not found")
    ac_id = actor["ac_id"]
    amount = _clean_amount(inp.amount)

    bucket = buckets.get_bucket(conn, inp.bucket_id, ac_id)
    if bucket is None:
        raise Invalid("unknown or inactive bucket")
    label = _clean_label(bucket, inp.custom_label)

    today = today_ist(now)
    txn_date = inp.txn_date or today
    if txn_date > today:
        raise Invalid("txn_date cannot be in the future")

    fund_source = inp.fund_source or ("own" if payer["is_principal"] else "advance")
    pay_mode = inp.pay_mode or "cash"
    if fund_source not in FUND_SOURCES:
        raise Invalid("unknown fund_source")
    if pay_mode not in PAY_MODES:
        raise Invalid("unknown pay_mode")
    if inp.payee_kind is not None and inp.payee_kind not in PAYEE_KINDS:
        raise Invalid("unknown payee_kind")
    if (inp.reach_count is None) != (inp.reach_unit is None):
        raise Invalid("reach_count and reach_unit go together")
    if inp.reach_count is not None and (inp.reach_count <= 0 or inp.reach_unit not in REACH_UNITS):
        raise Invalid("reach must be a positive count with a known unit")
    if inp.suggest_source not in SUGGEST_SOURCES:
        raise Invalid("unknown suggest_source")
    if inp.suggest_conf is not None and inp.suggest_source == "user":
        raise Invalid("suggest_conf belongs to machine suggestions only")
    if inp.created_via not in CREATED_VIA:
        raise Invalid("unknown created_via")
    _screen("remark", inp.remark)
    _screen("payee_label", inp.payee_label)
    _check_area(core, ac_id, inp.block_id, inp.area_id)
    if inp.receipt_asset_id is not None:
        asset = conn.execute("SELECT ac_id, kind, uploaded_by, status FROM field.media_asset WHERE asset_id = %s",
                             (inp.receipt_asset_id,)).fetchone()
        if (asset is None or asset["ac_id"] != ac_id or asset["kind"] not in ("photo", "document")
                or asset["uploaded_by"] not in (actor["member_id"], payer["member_id"]) or asset["status"] == "failed"):
            raise Invalid("receipt must be a photo or document sent by this member")

    _check_links(conn, actor, inp.report_id, inp.issue_id)

    window = _election_window(conn, ac_id, txn_date)
    flags = compute_flags(amount=amount, txn_date=txn_date, today=today, pay_mode=pay_mode,
                          sensitive=bucket["sensitive"], window=window, limits=limits)
    filled = replace(inp, amount=amount, txn_date=txn_date, fund_source=fund_source, pay_mode=pay_mode,
                     custom_label=label, remark=(inp.remark or "").strip() or None)
    return Prepared(inp=filled, actor=actor, bucket=bucket, window=window, flags=flags, payer=payer)


def _check_links(conn: psycopg.Connection, actor: dict[str, Any], report_id: UUID | None, issue_id: UUID | None) -> None:
    """A linked report or issue must be in the actor's AC and visible to them (report access rules)."""
    if report_id is None and issue_id is None:
        return
    from pulse.reports import access as report_access
    from pulse.reports import issues

    rv = report_access.viewer_for(conn, actor["member_id"])
    if report_id is not None and (rv is None or not report_access.can_see(conn, rv, report_id)):
        raise Invalid("report not found")
    if issue_id is not None:
        if rv is None:
            raise Invalid("issue not found")
        try:
            issues.get(conn, rv, issue_id)
        except issues.IssueNotFound:
            raise Invalid("issue not found") from None


def create_expense(conn: psycopg.Connection, core: CoreAdapter, actor_member_id: UUID, inp: ExpenseInput, *,
                   limits: LedgerLimits, now: datetime | None = None,
                   payer_member_id: UUID | None = None) -> dict[str, Any]:
    now = now or utcnow()
    p = prepare_expense(conn, core, actor_member_id, inp, limits=limits, now=now, payer_member_id=payer_member_id)
    i = p.inp
    ac_id = p.actor["ac_id"]
    txn_no = next_txn_no(conn, core, ac_id, "expense")
    row = conn.execute(
        """INSERT INTO field.money_txn (
               txn_no, ac_id, kind, amount, txn_date, from_member_id, to_member_id, bucket_id, custom_label,
               block_id, area_id, fund_source, pay_mode, payee_kind, payee_label, reach_count, reach_unit,
               remark, receipt_asset_id, in_election_window, suggest_source, suggest_conf, status, flagged,
               created_by, created_via, created_at, report_id, issue_id)
           VALUES (%s, %s, 'expense', %s, %s, %s, NULL, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                   'posted', %s, %s, %s, %s, %s, %s)
           RETURNING *""",
        (txn_no, ac_id, i.amount, i.txn_date, p.payer["member_id"], i.bucket_id, i.custom_label,
         i.block_id, i.area_id, i.fund_source, i.pay_mode, i.payee_kind, i.payee_label, i.reach_count,
         i.reach_unit, i.remark, i.receipt_asset_id, p.in_election_window, i.suggest_source, i.suggest_conf, p.flags,
         actor_member_id, i.created_via, now, i.report_id, i.issue_id),
    ).fetchone()
    if i.custom_label:
        _count_custom_label(conn, ac_id, i.custom_label, now)
    if followup.applies(p.bucket["top_code"]):
        followup.create_for(conn, row, days=limits.followup_days)
    return row


# ---- void and reclassify ----------------------------------------------------------------------

def void(conn: psycopg.Connection, txn_id: UUID, actor_member_id: UUID, reason: str | None = None, *,
         limits: LedgerLimits, now: datetime | None = None) -> dict[str, Any]:
    """Creator within the undo window ("undo"; for advances and returns only while still pending),
    otherwise finance scope `all` with a reason."""
    now = now or utcnow()
    actor = _actor(conn, actor_member_id)
    txn = _lock_txn(conn, txn_id)
    if txn["ac_id"] != actor["ac_id"]:
        raise NotFound("entry not found")
    if txn["status"] == "void":
        raise Invalid("entry is already void")
    is_undo = (txn["created_by"] == actor_member_id
               and now - txn["created_at"] <= timedelta(minutes=limits.undo_minutes)
               # a transfer can be undone only before the other side has answered
               and (txn["kind"] not in ("advance", "return") or txn["status"] == "pending_ack"))
    if is_undo:
        reason = (reason or "").strip() or "undo"
    elif actor["finance_scope"] == "all":
        reason = (reason or "").strip()
        if not reason:
            raise Invalid("a reason is required to void")
    else:
        raise Forbidden("only the creator within the undo window, or finance scope all, may void")
    row = conn.execute(
        """UPDATE field.money_txn SET status = 'void', void_reason = %s, voided_by = %s, voided_at = %s
           WHERE txn_id = %s RETURNING *""",
        (reason, actor_member_id, now, txn_id),
    ).fetchone()
    conn.execute(
        """INSERT INTO field.txn_audit (txn_id, at, actor_member_id, action, old, new, note)
           VALUES (%s, %s, %s, 'void', %s, %s, %s)""",
        (txn_id, now, actor_member_id, Jsonb({"status": txn["status"]}), Jsonb({"status": "void"}), reason),
    )
    followup.cancel(conn, txn_id, now=now)
    return row


def reclassify(conn: psycopg.Connection, txn_id: UUID, actor_member_id: UUID, new_bucket_id: int,
               custom_label: str | None = None, *, limits: LedgerLimits, now: datetime | None = None,
               note: str | None = None) -> dict[str, Any]:
    now = now or utcnow()
    actor = _actor(conn, actor_member_id)
    txn = _lock_txn(conn, txn_id)
    if txn["ac_id"] != actor["ac_id"]:
        raise NotFound("entry not found")
    if txn["kind"] != "expense":
        raise Invalid("only expenses have a bucket")
    if txn["status"] == "void":
        raise Invalid("a void entry cannot be re-classified")
    if not _may_change(actor, txn, limits, now):
        raise Forbidden("only the creator within the undo window, or finance scope all, may re-classify")
    bucket = buckets.get_bucket(conn, new_bucket_id, txn["ac_id"])
    if bucket is None:
        raise Invalid("unknown or inactive bucket")
    label = _clean_label(bucket, custom_label)

    flags = [f for f in txn["flagged"] if f != "sensitive_in_window"]
    if txn["in_election_window"] and bucket["sensitive"]:
        flags.append("sensitive_in_window")
    flags = [f for f in FLAG_ORDER if f in flags]
    row = conn.execute(
        """UPDATE field.money_txn SET bucket_id = %s, custom_label = %s, suggest_source = 'user',
               suggest_conf = NULL, flagged = %s
           WHERE txn_id = %s RETURNING *""",
        (new_bucket_id, label, flags, txn_id),
    ).fetchone()
    conn.execute(
        """INSERT INTO field.txn_audit (txn_id, at, actor_member_id, action, old, new, note)
           VALUES (%s, %s, %s, 'reclassify', %s, %s, %s)""",
        (txn_id, now, actor_member_id,
         Jsonb({"bucket_id": txn["bucket_id"], "custom_label": txn["custom_label"],
                "suggest_source": txn["suggest_source"],
                "suggest_conf": None if txn["suggest_conf"] is None else str(txn["suggest_conf"])}),
         Jsonb({"bucket_id": new_bucket_id, "custom_label": label, "suggest_source": "user"}), note),
    )
    if label:
        _count_custom_label(conn, txn["ac_id"], label, now)
    if followup.applies(bucket["top_code"]):
        followup.create_for(conn, row, days=limits.followup_days)
    else:
        followup.cancel(conn, txn_id, now=now)
    return row


# ---- transfers inside the team: advances and returns (LLD §5.2) -------------------------------

DISPUTE_REASONS = ("amount_differs", "not_received")


def _audit(conn: psycopg.Connection, *, txn_id: UUID | None, actor: UUID | None, action: str, now: datetime,
           old: dict | None = None, new: dict | None = None, note: str | None = None, ac_id: str | None = None) -> None:
    conn.execute(
        """INSERT INTO field.txn_audit (txn_id, ac_id, at, actor_member_id, action, old, new, note)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
        (txn_id, ac_id, now, actor, action, Jsonb(old) if old is not None else None,
         Jsonb(new) if new is not None else None, note),
    )


def _insert_transfer(conn: psycopg.Connection, core: CoreAdapter, *, kind: str, ac_id: str, amount: Decimal,
                     txn_date: date, from_member_id: UUID | None, to_member_id: UUID | None, status: str,
                     flags: list[str], created_by: UUID, created_via: str, now: datetime, pay_mode: str | None = None,
                     fund_source: str | None = None, remark: str | None = None) -> dict[str, Any]:
    window = _election_window(conn, ac_id, txn_date)
    return conn.execute(
        """INSERT INTO field.money_txn (txn_no, ac_id, kind, amount, txn_date, from_member_id, to_member_id,
               fund_source, pay_mode, remark, in_election_window, status, flagged, created_by, created_via, created_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *""",
        (next_txn_no(conn, core, ac_id, kind), ac_id, kind, amount, txn_date, from_member_id, to_member_id,
         fund_source, pay_mode, remark, window is not None, status, flags, created_by, created_via, now),
    ).fetchone()


def _transfer_basics(amount: Any, txn_date: date | None, pay_mode: str | None, remark: str | None,
                     created_via: str, now: datetime,
                     limits: LedgerLimits) -> tuple[Decimal, date, str, str | None, list[str]]:
    """Shared checks for non-expense entries. Only `large` and `backdated` apply: the cash and
    sensitive flags are expense rules."""
    amount = _clean_amount(amount)
    today = today_ist(now)
    txn_date = txn_date or today
    if txn_date > today:
        raise Invalid("txn_date cannot be in the future")
    pay_mode = pay_mode or "cash"
    if pay_mode not in PAY_MODES:
        raise Invalid("unknown pay_mode")
    if created_via not in CREATED_VIA:
        raise Invalid("unknown created_via")
    remark = (remark or "").strip() or None
    _screen("remark", remark)
    flags = compute_flags(amount=amount, txn_date=txn_date, today=today, pay_mode=pay_mode,
                          sensitive=False, window=None, limits=limits)
    return amount, txn_date, pay_mode, remark, flags


def _can_receive(conn: psycopg.Connection, giver: dict[str, Any], receiver_id: UUID) -> dict[str, Any]:
    """The receiver of an advance: active, same AC, not the giver, can see money, and in the giver's
    subtree unless the giver is the principal or has finance scope all."""
    if receiver_id == giver["member_id"]:
        raise Invalid("cannot give an advance to yourself")
    receiver = _actor(conn, receiver_id)
    if receiver["ac_id"] != giver["ac_id"]:
        raise NotFound("member not found")
    if receiver["finance_scope"] == "none":
        raise Invalid("the receiver has finance scope none and cannot hold money")
    if not (giver["is_principal"] or giver["finance_scope"] == "all") \
            and not is_in_subtree(conn, giver["member_id"], receiver_id):
        raise Forbidden("an advance can only go to someone in your own team")
    return receiver


def eligible_receivers(conn: psycopg.Connection, giver_member_id: UUID) -> list[dict[str, Any]]:
    """Who this member may give an advance to: direct reports first (most recently given first, then
    by name), then the rest of the subtree, or of the whole AC for the principal / finance all."""
    giver = _actor(conn, giver_member_id)
    everyone = bool(giver["is_principal"] or giver["finance_scope"] == "all")
    return conn.execute(
        """WITH RECURSIVE sub AS (
               SELECT member_id FROM field.member WHERE parent_member_id = %(g)s
               UNION
               SELECT m.member_id FROM field.member m JOIN sub ON m.parent_member_id = sub.member_id),
           last_given AS (
               SELECT to_member_id, max(created_at) AS at FROM field.money_txn
               WHERE kind = 'advance' AND from_member_id = %(g)s AND status <> 'void' GROUP BY 1)
           SELECT m.member_id, m.display_name, m.user_id,
                  (m.parent_member_id IS NOT DISTINCT FROM %(g)s) AS is_direct, lg.at AS last_given_at
           FROM field.member m LEFT JOIN last_given lg ON lg.to_member_id = m.member_id
           WHERE m.ac_id = %(ac)s AND m.active AND m.member_id <> %(g)s AND m.finance_scope <> 'none'
             AND (%(everyone)s OR m.member_id IN (SELECT member_id FROM sub))
           ORDER BY (m.parent_member_id IS NOT DISTINCT FROM %(g)s) DESC, lg.at DESC NULLS LAST,
                    m.display_name, m.member_id""",
        {"g": giver_member_id, "ac": giver["ac_id"], "everyone": everyone},
    ).fetchall()


def create_advance(conn: psycopg.Connection, core: CoreAdapter, giver_member_id: UUID, *, to_member_id: UUID,
                   amount: Any, txn_date: date | None = None, pay_mode: str | None = None, remark: str | None = None,
                   created_via: str = "telegram", limits: LedgerLimits, now: datetime | None = None) -> dict[str, Any]:
    """Money moved inside the team. Deducted from the giver at once; credited to the receiver only
    when they acknowledge (status pending_ack until then)."""
    now = now or utcnow()
    giver = _actor(conn, giver_member_id)
    if giver["finance_scope"] == "none" or not giver["can_give_advance"]:
        raise Forbidden("this member may not give advances")
    _can_receive(conn, giver, to_member_id)
    amount, txn_date, pay_mode, remark, flags = _transfer_basics(amount, txn_date, pay_mode, remark,
                                                                  created_via, now, limits)
    return _insert_transfer(conn, core, kind="advance", ac_id=giver["ac_id"], amount=amount, txn_date=txn_date,
                            from_member_id=giver_member_id, to_member_id=to_member_id, status="pending_ack",
                            flags=flags, created_by=giver_member_id, created_via=created_via, now=now,
                            pay_mode=pay_mode, remark=remark)


def givers_of(conn: psycopg.Connection, member_id: UUID) -> list[dict[str, Any]]:
    """Members who gave this member an acknowledged advance, most recent first: who money can be returned to."""
    return conn.execute(
        """SELECT m.member_id, m.display_name, max(t.created_at) AS last_at
           FROM field.money_txn t JOIN field.member m ON m.member_id = t.from_member_id
           WHERE t.kind = 'advance' AND t.status = 'posted' AND t.to_member_id = %s AND m.active
           GROUP BY m.member_id, m.display_name ORDER BY last_at DESC, m.display_name""",
        (member_id,),
    ).fetchall()


def create_return(conn: psycopg.Connection, core: CoreAdapter, returner_member_id: UUID, *, amount: Any,
                  to_member_id: UUID | None = None, remark: str | None = None, pay_mode: str | None = None,
                  created_via: str = "telegram", limits: LedgerLimits, now: datetime | None = None) -> dict[str, Any]:
    """Unspent money handed back to someone who gave this member an advance. The giver confirms it."""
    now = now or utcnow()
    # lock the returner so two returns at once cannot both pass the balance check
    conn.execute("SELECT 1 FROM field.member WHERE member_id = %s FOR UPDATE", (returner_member_id,))
    returner = _actor(conn, returner_member_id)
    if returner["finance_scope"] == "none":
        raise Forbidden("finance scope none cannot return money")
    givers = givers_of(conn, returner_member_id)
    if not givers:
        raise Invalid("nobody has given this member an advance")
    if to_member_id is None:
        to_member_id = givers[0]["member_id"]
    elif to_member_id not in {g["member_id"] for g in givers}:
        raise Invalid("money can only be returned to someone who gave you an advance")
    amount, txn_date, pay_mode, remark, flags = _transfer_basics(amount, None, pay_mode, remark,
                                                                  created_via, now, limits)
    balance = conn.execute(member_balance_sql(), {"member_id": returner_member_id}).fetchone()["balance"]
    if amount > balance:
        raise Invalid("cannot return more than the balance you hold")
    return _insert_transfer(conn, core, kind="return", ac_id=returner["ac_id"], amount=amount, txn_date=txn_date,
                            from_member_id=returner_member_id, to_member_id=to_member_id, status="pending_ack",
                            flags=flags, created_by=returner_member_id, created_via=created_via, now=now,
                            pay_mode=pay_mode, remark=remark)


def _answerable(conn: psycopg.Connection, txn_id: UUID, actor_member_id: UUID) -> dict[str, Any]:
    """The pending transfer, if the actor is its receiver or has finance scope all (row locked)."""
    actor = _actor(conn, actor_member_id)
    txn = _lock_txn(conn, txn_id)
    if txn["ac_id"] != actor["ac_id"]:
        raise NotFound("entry not found")
    if txn["kind"] not in ("advance", "return"):
        raise Invalid("only advances and returns are acknowledged")
    if txn["to_member_id"] != actor_member_id and actor["finance_scope"] != "all":
        raise Forbidden("only the receiver, or finance scope all, may answer this")
    if txn["status"] != "pending_ack":
        raise Invalid(f"entry is {txn['status']}, not waiting for acknowledgement")
    return txn


def acknowledge(conn: psycopg.Connection, txn_id: UUID, actor_member_id: UUID, *, note: str | None = None,
                now: datetime | None = None) -> dict[str, Any]:
    now = now or utcnow()
    txn = _answerable(conn, txn_id, actor_member_id)
    row = conn.execute("UPDATE field.money_txn SET status = 'posted' WHERE txn_id = %s RETURNING *",
                       (txn_id,)).fetchone()
    on_behalf = actor_member_id != txn["to_member_id"]
    _audit(conn, txn_id=txn_id, actor=actor_member_id, action="ack", now=now, old={"status": "pending_ack"},
           new={"status": "posted", "on_behalf_of": str(txn["to_member_id"]) if on_behalf else None}, note=note)
    return row


def dispute(conn: psycopg.Connection, txn_id: UUID, actor_member_id: UUID, reason: str, *,
            note: str | None = None, now: datetime | None = None) -> dict[str, Any]:
    now = now or utcnow()
    if reason not in DISPUTE_REASONS:
        raise Invalid(f"reason must be one of {DISPUTE_REASONS}")
    note = (note or "").strip() or None
    _screen("note", note)
    txn = _answerable(conn, txn_id, actor_member_id)
    row = conn.execute("UPDATE field.money_txn SET status = 'disputed' WHERE txn_id = %s RETURNING *",
                       (txn_id,)).fetchone()
    on_behalf = actor_member_id != txn["to_member_id"]
    _audit(conn, txn_id=txn_id, actor=actor_member_id, action="dispute", now=now, old={"status": "pending_ack"},
           new={"status": "disputed", "reason": reason,
                "on_behalf_of": str(txn["to_member_id"]) if on_behalf else None}, note=note)
    return row


# ---- treasurer entries: inflow, adjustment, ceiling (API only) --------------------------------

def _treasurer(conn: psycopg.Connection, actor_member_id: UUID) -> dict[str, Any]:
    actor = _actor(conn, actor_member_id)
    if actor["finance_scope"] != "all":
        raise Forbidden("finance scope all required")
    return actor


def _same_ac_member(conn: psycopg.Connection, actor: dict[str, Any], member_id: UUID | None) -> None:
    if member_id is not None and _actor(conn, member_id)["ac_id"] != actor["ac_id"]:
        raise NotFound("member not found")


def create_inflow(conn: psycopg.Connection, core: CoreAdapter, actor_member_id: UUID, *, to_member_id: UUID,
                  amount: Any, fund_source: str, txn_date: date | None = None, pay_mode: str | None = None,
                  remark: str | None = None, created_via: str = "web", limits: LedgerLimits,
                  now: datetime | None = None) -> dict[str, Any]:
    now = now or utcnow()
    actor = _treasurer(conn, actor_member_id)
    _same_ac_member(conn, actor, to_member_id)
    if fund_source not in FUND_SOURCES or fund_source == "advance":
        raise Invalid("fund_source must say where the money came from")
    amount, txn_date, pay_mode, remark, flags = _transfer_basics(amount, txn_date, pay_mode, remark,
                                                                  created_via, now, limits)
    return _insert_transfer(conn, core, kind="inflow", ac_id=actor["ac_id"], amount=amount, txn_date=txn_date,
                            from_member_id=None, to_member_id=to_member_id, status="posted", flags=flags,
                            created_by=actor_member_id, created_via=created_via, now=now, pay_mode=pay_mode,
                            fund_source=fund_source, remark=remark)


def create_adjustment(conn: psycopg.Connection, core: CoreAdapter, actor_member_id: UUID, *, amount: Any,
                      remark: str, from_member_id: UUID | None = None, to_member_id: UUID | None = None,
                      txn_date: date | None = None, created_via: str = "web", limits: LedgerLimits,
                      now: datetime | None = None) -> dict[str, Any]:
    now = now or utcnow()
    actor = _treasurer(conn, actor_member_id)
    if from_member_id is None and to_member_id is None:
        raise Invalid("an adjustment needs a from or a to member")
    _same_ac_member(conn, actor, from_member_id)
    _same_ac_member(conn, actor, to_member_id)
    if not (remark or "").strip():
        raise Invalid("an adjustment needs a remark")
    amount, txn_date, _, remark, flags = _transfer_basics(amount, txn_date, None, remark, created_via, now, limits)
    return _insert_transfer(conn, core, kind="adjustment", ac_id=actor["ac_id"], amount=amount, txn_date=txn_date,
                            from_member_id=from_member_id, to_member_id=to_member_id, status="posted",
                            flags=flags, created_by=actor_member_id, created_via=created_via, now=now,
                            remark=remark)


def set_ceiling(conn: psycopg.Connection, txn_id: UUID, actor_member_id: UUID, counts: bool | None, *,
                note: str | None = None, now: datetime | None = None) -> dict[str, Any]:
    """The treasurer decides whether an election-window expense counts to the statutory ceiling."""
    now = now or utcnow()
    actor = _treasurer(conn, actor_member_id)
    txn = _lock_txn(conn, txn_id)
    if txn["ac_id"] != actor["ac_id"]:
        raise NotFound("entry not found")
    if txn["kind"] != "expense" or not txn["in_election_window"]:
        raise Invalid("only expenses inside an election window count to the ceiling")
    if txn["status"] == "void":
        raise Invalid("a void entry cannot change")
    row = conn.execute("UPDATE field.money_txn SET counts_to_ceiling = %s WHERE txn_id = %s RETURNING *",
                       (counts, txn_id)).fetchone()
    _audit(conn, txn_id=txn_id, actor=actor_member_id, action="ceiling", now=now,
           old={"counts_to_ceiling": txn["counts_to_ceiling"]}, new={"counts_to_ceiling": counts}, note=note)
    return row


# ---- acknowledgement reminders (run by scripts/run_jobs.py; scheduled by the worker in 2b) -----

def record_reminder(conn: psycopg.Connection, txn_id: UUID, *, now: datetime | None = None) -> None:
    _audit(conn, txn_id=txn_id, actor=None, action="remind", now=now or utcnow())


def flag_ack_overdue(conn: psycopg.Connection, txn_id: UUID, *, now: datetime | None = None) -> bool:
    """Add `ack_overdue` to a still-pending transfer. Returns False if there was nothing to do."""
    now = now or utcnow()
    txn = _lock_txn(conn, txn_id)
    if txn["status"] != "pending_ack" or "ack_overdue" in txn["flagged"]:
        return False
    flags = [f for f in FLAG_ORDER + ("ack_overdue",) if f in txn["flagged"] or f == "ack_overdue"]
    conn.execute("UPDATE field.money_txn SET flagged = %s WHERE txn_id = %s", (flags, txn_id))
    _audit(conn, txn_id=txn_id, actor=None, action="flag", now=now, old={"flagged": txn["flagged"]},
           new={"flagged": flags})
    return True


def record_export(conn: psycopg.Connection, actor_member_id: UUID, *, reason: str, filters: dict[str, Any],
                  row_count: int, now: datetime | None = None) -> None:
    """Every bulk export is logged with who, why and what (HLD §6: no bulk export without a logged reason)."""
    actor = _treasurer(conn, actor_member_id)
    reason = (reason or "").strip()
    if not reason:
        raise Invalid("an export needs a reason")
    _screen("reason", reason)
    _audit(conn, txn_id=None, ac_id=actor["ac_id"], actor=actor_member_id, action="export", now=now or utcnow(),
           new={"filters": filters, "row_count": row_count}, note=reason)
