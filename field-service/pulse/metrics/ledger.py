"""Ledger metrics: the single source of the balance formulas (LLD §3.3, §7).

Each formula is defined once as data (Term rows). The Python evaluator and the SQL generator both
read that data, and the tests check that they agree on the same rows.

Balance components for a member (statuses in brackets):
    received      = inflow to + advance to [posted] + return to [posted] + adjustment to [posted]
    spent         = expense from [posted]
    passed_on     = advance from [posted, pending_ack]     deducted from the giver at once
    returned      = return from [posted, pending_ack]
    adjusted_out  = adjustment from [posted]
    balance       = received − spent − passed_on − returned − adjusted_out
A pending transfer is the giver's loss before it is the receiver's gain: the gap is "in transit".
Disputed and void rows count on neither side. A negative balance means the team owes the member.

Spend: Σ amount where kind = expense and status is not void. Advances, returns, inflows and
adjustments never count: money given is not money spent.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Iterable, Mapping
from uuid import UUID

VOID = "void"
SPEND_KINDS: tuple[str, ...] = ("expense",)
TRANSFER_KINDS: tuple[str, ...] = ("advance", "return")
_ZERO = Decimal("0.00")


@dataclass(frozen=True)
class Term:
    sign: int                  # +1 or -1
    kind: str
    side: str                  # 'to', 'from', or 'any' (either member column equals the member)
    statuses: tuple[str, ...]


BALANCE_COMPONENTS: dict[str, tuple[Term, ...]] = {
    "received": (Term(+1, "inflow", "to", ("posted",)), Term(+1, "advance", "to", ("posted",)),
                 Term(+1, "return", "to", ("posted",)), Term(+1, "adjustment", "to", ("posted",))),
    "spent": (Term(+1, "expense", "from", ("posted",)),),
    "passed_on": (Term(+1, "advance", "from", ("posted", "pending_ack")),),
    "returned": (Term(+1, "return", "from", ("posted", "pending_ack")),),
    "adjusted_out": (Term(+1, "adjustment", "from", ("posted",)),),
}
_BALANCE_SIGN = {"received": +1, "spent": -1, "passed_on": -1, "returned": -1, "adjusted_out": -1}

# the balance is derived from the components, so the two can never disagree
BALANCE_TERMS: tuple[Term, ...] = tuple(
    Term(t.sign * _BALANCE_SIGN[name], t.kind, t.side, t.statuses)
    for name, terms in BALANCE_COMPONENTS.items() for t in terms
)

# what a member has handed over that the other side has not yet acknowledged
IN_TRANSIT_TERMS: tuple[Term, ...] = (Term(+1, "advance", "from", ("pending_ack",)),
                                      Term(+1, "return", "from", ("pending_ack",)))
# transfers in dispute that this member gave or received
DISPUTED_TERMS: tuple[Term, ...] = (Term(+1, "advance", "any", ("disputed",)),
                                    Term(+1, "return", "any", ("disputed",)))


# ---- Python evaluator -------------------------------------------------------------------------

def _matches(t: Term, r: Mapping[str, Any], member_id: UUID) -> bool:
    if r["kind"] != t.kind or r["status"] not in t.statuses:
        return False
    if t.side == "any":
        return r["from_member_id"] == member_id or r["to_member_id"] == member_id
    return r[f"{t.side}_member_id"] == member_id


def _sum_terms(terms: Iterable[Term], rows: Iterable[Mapping[str, Any]], member_id: UUID) -> Decimal:
    rows = list(rows)
    return sum((t.sign * Decimal(r["amount"]) for t in terms for r in rows if _matches(t, r, member_id)), _ZERO)


def team_position(balances: Iterable[Decimal | None]) -> dict[str, Decimal]:
    """What the team holds and what it owes, from members' balances (the principal excluded by the
    caller). Not netted: one member holding ₹5,000 and another owed ₹3,000 is "holds 5,000, owed
    3,000", not "holds 2,000"."""
    bs = [Decimal(b) for b in balances if b is not None]
    return {"holds": sum((b for b in bs if b > 0), _ZERO), "owed": sum((-b for b in bs if b < 0), _ZERO)}


def member_balance(rows: Iterable[Mapping[str, Any]], member_id: UUID) -> Decimal:
    return _sum_terms(BALANCE_TERMS, rows, member_id)


def balance_components(rows: Iterable[Mapping[str, Any]], member_id: UUID) -> dict[str, Decimal]:
    rows = list(rows)
    out = {name: _sum_terms(terms, rows, member_id) for name, terms in BALANCE_COMPONENTS.items()}
    out["balance"] = sum((_BALANCE_SIGN[n] * v for n, v in out.items()), _ZERO)
    out["in_transit"] = _sum_terms(IN_TRANSIT_TERMS, rows, member_id)
    out["disputed"] = _sum_terms(DISPUTED_TERMS, rows, member_id)
    return out


