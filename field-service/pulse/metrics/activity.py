"""Team activity and impact follow-up metrics (LLD §7): single source, parity-tested.

    member_active_days  Asia/Kolkata days in the last 14 (today included) with any non-void entry
                        the member created or any report they filed
    followup_done_pct   follow-ups answered "done" ÷ answered × 100; coverage_pct = answered ÷ asked
                        × 100 (cancelled ones not counted). NULL when the denominator is zero:
                        an unanswered follow-up is missing, not "no".
Percentages 0–100, 2 places, half up.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Iterable, Mapping

from pulse.common.clock import IST
from pulse.metrics.spend import IST_SQL

ACTIVE_WINDOW_DAYS = 14
_CENT = Decimal("0.01")


def _pct(part: int, whole: int) -> Decimal | None:
    if not whole:
        return None
    return (Decimal(part) * 100 / Decimal(whole)).quantize(_CENT, rounding=ROUND_HALF_UP)


def member_active_days(moments: Iterable[datetime], today: date, window: int = ACTIVE_WINDOW_DAYS) -> int:
    """moments: created_at of the member's non-void entries and filed_at of their reports."""
    first = today - timedelta(days=window - 1)
    return len({m.astimezone(IST).date() for m in moments if first <= m.astimezone(IST).date() <= today})


def active_days_sql(member: str = "%(member_id)s", today: str = "%(today)s") -> str:
    """One row: active_days. `member` and `today` are SQL expressions or bound parameters."""
    return f"""SELECT count(DISTINCT d) AS active_days FROM (
            SELECT (t.created_at AT TIME ZONE {IST_SQL})::date AS d FROM field.money_txn t
            WHERE t.created_by = {member} AND t.status <> 'void'
            UNION ALL
            SELECT (r.filed_at AT TIME ZONE {IST_SQL})::date FROM field.report r
            WHERE r.reporter_member_id = {member} AND r.status <> 'draft' AND r.filed_at IS NOT NULL) x
        WHERE d BETWEEN {today}::date - {ACTIVE_WINDOW_DAYS - 1} AND {today}::date"""


def followup_done_pct(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    asked = [r for r in rows if r["asked_at"] is not None and r["cancelled_at"] is None]
    answered = [r for r in asked if r["answer"] is not None]
    done = [r for r in answered if r["answer"] == "done"]
    return {"asked": len(asked), "answered": len(answered), "done": len(done),
            "done_pct": _pct(len(done), len(answered)), "coverage_pct": _pct(len(answered), len(asked))}


def followup_sql(where: str = "TRUE") -> str:
    """One row: asked, answered, done, done_pct, coverage_pct over field.txn_followup f."""
    return f"""SELECT asked, answered, done,
               ROUND(100.0 * done / NULLIF(answered, 0), 2) AS done_pct,
               ROUND(100.0 * answered / NULLIF(asked, 0), 2) AS coverage_pct
        FROM (SELECT count(*) FILTER (WHERE f.asked_at IS NOT NULL AND f.cancelled_at IS NULL)::int AS asked,
                     count(*) FILTER (WHERE f.asked_at IS NOT NULL AND f.cancelled_at IS NULL
                                      AND f.answer IS NOT NULL)::int AS answered,
                     count(*) FILTER (WHERE f.asked_at IS NOT NULL AND f.cancelled_at IS NULL
                                      AND f.answer = 'done')::int AS done
              FROM field.txn_followup f WHERE {where}) c"""
