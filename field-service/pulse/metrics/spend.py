"""Spend metrics (LLD §7): the single source. Each metric has a Python evaluator over expense rows
and an SQL form over the same rows; tests/test_metrics_spend.py checks they agree.

All metrics read non-void expenses only (money given is not money spent). The caller narrows the
rows with a WHERE fragment (AC, dates, finance scope); every SQL form here reads the `spend` CTE
built by `spend_cte(where)`. Percentages are 0–100, rounded half-up to 2 places; a ratio whose
denominator is zero, or whose input is missing, is NULL/None, never 0.

    spend_share_pct        group spend ÷ spend_total × 100
    unclassified_pct       spend in OTHER ÷ spend_total × 100
    tail_count_pct         entries with amount ≤ tail threshold ÷ entries × 100
    tail_value_pct         value of those entries ÷ spend_total × 100
    top_decile_value_pct   value of the largest ⌈10 %⌉ of entries (ties by txn_no) ÷ spend_total × 100
    logging_lag_days       median over a member's non-void entries (any kind) of
                           (Asia/Kolkata date of created_at − txn_date), 1 decimal
    reach                  per reach unit (persons, families, villages and events do not add up):
                           reach_total = Σ reach_count; cost_per_reach = spend on those entries ÷
                           reach_total; coverage_pct = spend on those entries ÷ spend_total × 100
    spend_per_1000_electors  area spend × 1000 ÷ electors; None when the core has no elector count
    ceiling_used_pct       Σ expense in the window with counts_to_ceiling not false ÷ ceiling × 100
"""
from __future__ import annotations

import math
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Iterable, Mapping, Sequence

from pulse.common.clock import IST

OTHER_CODE = "OTHER"
_CENT = Decimal("0.01")
_TENTH = Decimal("0.1")
_ZERO = Decimal("0.00")

# Size bands for tail analysis: (label, lower exclusive, upper inclusive); None = open.
SIZE_BANDS: tuple[tuple[str, Decimal | None, Decimal | None], ...] = (
    ("0-500", None, Decimal("500")),
    ("501-2000", Decimal("500"), Decimal("2000")),
    ("2001-10000", Decimal("2000"), Decimal("10000")),
    ("10001-25000", Decimal("10000"), Decimal("25000")),
    ("25001-100000", Decimal("25000"), Decimal("100000")),
    ("100000+", Decimal("100000"), None),
)

# Postgres here may lack tz data (embedded build); IST has no DST, so a fixed offset is exact.
IST_SQL = "INTERVAL '05:30'"


def _pct(part: Decimal | int, whole: Decimal | int) -> Decimal | None:
    if not whole:
        return None
    return (Decimal(part) * 100 / Decimal(whole)).quantize(_CENT, rounding=ROUND_HALF_UP)


def _round2(x: Decimal) -> Decimal:
    return x.quantize(_CENT, rounding=ROUND_HALF_UP)


# ---- the rows ---------------------------------------------------------------------------------

def is_spend(a: str = "t") -> str:
    """The one spend predicate: a non-void expense. Advances, returns, inflows, adjustments never count."""
    return f"{a}.kind = 'expense' AND {a}.status <> 'void'"


def spend_cte(where: str = "TRUE") -> str:
    """`spend` = non-void expenses passing the caller's filter, with their top-level bucket."""
    return f"""spend AS (
        SELECT t.txn_id, t.txn_no, t.ac_id, t.amount, t.txn_date, t.created_at, t.created_by,
               t.from_member_id, t.block_id, t.area_id, t.reach_count, t.reach_unit,
               t.in_election_window, t.counts_to_ceiling, t.pay_mode, t.fund_source,
               b.bucket_id AS sub_bucket_id, tb.bucket_id AS bucket_id, tb.code AS top_code
        FROM field.money_txn t
        JOIN field.exp_bucket b ON b.bucket_id = t.bucket_id
        JOIN field.exp_bucket tb ON tb.bucket_id = COALESCE(b.parent_id, b.bucket_id)
        WHERE {is_spend("t")} AND ({where}))"""