def in_transit(rows: Iterable[Mapping[str, Any]], member_id: UUID) -> Decimal:
    return _sum_terms(IN_TRANSIT_TERMS, rows, member_id)


def disputed(rows: Iterable[Mapping[str, Any]], member_id: UUID) -> Decimal:
    return _sum_terms(DISPUTED_TERMS, rows, member_id)


def spend_total(rows: Iterable[Mapping[str, Any]]) -> Decimal:
    return sum((Decimal(r["amount"]) for r in rows if r["kind"] in SPEND_KINDS and r["status"] != VOID), _ZERO)


def in_transit_total(rows: Iterable[Mapping[str, Any]]) -> Decimal:
    """All transfers in the filter waiting for acknowledgement (LLD §7 in_transit)."""
    return sum((Decimal(r["amount"]) for r in rows if r["kind"] in TRANSFER_KINDS and r["status"] == "pending_ack"), _ZERO)


def disputed_total(rows: Iterable[Mapping[str, Any]]) -> Decimal:
    return sum((Decimal(r["amount"]) for r in rows if r["kind"] in TRANSFER_KINDS and r["status"] == "disputed"), _ZERO)


# ---- SQL generator ----------------------------------------------------------------------------
# Literals below come only from the constants in this module, never from input.

def _lit(values: Iterable[str]) -> str:
    return ", ".join("'" + v.replace("'", "''") + "'" for v in values)


def _cond(t: Term, member: str) -> str:
    side = (f"(from_member_id = {member} OR to_member_id = {member})" if t.side == "any"
            else f"{t.side}_member_id = {member}")
    return f"kind = '{t.kind}' AND status IN ({_lit(t.statuses)}) AND {side}"


def _sum_sql(terms: Iterable[Term], member: str) -> str:
    whens = " ".join(f"WHEN {_cond(t, member)} THEN {'' if t.sign > 0 else '-'}amount" for t in terms)
    return f"COALESCE(SUM(CASE {whens} ELSE 0 END), 0)::numeric(14, 2)"


def _member_rows(member: str) -> str:
    return f"FROM field.money_txn WHERE from_member_id = {member} OR to_member_id = {member}"


def member_balance_sql(member_param: str = "%(member_id)s") -> str:
    return f"SELECT {_sum_sql(BALANCE_TERMS, member_param)} AS balance {_member_rows(member_param)}"


def in_transit_sql(member_param: str = "%(member_id)s") -> str:
    return f"SELECT {_sum_sql(IN_TRANSIT_TERMS, member_param)} AS in_transit {_member_rows(member_param)}"


def disputed_sql(member_param: str = "%(member_id)s") -> str:
    return f"SELECT {_sum_sql(DISPUTED_TERMS, member_param)} AS disputed {_member_rows(member_param)}"


def balance_components_select(member_col: str) -> str:
    """Select-list of every component for the member in `member_col` (used per row of a member query)."""
    parts = [f"{_sum_sql(terms, member_col)} AS {name}" for name, terms in BALANCE_COMPONENTS.items()]
    parts.append(f"{_sum_sql(BALANCE_TERMS, member_col)} AS balance")
    parts.append(f"{_sum_sql(IN_TRANSIT_TERMS, member_col)} AS in_transit")
    parts.append(f"{_sum_sql(DISPUTED_TERMS, member_col)} AS disputed")
    return ", ".join(parts)


def balance_components_sql(member_param: str = "%(member_id)s") -> str:
    return f"SELECT {balance_components_select(member_param)} {_member_rows(member_param)}"


def spend_total_sql(where: str = "TRUE") -> str:
    """`where` is an extra SQL filter written by the caller with bound parameters (e.g. ac_id = %(ac_id)s);
    the table is aliased `t`, as in the scope fragments."""
    return (f"SELECT COALESCE(SUM(amount), 0)::numeric(14, 2) AS spend_total FROM field.money_txn t "
            f"WHERE kind IN ({_lit(SPEND_KINDS)}) AND status <> '{VOID}' AND ({where})")


def transfer_totals_sql(where: str = "TRUE") -> str:
    return (f"SELECT COALESCE(SUM(amount) FILTER (WHERE status = 'pending_ack'), 0)::numeric(14, 2) AS in_transit, "
            f"COALESCE(SUM(amount) FILTER (WHERE status = 'disputed'), 0)::numeric(14, 2) AS disputed "
            f"FROM field.money_txn t WHERE kind IN ({_lit(TRANSFER_KINDS)}) AND ({where})")
