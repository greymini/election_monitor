"""Election-window register (LLD slice 4): windows, statutory-head mapping, the register by head,
the review list and the logged export. Finance scope `all` only; callers check it before calling.

Which entries belong to a window: non-void expenses of the AC whose txn_date falls inside it. The
ceiling figure itself is the single-source metric (pulse/metrics/spend.py, ceiling_used_pct), which
counts entries saved with in_election_window set. An entry dated inside a window but recorded
before the window existed was saved without that mark (the ledger is append-only), so the register
reports those separately as `recorded_before_window` instead of silently counting or dropping them.

Statutory head of an entry: its sub-head's mapping, else its top-level bucket's, else NULL. NULL is
shown as "unmapped" and never dropped.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

import psycopg

from pulse import settings_ac
from pulse.ledger.errors import Invalid, NotFound
from pulse.metrics import spend as sm

UNMAPPED = "unmapped"
HEAD_MAX = 120
REVIEW_FLAGS = ("cash_over_limit", "sensitive_in_window")


# ---- windows ----------------------------------------------------------------------------------

def windows(conn: psycopg.Connection, ac_id: str) -> list[dict[str, Any]]:
    return conn.execute("SELECT * FROM field.ac_election_window WHERE ac_id = %s ORDER BY starts_on", (ac_id,)).fetchall()


def window(conn: psycopg.Connection, ac_id: str, window_id: int) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM field.ac_election_window WHERE ac_id = %s AND window_id = %s",
                       (ac_id, window_id)).fetchone()
    if row is None:
        raise NotFound("election window not found")
    return row


def current_window(conn: psycopg.Connection, ac_id: str, on: date) -> dict[str, Any] | None:
    return conn.execute("SELECT * FROM field.ac_election_window WHERE ac_id = %s AND %s BETWEEN starts_on AND ends_on",
                        (ac_id, on)).fetchone()


def _check(starts_on: date, ends_on: date, ceiling: Decimal | None, cash_limit: Decimal | None) -> None:
    if ends_on < starts_on:
        raise Invalid("the window ends before it starts")
    for name, v in (("ceiling_amount", ceiling), ("cash_limit", cash_limit)):
        if v is not None and v <= 0:
            raise Invalid(f"{name} must be greater than zero")


def _overlap_guard(e: psycopg.Error) -> Invalid:   # noqa: ARG001
    return Invalid("this window overlaps another window of the AC")


def create_window(conn: psycopg.Connection, ac_id: str, *, starts_on: date, ends_on: date,
                  ceiling_amount: Decimal | None, cash_limit: Decimal | None, event_id: str | None,
                  actor_user_id: str, actor_member_id: UUID) -> dict[str, Any]:
    _check(starts_on, ends_on, ceiling_amount, cash_limit)
    try:
        with conn.transaction():
            row = conn.execute(
                """INSERT INTO field.ac_election_window (ac_id, event_id, starts_on, ends_on, ceiling_amount, cash_limit)
                   VALUES (%s, %s, %s, %s, %s, %s) RETURNING *""",
                (ac_id, event_id, starts_on, ends_on, ceiling_amount, cash_limit)).fetchone()
    except psycopg.errors.ExclusionViolation as e:
        raise _overlap_guard(e) from None
    settings_ac.audit(conn, ac_id, "window_created", actor_user_id=actor_user_id, actor_member_id=actor_member_id,
                      data=_audit_data(row))
    return row


def update_window(conn: psycopg.Connection, ac_id: str, window_id: int, *, starts_on: date, ends_on: date,
                  ceiling_amount: Decimal | None, cash_limit: Decimal | None, event_id: str | None,
                  actor_user_id: str, actor_member_id: UUID) -> dict[str, Any]:
    old = window(conn, ac_id, window_id)
    _check(starts_on, ends_on, ceiling_amount, cash_limit)
    try:
        with conn.transaction():
            row = conn.execute(
                """UPDATE field.ac_election_window SET event_id = %s, starts_on = %s, ends_on = %s,
                       ceiling_amount = %s, cash_limit = %s WHERE window_id = %s RETURNING *""",
                (event_id, starts_on, ends_on, ceiling_amount, cash_limit, window_id)).fetchone()
    except psycopg.errors.ExclusionViolation as e:
        raise _overlap_guard(e) from None
    settings_ac.audit(conn, ac_id, "window_changed", actor_user_id=actor_user_id, actor_member_id=actor_member_id,
                      data={"old": _audit_data(old), "new": _audit_data(row)})
    return row


def delete_window(conn: psycopg.Connection, ac_id: str, window_id: int, *, actor_user_id: str,
                  actor_member_id: UUID) -> None:
    old = window(conn, ac_id, window_id)
    conn.execute("DELETE FROM field.ac_election_window WHERE window_id = %s", (window_id,))
    settings_ac.audit(conn, ac_id, "window_deleted", actor_user_id=actor_user_id, actor_member_id=actor_member_id,
                      data=_audit_data(old))


def _audit_data(w: dict[str, Any]) -> dict[str, Any]:
    return {k: (str(v) if v is not None else None) for k, v in w.items() if k != "ac_id"}


# ---- statutory heads --------------------------------------------------------------------------

def heads(conn: psycopg.Connection) -> list[dict[str, Any]]:
    """Every active bucket with its mapping (NULL = not mapped)."""
    return conn.execute(
        """SELECT b.bucket_id, b.code, b.name_hi, b.name_en, b.parent_id, b.statutory_head,
                  COALESCE(b.statutory_head, p.statutory_head) AS effective_head
           FROM field.exp_bucket b LEFT JOIN field.exp_bucket p ON p.bucket_id = b.parent_id
           WHERE b.active ORDER BY COALESCE(p.sort, b.sort), b.parent_id NULLS FIRST, b.sort""").fetchall()


def set_head(conn: psycopg.Connection, ac_id: str, bucket_id: int, head: str | None, *, actor_user_id: str,
             actor_member_id: UUID) -> dict[str, Any]:
    """Map a bucket to a statutory head (None clears it). The bucket table is shared by every AC, so
    this mapping applies to all of them (docs/MERGE.md, open decisions)."""
    head = (head or "").strip() or None
    if head is not None and len(head) > HEAD_MAX:
        raise Invalid(f"statutory head is longer than {HEAD_MAX} characters")
    old = conn.execute("SELECT * FROM field.exp_bucket WHERE bucket_id = %s FOR UPDATE", (bucket_id,)).fetchone()
    if old is None or (old["ac_id"] is not None and old["ac_id"] != ac_id):
        raise NotFound("bucket not found")
    row = conn.execute("UPDATE field.exp_bucket SET statutory_head = %s WHERE bucket_id = %s RETURNING *",
                       (head, bucket_id)).fetchone()
    settings_ac.audit(conn, ac_id, "statutory_head_set", actor_user_id=actor_user_id, actor_member_id=actor_member_id,
                      data={"bucket_id": bucket_id, "code": row["code"], "old": old["statutory_head"], "new": head})
    return row


# ---- the register -----------------------------------------------------------------------------

_ROWS = """
SELECT t.txn_id, t.txn_no, t.txn_date, t.amount, t.pay_mode, t.flagged, t.in_election_window, t.counts_to_ceiling,
       t.block_id, t.area_id, t.remark, t.custom_label, t.payee_kind, t.payee_label, t.created_at,
       b.code AS bucket_code, b.name_hi AS bucket_name_hi, tb.code AS top_bucket_code, tb.name_hi AS top_bucket_name_hi,
       COALESCE(b.statutory_head, tb.statutory_head) AS statutory_head,
       m.display_name AS payer_name
