"""Area metrics (LLD §7): the single source; tests/test_metrics_areas.py checks Python against SQL.

    reports_open               reports with status new, acknowledged, assigned or in_progress
    reports_per_1000_electors  open reports × 1000 ÷ electors, 2 places; NULL without an elector count
    need_vs_spend_rank_gap     per area: percentile rank of open reports per 1000 electors minus
                               percentile rank of spend per 1000 electors (0–100, 1 place each)

Percentile rank is Postgres percent_rank(): (number of areas with a strictly lower value) ÷ (n − 1)
× 100, so ties share the lower rank. Only areas with an elector count take part: an area without one
has NULL rates, NULL ranks and a NULL gap, and does not shift the others. With fewer than two areas
taking part every rank is NULL. Zero open reports and zero spend are real values (0), not missing.
A large positive gap means attention is needed and little has been spent there. It is an
association, not a cause.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Iterable, Mapping, Sequence

OPEN_STATUSES = ("new", "acknowledged", "assigned", "in_progress")
INTERPRETATION = "association, not cause"
_CENT = Decimal("0.01")
_TENTH = Decimal("0.1")


def reports_open(rows: Iterable[Mapping[str, Any]]) -> dict[tuple[str | None, int | None], int]:
    """{(area_id, category_id): count} over report rows."""
    out: dict[tuple[str | None, int | None], int] = {}
    for r in rows:
        if r["status"] in OPEN_STATUSES:
            key = (r["area_id"], r["category_id"])
            out[key] = out.get(key, 0) + 1
    return out


def per_1000(value: Decimal | int, electors: int | None) -> Decimal | None:
    if not electors:
        return None
    return (Decimal(value) * 1000 / Decimal(electors)).quantize(_CENT, rounding=ROUND_HALF_UP)


def reports_per_1000_electors(open_reports: int, electors: int | None) -> Decimal | None:
    return per_1000(open_reports, electors)


def percent_ranks(values: Mapping[str, Decimal | None]) -> dict[str, Decimal | None]:
    present = [v for v in values.values() if v is not None]
    n = len(present)
    out: dict[str, Decimal | None] = {}
    for key, v in values.items():
        if v is None or n < 2:
            out[key] = None
        else:
            lower = sum(1 for x in present if x < v)
            out[key] = (Decimal(lower) * 100 / Decimal(n - 1)).quantize(_TENTH, rounding=ROUND_HALF_UP)
    return out


def need_vs_spend(areas: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """areas: area_id, electors (or None), open_reports, open_issues, spend. Returns one row per area
    with need_per_1000, spend_per_1000, need_rank, spend_rank, rank_gap; ordered by gap (NULLs last)."""
    rows = []
    for a in areas:
        rows.append({**a, "spend": Decimal(a["spend"]).quantize(_CENT),
                     "need_per_1000": per_1000(a["open_reports"], a["electors"]),
                     "spend_per_1000": per_1000(a["spend"], a["electors"])})
    need = percent_ranks({r["area_id"]: r["need_per_1000"] for r in rows})
    spend = percent_ranks({r["area_id"]: r["spend_per_1000"] for r in rows})
    for r in rows:
        r["need_rank"], r["spend_rank"] = need[r["area_id"]], spend[r["area_id"]]
        r["rank_gap"] = (r["need_rank"] - r["spend_rank"]
                         if r["need_rank"] is not None and r["spend_rank"] is not None else None)
    return sorted(rows, key=lambda r: (r["rank_gap"] is None, -(r["rank_gap"] or 0), r["area_id"]))


# ---- SQL forms --------------------------------------------------------------------------------

def reports_open_sql(where: str = "TRUE") -> str:
    statuses = ", ".join(f"'{s}'" for s in OPEN_STATUSES)
    return f"""SELECT r.area_id, r.category_id, count(*) AS n FROM field.report r
               WHERE ({where}) AND r.status IN ({statuses}) GROUP BY 1, 2 ORDER BY 1, 2"""


def need_vs_spend_sql(spend_cte_sql: str) -> str:
    """spend_cte_sql: pulse.metrics.spend.spend_cte(where) for the spend side (AC, dates, scope).
    Bound parameters: %(nv_ac)s, %(nv_areas)s (text[]), %(nv_electors)s (int[], NULL = unknown)."""
    statuses = ", ".join(f"'{s}'" for s in OPEN_STATUSES)
    return f"""WITH {spend_cte_sql},
        areas AS (SELECT a.area_id, a.electors FROM unnest(%(nv_areas)s::text[], %(nv_electors)s::int[])
                  AS a(area_id, electors)),
        open_r AS (SELECT r.area_id, count(*) AS n FROM field.report r
                   WHERE r.ac_id = %(nv_ac)s AND r.status IN ({statuses}) GROUP BY 1),
        open_i AS (SELECT i.area_id, count(*) AS n FROM field.issue i
                   WHERE i.ac_id = %(nv_ac)s AND i.status = 'open' GROUP BY 1),
        sp AS (SELECT area_id, SUM(amount) AS v FROM spend GROUP BY 1),
        base AS (
          SELECT a.area_id, a.electors, COALESCE(o.n, 0)::int AS open_reports, COALESCE(oi.n, 0)::int AS open_issues,
                 COALESCE(sp.v, 0)::numeric(14, 2) AS spend,
                 CASE WHEN a.electors > 0 THEN ROUND(COALESCE(o.n, 0) * 1000.0 / a.electors, 2) END AS need_per_1000,
                 CASE WHEN a.electors > 0 THEN ROUND(COALESCE(sp.v, 0) * 1000 / a.electors, 2) END AS spend_per_1000
          FROM areas a LEFT JOIN open_r o ON o.area_id = a.area_id LEFT JOIN open_i oi ON oi.area_id = a.area_id
          LEFT JOIN sp ON sp.area_id = a.area_id),
        ranked AS (
          SELECT area_id, count(*) OVER () AS n,
                 ROUND((percent_rank() OVER (ORDER BY need_per_1000) * 100)::numeric, 1) AS nr,
                 ROUND((percent_rank() OVER (ORDER BY spend_per_1000) * 100)::numeric, 1) AS sr
          FROM base WHERE need_per_1000 IS NOT NULL)
        SELECT b.*, CASE WHEN r.n >= 2 THEN r.nr END AS need_rank, CASE WHEN r.n >= 2 THEN r.sr END AS spend_rank,
               CASE WHEN r.n >= 2 THEN r.nr - r.sr END AS rank_gap
        FROM base b LEFT JOIN ranked r ON r.area_id = b.area_id
        ORDER BY (CASE WHEN r.n >= 2 THEN r.nr - r.sr END) DESC NULLS LAST, b.area_id"""


def area_params(core, ac_id: str) -> dict[str, Any]:
    """The areas of the AC with their elector counts, from the CoreAdapter (missing stays NULL)."""
    areas = core.list_areas(ac_id)
    return {"nv_ac": ac_id, "nv_areas": [a.area_id for a in areas],
            "nv_electors": [core.electors_for_area(a.area_id) for a in areas]}
