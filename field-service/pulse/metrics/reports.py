"""Report response metrics (LLD §7): single source, Python and SQL kept in step by a parity test.

    ack_minutes    median minutes from filing to acknowledgement, over reports that were acknowledged
    resolve_days   median days from filing to resolution, over reports with status resolved
Both 1 decimal, half up; NULL/None when there is nothing to take the median of.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Iterable, Mapping

_TENTH = Decimal("0.1")


def _median(values: list[Decimal]) -> Decimal | None:
    if not values:
        return None
    values = sorted(values)
    mid = len(values) // 2
    m = values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2
    return m.quantize(_TENTH, rounding=ROUND_HALF_UP)


def ack_minutes(rows: Iterable[Mapping[str, Any]]) -> Decimal | None:
    return _median([Decimal((r["ack_at"] - r["filed_at"]).total_seconds()) / 60
                    for r in rows if r["ack_at"] is not None and r["filed_at"] is not None])


def resolve_days(rows: Iterable[Mapping[str, Any]]) -> Decimal | None:
    return _median([Decimal((r["resolved_at"] - r["filed_at"]).total_seconds()) / 86400
                    for r in rows if r["status"] == "resolved" and r["resolved_at"] is not None])


def medians_sql(where: str = "TRUE") -> str:
    """One row: ack_minutes, resolve_days over field.report r filtered by `where`."""
    return f"""SELECT
        ROUND((percentile_cont(0.5) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM r.ack_at - r.filed_at) / 60)
               FILTER (WHERE r.ack_at IS NOT NULL AND r.filed_at IS NOT NULL))::numeric, 1) AS ack_minutes,
        ROUND((percentile_cont(0.5) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM r.resolved_at - r.filed_at) / 86400)
               FILTER (WHERE r.status = 'resolved' AND r.resolved_at IS NOT NULL))::numeric, 1) AS resolve_days
        FROM field.report r WHERE {where}"""
