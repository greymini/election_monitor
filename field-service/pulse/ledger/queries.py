"""Read side of the ledger: transaction list, balances tree, spend summary and tail.

Every query is narrowed by pulse/ledger/scope.py inside the SQL. Formulas come from pulse/metrics.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

import psycopg

from pulse.config import LedgerLimits
from pulse.core_adapter import CoreAdapter
from pulse.ledger.scope import Viewer, params, txn_where, visible_members
from pulse.metrics import spend as sm
from pulse.metrics.ledger import balance_components_select, transfer_totals_sql

GROUP_KEYS = {
    "bucket": "bucket_id",
    "sub_bucket": "sub_bucket_id",
    "block": "block_id",
    "area": "area_id",
    "member": "from_member_id",
    "week": "date_trunc('week', txn_date)::date",
    "month": "date_trunc('month', txn_date)::date",
    "day": "txn_date",
    "pay_mode": "pay_mode",
    "fund_source": "fund_source",
}
MAX_PAGE = 200


@dataclass
class TxnFilter:
    date_from: date | None = None
    date_to: date | None = None
    kind: str | None = None
    bucket_id: int | None = None          # a top-level bucket includes its sub-heads
    block_id: str | None = None
    area_id: str | None = None
    member_id: UUID | None = None         # payer or receiver
    flag: str | None = None
    status: str | None = None

    def sql(self, a: str = "t") -> tuple[str, dict[str, Any]]:
        parts: list[str] = []
        p: dict[str, Any] = {}
        if self.date_from:
            parts.append(f"{a}.txn_date >= %(f_from)s"); p["f_from"] = self.date_from
        if self.date_to:
            parts.append(f"{a}.txn_date <= %(f_to)s"); p["f_to"] = self.date_to
        if self.kind:
            parts.append(f"{a}.kind = %(f_kind)s"); p["f_kind"] = self.kind
        if self.bucket_id is not None:
            parts.append(f"{a}.bucket_id IN (SELECT bucket_id FROM field.exp_bucket "
                         f"WHERE bucket_id = %(f_bucket)s OR parent_id = %(f_bucket)s)")
            p["f_bucket"] = self.bucket_id
        if self.block_id:
            parts.append(f"{a}.block_id = %(f_block)s"); p["f_block"] = self.block_id
        if self.area_id:
            parts.append(f"{a}.area_id = %(f_area)s"); p["f_area"] = self.area_id
        if self.member_id:
            parts.append(f"(%(f_member)s IN ({a}.from_member_id, {a}.to_member_id))"); p["f_member"] = self.member_id
        if self.flag:
            parts.append(f"%(f_flag)s = ANY ({a}.flagged)"); p["f_flag"] = self.flag
        if self.status:
            parts.append(f"{a}.status = %(f_status)s"); p["f_status"] = self.status
        return (" AND ".join(parts) or "TRUE"), p


_TXN_SELECT = """
SELECT t.*, fm.display_name AS from_name, tm.display_name AS to_name, cm.display_name AS created_by_name,
       b.code AS bucket_code, b.name_hi AS bucket_name_hi, tb.code AS top_bucket_code, tb.name_hi AS top_bucket_name_hi