def spend_rows_sql(where: str = "TRUE") -> str:
    return f"WITH {spend_cte(where)} SELECT * FROM spend"


# ---- Python evaluators ------------------------------------------------------------------------

def total(rows: Sequence[Mapping[str, Any]]) -> Decimal:
    return sum((Decimal(r["amount"]) for r in rows), _ZERO)


def spend_share_pct(part: Decimal, spend_total: Decimal) -> Decimal | None:
    return _pct(part, spend_total)


def unclassified_pct(rows: Sequence[Mapping[str, Any]]) -> Decimal | None:
    return _pct(sum((Decimal(r["amount"]) for r in rows if r["top_code"] == OTHER_CODE), _ZERO), total(rows))


def tail(rows: Sequence[Mapping[str, Any]], threshold: Decimal) -> dict[str, Decimal | None]:
    small = [r for r in rows if Decimal(r["amount"]) <= threshold]
    return {"tail_count_pct": _pct(len(small), len(rows)),
            "tail_value_pct": _pct(total(small), total(rows))}


def top_decile_value_pct(rows: Sequence[Mapping[str, Any]]) -> Decimal | None:
    if not rows:
        return None
    ranked = sorted(rows, key=lambda r: (-Decimal(r["amount"]), r["txn_no"]))
    k = math.ceil(len(ranked) * 0.1)
    return _pct(total(ranked[:k]), total(rows))


