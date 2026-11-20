"""Balances, spend summary and tail (LLD §9). Finance scope is applied inside every query."""
from __future__ import annotations

from datetime import date
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from pulse.api.deps import get_state
from pulse.api.ledger_auth import LedgerCaller, at_least
from pulse.api.routers.ledger import ac_limits, plain
from pulse.db import transaction
from pulse.ledger import queries, scope
from pulse.metrics import areas as am
from pulse.metrics import spend as sm

router = APIRouter(prefix="/acs/{ac_number}/field")

GroupKey = Literal["bucket", "sub_bucket", "block", "area", "member", "week", "month", "day", "pay_mode",
                   "fund_source"]


def spend_filter(date_from: date | None = None, date_to: date | None = None, block_id: str | None = None,
                 area_id: str | None = None, member_id: UUID | None = None,
                 bucket_id: int | None = None) -> queries.SpendFilter:
    """The spend screens' page filters, shared by summary, tail and flow."""
    return queries.SpendFilter(date_from=date_from, date_to=date_to, block_id=block_id, area_id=area_id,
                               member_id=member_id, bucket_id=bucket_id)


@router.get("/balances")
def balances(caller: LedgerCaller = Depends(at_least("team")), state=Depends(get_state)):
    """The visible tree. The principal is the source of funds, so their balance is not shown (LLD §3.3)."""
    with transaction(state.pool) as conn:
        rows = queries.balances(conn, caller.viewer)
    from pulse.metrics.ledger import team_position

    out = []
    for r in rows:
        r = dict(r)
        if r["is_principal"]:
            r["balance"] = None
        out.append(r)
    position = team_position(r["balance"] for r in out if not r["is_principal"])
    return {"members": plain(out), "team_position": plain(position)}


@router.get("/spend/summary")
def spend_summary(group_by: str = Query("", description="comma list of " + ", ".join(GroupKey.__args__)),
                  sf: queries.SpendFilter = Depends(spend_filter),
                  caller: LedgerCaller = Depends(at_least("team")), state=Depends(get_state)):
    keys = [k.strip() for k in group_by.split(",") if k.strip()]
    bad = [k for k in keys if k not in queries.GROUP_KEYS]
    if bad or len(set(keys)) != len(keys):
        raise HTTPException(422, "group_by takes distinct values from: " + ", ".join(queries.GROUP_KEYS))
    with transaction(state.pool) as conn:
        s = queries.summary(conn, state.core, caller.viewer, keys, date_from=sf.date_from, date_to=sf.date_to,
                            limits=ac_limits(state, conn, caller.viewer.ac_id), sf=sf)
    return plain({"group_by": keys, "totals": s.totals, "groups": s.groups, "reach": s.reach, "ceiling": s.ceiling})


@router.get("/spend/tail")
def spend_tail(sf: queries.SpendFilter = Depends(spend_filter),
               caller: LedgerCaller = Depends(at_least("all")), state=Depends(get_state)):
    with transaction(state.pool) as conn:
        return plain(queries.tail_bands(conn, caller.viewer, date_from=sf.date_from, date_to=sf.date_to,
                                        limits=ac_limits(state, conn, caller.viewer.ac_id), sf=sf))


@router.get("/spend/flow")
def spend_flow(sf: queries.SpendFilter = Depends(spend_filter),
               caller: LedgerCaller = Depends(at_least("team")), state=Depends(get_state)):
    """Money flow as nodes and links: funds in -> principal -> team (net of returns) -> buckets."""
    with transaction(state.pool) as conn:
        return plain(queries.money_flow(conn, caller.viewer, sf))


@router.get("/spend/need-vs-spend")
def need_vs_spend(date_from: date | None = None, date_to: date | None = None,
                  caller: LedgerCaller = Depends(at_least("all")), state=Depends(get_state)):
    """Per area: open reports and issues beside spend, reach and electors, with both percentile ranks
    and their gap (pulse/metrics/areas.py). An association, not a cause."""
    where = [scope.txn_where(caller.viewer)]
    p = {**scope.params(caller.viewer), **am.area_params(state.core, caller.viewer.ac_id)}
    if date_from:
        where.append("t.txn_date >= %(f_from)s"); p["f_from"] = date_from
    if date_to:
        where.append("t.txn_date <= %(f_to)s"); p["f_to"] = date_to
    cte = sm.spend_cte(" AND ".join(where))
    with transaction(state.pool) as conn:
        rows = conn.execute(am.need_vs_spend_sql(cte), p).fetchall()
        reach = conn.execute(
            f"""WITH {cte} SELECT area_id, reach_unit, SUM(reach_count)::bigint AS reach_total FROM spend
                WHERE reach_count IS NOT NULL AND area_id IS NOT NULL GROUP BY 1, 2 ORDER BY 1, 2""", p).fetchall()
    names = {a.area_id: a for a in state.core.list_areas(caller.viewer.ac_id)}
    by_area: dict[str, dict[str, int]] = {}
    for r in reach:
        by_area.setdefault(r["area_id"], {})[r["reach_unit"]] = r["reach_total"]
    out = []
    for r in rows:
        a = names.get(r["area_id"])
        out.append({**r, "block_id": a.block_id if a else None, "name_hi": a.name_hi if a else None,
                    "name_en": a.name_en if a else None, "kind": a.kind if a else None,
                    "reach": by_area.get(r["area_id"], {})})
    return plain({"interpretation": am.INTERPRETATION, "date_from": date_from, "date_to": date_to,
                  "areas": out})