FROM field.money_txn t
LEFT JOIN field.member fm ON fm.member_id = t.from_member_id
LEFT JOIN field.member tm ON tm.member_id = t.to_member_id
LEFT JOIN field.member cm ON cm.member_id = t.created_by
LEFT JOIN field.exp_bucket b ON b.bucket_id = t.bucket_id
LEFT JOIN field.exp_bucket tb ON tb.bucket_id = COALESCE(b.parent_id, b.bucket_id)
"""


def list_txns(conn: psycopg.Connection, viewer: Viewer, f: TxnFilter, *, limit: int = 50,
              offset: int = 0) -> tuple[list[dict[str, Any]], int]:
    where, fp = f.sql()
    p = {**params(viewer), **fp, "limit": min(max(limit, 1), MAX_PAGE), "offset": max(offset, 0)}
    clause = f"WHERE {txn_where(viewer)} AND {where}"
    total = conn.execute(f"SELECT count(*) AS n FROM field.money_txn t {clause}", p).fetchone()["n"]
    rows = conn.execute(f"{_TXN_SELECT} {clause} ORDER BY t.txn_date DESC, t.txn_no DESC LIMIT %(limit)s OFFSET %(offset)s",
                        p).fetchall()
    return rows, total


LEDGER_GROUPINGS = ("day", "bucket", "member")


def ledger_summary(conn: psycopg.Connection, viewer: Viewer, f: TxnFilter, grouping: str) -> dict[str, Any]:
    """The ledger screen's figures for a filter, over every matching row (the list itself is paged):
    spend total and count, the largest expense, spend by top-level bucket, and per group (day, bucket or
    member) the number of rows and the spend in it. Spend uses the single predicate in metrics/spend.py."""
    if grouping not in LEDGER_GROUPINGS:
        raise ValueError(f"grouping must be one of {LEDGER_GROUPINGS}")
    where, fp = f.sql()
    scoped = f"{txn_where(viewer)} AND {where}"
    p = {**params(viewer), **fp}
    spend = sm.spend_cte(scoped)
    totals = conn.execute(
        f"""WITH {spend} SELECT COALESCE(SUM(amount), 0)::numeric(14, 2) AS spend_total, count(*) AS spend_entries,
                   (SELECT count(*) FROM field.money_txn t WHERE {scoped}) AS rows
            FROM spend""", p).fetchone()
    largest = conn.execute(
        f"""WITH {spend} SELECT txn_id, txn_no, amount, top_code AS top_bucket_code FROM spend
            ORDER BY amount DESC, txn_no LIMIT 1""", p).fetchone()
    by_bucket = conn.execute(
        f"""WITH {spend} SELECT top_code AS top_bucket_code, SUM(amount)::numeric(14, 2) AS spend, count(*) AS entries
            FROM spend GROUP BY top_code ORDER BY 2 DESC, 1""", p).fetchall()
    key = {
        "day": "t.txn_date::text",
        "bucket": f"CASE WHEN t.kind = 'expense' THEN COALESCE(tb.code, '{sm.OTHER_CODE}') ELSE '~' || t.kind END",
        "member": "(CASE WHEN t.kind = 'expense' THEN t.from_member_id ELSE COALESCE(t.from_member_id, t.to_member_id) END)::text",
    }[grouping]
    groups = conn.execute(
        f"""SELECT {key} AS key, count(*) AS rows, count(*) FILTER (WHERE t.kind <> 'expense') AS transfers,
                   COALESCE(SUM(t.amount) FILTER (WHERE {sm.is_spend("t")}), 0)::numeric(14, 2) AS spend,
                   max(m.display_name) AS member_name
            FROM field.money_txn t
            LEFT JOIN field.exp_bucket b ON b.bucket_id = t.bucket_id
            LEFT JOIN field.exp_bucket tb ON tb.bucket_id = COALESCE(b.parent_id, b.bucket_id)
            LEFT JOIN field.member m ON m.member_id = (CASE WHEN t.kind = 'expense' THEN t.from_member_id
                                                       ELSE COALESCE(t.from_member_id, t.to_member_id) END)
            WHERE {scoped}
            GROUP BY 1
            ORDER BY {"1 DESC" if grouping == "day" else "4 DESC, 1"}""", p).fetchall()
    return {"grouping": grouping, "totals": dict(totals), "largest": dict(largest) if largest else None,
            "by_bucket": by_bucket, "groups": groups}


def export_txns(conn: psycopg.Connection, viewer: Viewer, f: TxnFilter) -> list[dict[str, Any]]:
    where, fp = f.sql()
    return conn.execute(f"{_TXN_SELECT} WHERE {txn_where(viewer)} AND {where} ORDER BY t.txn_date, t.txn_no",
                        {**params(viewer), **fp}).fetchall()


def get_txn(conn: psycopg.Connection, viewer: Viewer, txn_id: UUID) -> dict[str, Any] | None:
    return conn.execute(f"{_TXN_SELECT} WHERE {txn_where(viewer)} AND t.txn_id = %(id)s",
                        {**params(viewer), "id": txn_id}).fetchone()


# ---- balances ---------------------------------------------------------------------------------

def balances(conn: psycopg.Connection, viewer: Viewer) -> list[dict[str, Any]]:
    """The visible part of the team tree in walk order, each member with every balance component."""
    root = ("SELECT member_id FROM field.member WHERE ac_id = %(v_ac)s AND parent_member_id IS NULL"
            if viewer.finance_scope == "all" else "SELECT %(v_me)s::uuid")
    return conn.execute(
        f"""WITH RECURSIVE tree AS (
                SELECT m.member_id, 0 AS depth, ARRAY[m.display_name, m.member_id::text] AS path
                FROM field.member m WHERE m.member_id IN ({root})
                UNION ALL
                SELECT c.member_id, tree.depth + 1, tree.path || ARRAY[c.display_name, c.member_id::text]
                FROM field.member c JOIN tree ON c.parent_member_id = tree.member_id)
            SELECT m.member_id, m.display_name, m.parent_member_id, m.is_principal, m.active, m.finance_scope,
                   tree.depth, bal.*, last.last_entry_date, lag.logging_lag_days, od.overdue_acks
            FROM tree JOIN field.member m ON m.member_id = tree.member_id
            CROSS JOIN LATERAL (SELECT {balance_components_select('m.member_id')} FROM field.money_txn
                                WHERE from_member_id = m.member_id OR to_member_id = m.member_id) bal
            CROSS JOIN LATERAL (SELECT max(txn_date) AS last_entry_date FROM field.money_txn
                                WHERE created_by = m.member_id AND status <> 'void') last
            CROSS JOIN LATERAL ({sm.logging_lag_sql('m.member_id')}) lag
            CROSS JOIN LATERAL (SELECT count(*) AS overdue_acks FROM field.money_txn
                                WHERE status = 'pending_ack' AND 'ack_overdue' = ANY (flagged)
                                  AND (from_member_id = m.member_id OR to_member_id = m.member_id)) od
            WHERE m.member_id IN {visible_members(viewer)}
            ORDER BY tree.path""",
        params(viewer),
    ).fetchall()


# ---- spend summary and tail -------------------------------------------------------------------

@dataclass
class Summary:
    totals: dict[str, Any]
    groups: list[dict[str, Any]]
    reach: list[dict[str, Any]]
    ceiling: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class SpendFilter:
    """The page filters of the spend screens. member_id is the payer for spend, payer or receiver
    for transfers; bucket_id includes the sub-heads of a top-level bucket."""
    date_from: date | None = None
    date_to: date | None = None
    block_id: str | None = None
    area_id: str | None = None
    member_id: UUID | None = None
    bucket_id: int | None = None

    def txn_filter(self) -> TxnFilter:
        return TxnFilter(date_from=self.date_from, date_to=self.date_to, block_id=self.block_id,
                         area_id=self.area_id, member_id=self.member_id, bucket_id=self.bucket_id)


def _spend_where(viewer: Viewer, date_from: date | None, date_to: date | None,
                 sf: SpendFilter | None = None) -> tuple[str, dict[str, Any]]:
    f = (sf or SpendFilter(date_from=date_from, date_to=date_to)).txn_filter()
    where, fp = f.sql()
    return f"{txn_where(viewer)} AND {where}", {**params(viewer), **fp}


def summary(conn: psycopg.Connection, core: CoreAdapter, viewer: Viewer, group_by: list[str], *,
            date_from: date | None, date_to: date | None, limits: LedgerLimits,
            sf: SpendFilter | None = None) -> Summary:
    sf = sf or SpendFilter(date_from=date_from, date_to=date_to)
    where, p = _spend_where(viewer, None, None, sf)
    p["tail_threshold"] = limits.tail_threshold
    p["ac_id"] = viewer.ac_id
    totals = dict(conn.execute(sm.totals_sql(where), p).fetchone())
    # transfers (in transit, disputed) follow the date, place and member filters, not the bucket
    tf = TxnFilter(date_from=sf.date_from, date_to=sf.date_to, block_id=sf.block_id, area_id=sf.area_id,
                   member_id=sf.member_id)
    tw, tp = tf.sql()
    totals.update(conn.execute(transfer_totals_sql(f"{txn_where(viewer)} AND {tw}"), {**p, **tp}).fetchone())

    groups: list[dict[str, Any]] = []
    if group_by:
        keys = [f"{GROUP_KEYS[g]} AS {g}" for g in group_by]
        rows = conn.execute(
            f"""WITH {sm.spend_cte(where)}
                SELECT {', '.join(keys)}, SUM(amount)::numeric(14, 2) AS spend, count(*) AS entries
                FROM spend GROUP BY {', '.join(str(i + 1) for i in range(len(keys)))}
                ORDER BY spend DESC, {', '.join(str(i + 1) for i in range(len(keys)))}""",
            p,
        ).fetchall()
        groups = _label_groups(conn, core, viewer, group_by, rows, totals["spend_total"])

    reach = conn.execute(sm.reach_sql(where), p).fetchall()
    ceiling = conn.execute(sm.ceiling_sql(where), p).fetchall() if viewer.finance_scope == "all" else []
    return Summary(totals=totals, groups=groups, reach=reach, ceiling=ceiling)


def _label_groups(conn, core: CoreAdapter, viewer: Viewer, group_by: list[str], rows, spend_total: Decimal):
    bucket_names = {r["bucket_id"]: r for r in conn.execute("SELECT bucket_id, code, name_hi FROM field.exp_bucket")}
    members = {r["member_id"]: r["display_name"] for r in conn.execute(
        "SELECT member_id, display_name FROM field.member WHERE ac_id = %s", (viewer.ac_id,))}
    blocks = {b.block_id: b.name_hi for b in core.list_blocks(viewer.ac_id)}
    areas = {a.area_id: a.name_hi for a in core.list_areas(viewer.ac_id)}
    out = []
    for r in rows:
        g = dict(r)
        g["share_pct"] = sm.spend_share_pct(r["spend"], spend_total)
        for key in group_by:
            if key in ("bucket", "sub_bucket") and r[key] is not None:
                g[f"{key}_code"] = bucket_names[r[key]]["code"]
                g[f"{key}_name_hi"] = bucket_names[r[key]]["name_hi"]
            elif key == "member":
                g["member_name"] = members.get(r[key])
            elif key == "block":
                g["block_name_hi"] = blocks.get(r[key])
            elif key == "area":
                g["area_name_hi"] = areas.get(r[key])
        if group_by == ["area"]:
            electors = core.electors_for_area(r["area"]) if r["area"] else None
            g["electors"] = electors
            g["spend_per_1000_electors"] = sm.spend_per_1000_electors(r["spend"], electors)
        out.append(g)
    return out


def tail_bands(conn: psycopg.Connection, viewer: Viewer, *, date_from: date | None, date_to: date | None,
               limits: LedgerLimits, sf: SpendFilter | None = None) -> dict[str, Any]:
    where, p = _spend_where(viewer, date_from, date_to, sf)
    p["tail_threshold"] = limits.tail_threshold
    totals = conn.execute(sm.totals_sql(where), p).fetchone()
    return {"tail_threshold": limits.tail_threshold, "bands": conn.execute(sm.size_bands_sql(where), p).fetchall(),
            "tail_count_pct": totals["tail_count_pct"], "tail_value_pct": totals["tail_value_pct"],
            "top_decile_value_pct": totals["top_decile_value_pct"], "spend_total": totals["spend_total"],
            "entries": totals["entries"]}


# ---- money flow -------------------------------------------------------------------------------

def money_flow(conn: psycopg.Connection, viewer: Viewer, sf: SpendFilter) -> dict[str, Any]:
    """Nodes and links for a flow diagram: funds in -> principal -> team (net of returns) -> buckets.

    Transfer links are acknowledged advances minus acknowledged returns per pair; a pair whose net
    is zero or negative is left out (a flow diagram cannot show cycles). Spend links run from the
    payer to the top-level bucket. Every row passes the viewer's finance scope inside the SQL.
    """
    where, p = _spend_where(viewer, None, None, sf)
    tw, tp = TxnFilter(date_from=sf.date_from, date_to=sf.date_to, member_id=sf.member_id).sql()
    p.update(tp)
    scope = txn_where(viewer)
    inflows = conn.execute(
        f"""SELECT t.to_member_id AS target, COALESCE(t.fund_source, 'other') AS source,
                   SUM(t.amount)::numeric(14, 2) AS value
            FROM field.money_txn t WHERE {scope} AND {tw} AND t.kind = 'inflow' AND t.status = 'posted'
            GROUP BY 1, 2 ORDER BY 3 DESC""", p).fetchall()
    transfers = conn.execute(
        f"""SELECT giver, receiver, SUM(v)::numeric(14, 2) AS value FROM (
                SELECT t.from_member_id AS giver, t.to_member_id AS receiver, t.amount AS v
                FROM field.money_txn t WHERE {scope} AND {tw} AND t.kind = 'advance' AND t.status = 'posted'
                UNION ALL
                SELECT t.to_member_id, t.from_member_id, -t.amount
                FROM field.money_txn t WHERE {scope} AND {tw} AND t.kind = 'return' AND t.status = 'posted') x
            GROUP BY 1, 2 HAVING SUM(v) > 0 ORDER BY 3 DESC""", p).fetchall()
    spend = conn.execute(
        f"""WITH {sm.spend_cte(where)}
            SELECT from_member_id AS payer, bucket_id, SUM(amount)::numeric(14, 2) AS value
            FROM spend GROUP BY 1, 2 ORDER BY 3 DESC""", p).fetchall()
    members = {r["member_id"]: r["display_name"] for r in conn.execute(
        "SELECT member_id, display_name FROM field.member WHERE ac_id = %s", (viewer.ac_id,))}
    buckets = {r["bucket_id"]: r for r in conn.execute(
        "SELECT bucket_id, code, name_hi, name_en FROM field.exp_bucket")}
    nodes: dict[str, dict[str, Any]] = {}

    def member(mid) -> str:
        key = f"member:{mid}"
        nodes.setdefault(key, {"id": key, "kind": "member", "member_id": str(mid), "name": members.get(mid)})
        return key

    links = []
    for r in inflows:
        key = f"fund:{r['source']}"
        nodes.setdefault(key, {"id": key, "kind": "fund", "fund_source": r["source"]})
        links.append({"source": key, "target": member(r["target"]), "value": r["value"], "kind": "inflow"})
    for r in transfers:
        links.append({"source": member(r["giver"]), "target": member(r["receiver"]), "value": r["value"],
                      "kind": "advance_net"})
    for r in spend:
        b = buckets[r["bucket_id"]]
        key = f"bucket:{b['code']}"
        nodes.setdefault(key, {"id": key, "kind": "bucket", "bucket_id": b["bucket_id"], "code": b["code"],
                               "name": b["name_hi"], "name_en": b["name_en"]})
        links.append({"source": member(r["payer"]), "target": key, "value": r["value"], "kind": "spend"})
    return {"nodes": list(nodes.values()), "links": links}