def reach(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    whole = total(rows)
    out = []
    for unit in sorted({r["reach_unit"] for r in rows if r["reach_count"] is not None}):
        with_reach = [r for r in rows if r["reach_unit"] == unit and r["reach_count"] is not None]
        count = sum(r["reach_count"] for r in with_reach)
        spent = total(with_reach)
        out.append({"reach_unit": unit, "reach_total": count, "spend_with_reach": spent,
                    "cost_per_reach": _round2(spent / count), "coverage_pct": _pct(spent, whole)})
    return out


def band_of(amount: Decimal) -> str:
    for label, lo, hi in SIZE_BANDS:
        if (lo is None or amount > lo) and (hi is None or amount <= hi):
            return label
    raise ValueError(amount)


def size_bands(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for label, _, _ in SIZE_BANDS:
        in_band = [r for r in rows if band_of(Decimal(r["amount"])) == label]
        out.append({"band": label, "count": len(in_band), "value": total(in_band)})
    return out


def logging_lag_days(entries: Iterable[Mapping[str, Any]]) -> Decimal | None:
    """entries: a member's non-void entries of any kind, with created_at and txn_date."""
    lags = sorted(Decimal((r["created_at"].astimezone(IST).date() - r["txn_date"]).days) for r in entries)
    if not lags:
        return None
    mid = len(lags) // 2
    median = lags[mid] if len(lags) % 2 else (lags[mid - 1] + lags[mid]) / 2
    return median.quantize(_TENTH, rounding=ROUND_HALF_UP)


def spend_per_1000_electors(area_spend: Decimal, electors: int | None) -> Decimal | None:
    if not electors:
        return None
    return _round2(Decimal(area_spend) * 1000 / Decimal(electors))


def ceiling_used_pct(rows: Sequence[Mapping[str, Any]], window: Mapping[str, Any]) -> Decimal | None:
    if window.get("ceiling_amount") is None:
        return None
    counted = [r for r in rows if r["in_election_window"] and r["counts_to_ceiling"] is not False
               and window["starts_on"] <= r["txn_date"] <= window["ends_on"]]
    return _pct(total(counted), window["ceiling_amount"])


# ---- SQL forms (each reads the `spend` CTE) ---------------------------------------------------

def totals_sql(where: str = "TRUE") -> str:
    """One row: spend_total, entries, unclassified_pct, tail_count_pct, tail_value_pct, top_decile_value_pct.
    Needs the bound parameter %(tail_threshold)s."""
    return f"""WITH {spend_cte(where)},
        ranked AS (SELECT amount, row_number() OVER (ORDER BY amount DESC, txn_no) AS rn,
                          count(*) OVER () AS n FROM spend)
        SELECT
          (SELECT COALESCE(SUM(amount), 0)::numeric(14, 2) FROM spend) AS spend_total,
          (SELECT count(*) FROM spend) AS entries,
          (SELECT ROUND(100 * COALESCE(SUM(amount) FILTER (WHERE top_code = '{OTHER_CODE}'), 0)
                  / NULLIF(SUM(amount), 0), 2) FROM spend) AS unclassified_pct,
          (SELECT ROUND(100.0 * count(*) FILTER (WHERE amount <= %(tail_threshold)s)
                  / NULLIF(count(*), 0), 2) FROM spend) AS tail_count_pct,
          (SELECT ROUND(100 * COALESCE(SUM(amount) FILTER (WHERE amount <= %(tail_threshold)s), 0)
                  / NULLIF(SUM(amount), 0), 2) FROM spend) AS tail_value_pct,
          (SELECT ROUND(100 * COALESCE(SUM(amount) FILTER (WHERE rn <= CEIL(n * 0.1)), 0)
                  / NULLIF(SUM(amount), 0), 2) FROM ranked) AS top_decile_value_pct"""


def reach_sql(where: str = "TRUE") -> str:
    return f"""WITH {spend_cte(where)},
        whole AS (SELECT SUM(amount) AS v FROM spend)
        SELECT reach_unit, SUM(reach_count)::bigint AS reach_total,
               SUM(amount)::numeric(14, 2) AS spend_with_reach,
               ROUND(SUM(amount) / SUM(reach_count), 2) AS cost_per_reach,
               ROUND(100 * SUM(amount) / NULLIF((SELECT v FROM whole), 0), 2) AS coverage_pct
        FROM spend WHERE reach_count IS NOT NULL GROUP BY reach_unit ORDER BY reach_unit"""


def _band_case() -> str:
    whens = []
    for label, lo, hi in SIZE_BANDS:
        conds = ([f"amount > {lo}"] if lo is not None else []) + ([f"amount <= {hi}"] if hi is not None else [])
        whens.append(f"WHEN {' AND '.join(conds)} THEN '{label}'")
    return "CASE " + " ".join(whens) + " END"


def size_bands_sql(where: str = "TRUE") -> str:
    values = ", ".join(f"('{label}', {i})" for i, (label, _, _) in enumerate(SIZE_BANDS))
    return f"""WITH {spend_cte(where)},
        bands(band, ord) AS (VALUES {values}),
        banded AS (SELECT {_band_case()} AS band, amount FROM spend)
        SELECT b.band, count(x.amount) AS count, COALESCE(SUM(x.amount), 0)::numeric(14, 2) AS value
        FROM bands b LEFT JOIN banded x ON x.band = b.band GROUP BY b.band, b.ord ORDER BY b.ord"""


def logging_lag_sql(member_param: str = "%(member_id)s") -> str:
    return f"""SELECT ROUND((percentile_cont(0.5) WITHIN GROUP (
                   ORDER BY ((created_at AT TIME ZONE {IST_SQL})::date - txn_date)))::numeric, 1) AS logging_lag_days
               FROM field.money_txn WHERE created_by = {member_param} AND status <> 'void'"""


def ceiling_sql(where: str = "TRUE") -> str:
    """One row per election window of the AC in %(ac_id)s."""
    return f"""WITH {spend_cte(where)}
        SELECT w.window_id, w.starts_on, w.ends_on, w.ceiling_amount,
               COALESCE(SUM(s.amount) FILTER (WHERE s.in_election_window AND s.counts_to_ceiling IS NOT FALSE), 0)
                   ::numeric(14, 2) AS counted,
               ROUND(100 * COALESCE(SUM(s.amount) FILTER (WHERE s.in_election_window
                     AND s.counts_to_ceiling IS NOT FALSE), 0) / w.ceiling_amount, 2) AS ceiling_used_pct
        FROM field.ac_election_window w
        LEFT JOIN spend s ON s.ac_id = w.ac_id AND s.txn_date BETWEEN w.starts_on AND w.ends_on
        WHERE w.ac_id = %(ac_id)s
        GROUP BY w.window_id ORDER BY w.starts_on"""