FROM field.money_txn t
JOIN field.exp_bucket b ON b.bucket_id = t.bucket_id
JOIN field.exp_bucket tb ON tb.bucket_id = COALESCE(b.parent_id, b.bucket_id)
LEFT JOIN field.member m ON m.member_id = t.from_member_id
WHERE t.ac_id = %(ac)s AND t.kind = 'expense' AND t.status <> 'void'
  AND t.txn_date BETWEEN %(from)s AND %(to)s"""


def rows(conn: psycopg.Connection, ac_id: str, w: dict[str, Any], extra: str = "TRUE") -> list[dict[str, Any]]:
    return conn.execute(f"{_ROWS} AND ({extra}) ORDER BY t.txn_date, t.txn_no",
                        {"ac": ac_id, "from": w["starts_on"], "to": w["ends_on"]}).fetchall()


def register(conn: psycopg.Connection, ac_id: str, window_id: int) -> dict[str, Any]:
    w = window(conn, ac_id, window_id)
    entries = rows(conn, ac_id, w)
    by_head: dict[str, dict[str, Any]] = {}
    for r in entries:
        key = r["statutory_head"] or UNMAPPED
        g = by_head.setdefault(key, {"statutory_head": key, "mapped": r["statutory_head"] is not None,
                                     "entries": 0, "amount": Decimal("0.00")})
        g["entries"] += 1
        g["amount"] += r["amount"]
    groups = sorted(by_head.values(), key=lambda g: (not g["mapped"], -g["amount"], g["statutory_head"]))
    ceiling = next((c for c in conn.execute(sm.ceiling_sql(f"t.ac_id = %(ac_id)s"), {"ac_id": ac_id}).fetchall()
                    if c["window_id"] == window_id), None)
    before = [r for r in entries if not r["in_election_window"]]
    running, daily = Decimal("0.00"), []
    for r in entries:
        if r["in_election_window"] and r["counts_to_ceiling"] is not False:
            running += r["amount"]
            if daily and daily[-1]["date"] == r["txn_date"]:
                daily[-1]["counted_to_date"] = running
            else:
                daily.append({"date": r["txn_date"], "counted_to_date": running})
    return {"window": w, "heads": groups,
            "total": sum((r["amount"] for r in entries), Decimal("0.00")), "entries": len(entries),
            "counted": ceiling["counted"] if ceiling else Decimal("0.00"),
            "ceiling_used_pct": ceiling["ceiling_used_pct"] if ceiling else None,
            "undecided": sum(1 for r in entries if r["in_election_window"] and r["counts_to_ceiling"] is None),
            "recorded_before_window": {"entries": len(before),
                                       "amount": sum((r["amount"] for r in before), Decimal("0.00"))},
            "daily": daily}


def review(conn: psycopg.Connection, ac_id: str, window_id: int) -> list[dict[str, Any]]:
    """Entries the treasurer should look at: cash over the window's limit, sensitive buckets in the
    window, and window entries whose ceiling decision is still open."""
    w = window(conn, ac_id, window_id)
    return rows(conn, ac_id, w, "t.flagged && ARRAY['cash_over_limit', 'sensitive_in_window']::text[] "
                                "OR (t.in_election_window AND t.counts_to_ceiling IS NULL)")


EXPORT_COLUMNS = ("statutory_head", "txn_no", "txn_date", "amount", "pay_mode", "top_bucket_code", "bucket_code",
                  "custom_label", "payee_kind", "payee_label", "payer_name", "block_id", "area_id", "remark",
                  "in_election_window", "counts_to_ceiling", "flagged")


def export_rows(conn: psycopg.Connection, ac_id: str, window_id: int) -> list[dict[str, Any]]:
    """Ordered by head (mapped heads first, then 'unmapped'), then date."""
    w = window(conn, ac_id, window_id)
    out = [dict(r, statutory_head=r["statutory_head"] or UNMAPPED) for r in rows(conn, ac_id, w)]
    out.sort(key=lambda r: (r["statutory_head"] == UNMAPPED, r["statutory_head"], r["txn_date"], r["txn_no"]))
    return out
