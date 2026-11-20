"""Area, activity and follow-up metrics (slice 3): Python and SQL agree; ties share the lower rank;
an area without electors has NULL rates, ranks and gap and does not move the others."""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from pulse.common.clock import utcnow
from pulse.ledger import service as ledger
from pulse.ledger.service import ExpenseInput
from pulse.metrics import activity as act
from pulse.metrics import areas as am
from pulse.metrics import spend as sm
from pulse.reports import access, issues
from tests.helpers import bucket_id
from tests.triage_world import file_report

D = Decimal
BLOCK = {"area-a1": "blk-a", "area-a2": "blk-a", "area-a3": "blk-a", "area-b1": "blk-b", "area-b2": "blk-b",
         "area-b3": "blk-b", "area-c1": "blk-c", "area-c2": "blk-c"}


def test_percent_rank_ties_and_nulls():
    assert am.percent_ranks({"a": D("1"), "b": D("1"), "c": D("3"), "d": None}) == {
        "a": D("0.0"), "b": D("0.0"), "c": D("100.0"), "d": None}
    assert am.percent_ranks({"a": D("5"), "b": None}) == {"a": None, "b": None}       # fewer than two
    assert am.percent_ranks({"a": D("0"), "b": D("2"), "c": D("2"), "d": D("9")}) == {
        "a": D("0.0"), "b": D("33.3"), "c": D("33.3"), "d": D("100.0")}
    assert am.per_1000(5, None) is None and am.per_1000(5, 0) is None
    assert am.reports_per_1000_electors(3, 4210) == D("0.71")


def _world(conn, core, limits, team):
    """Reports and spend in a few areas; area-a3 has no elector count in the stub core."""
    for area, n in (("area-a1", 3), ("area-a2", 1), ("area-a3", 2), ("area-b1", 1)):
        for _ in range(n):
            file_report(conn, core, team["worker-a1"], area=(BLOCK[area], area))
    closed = file_report(conn, core, team["worker-a1"], area=("blk-b", "area-b2"))
    conn.execute("UPDATE field.report SET status = 'resolved' WHERE report_id = %s", (closed["report_id"],))
    food = bucket_id(conn, "FOOD")
    for area, amount in (("area-a1", "1000"), ("area-b1", "20000"), ("area-b2", "5000"), ("area-a3", "999"),
                         ("area-c1", "3000")):
        ledger.create_expense(conn, core, team["principal"], ExpenseInput(
            amount=D(amount), bucket_id=food, block_id=BLOCK[area], area_id=area), limits=limits)
    gone = ledger.create_expense(conn, core, team["principal"], ExpenseInput(
        amount=D("70000"), bucket_id=food, block_id="blk-a", area_id="area-a2"), limits=limits)
    ledger.void(conn, gone["txn_id"], team["principal"], "undo", limits=limits)
    v = access.viewer_for(conn, team["principal"])
    issues.create(conn, core, v, now=utcnow(), title="डेमो", area_id="area-a1")


def _python_side(conn, core):
    reports = conn.execute("SELECT status, area_id, category_id FROM field.report WHERE ac_id = 'ac-32'").fetchall()
    spend = conn.execute(sm.spend_rows_sql("t.ac_id = 'ac-32'")).fetchall()
    open_issues = conn.execute("SELECT area_id FROM field.issue WHERE status = 'open'").fetchall()
    rows = []
    for a in core.list_areas("ac-32"):
        rows.append({"area_id": a.area_id, "electors": core.electors_for_area(a.area_id),
                     "open_reports": sum(n for (area, _), n in am.reports_open(reports).items() if area == a.area_id),
                     "open_issues": sum(1 for i in open_issues if i["area_id"] == a.area_id),
                     "spend": sm.total([s for s in spend if s["area_id"] == a.area_id])})
    return am.need_vs_spend(rows)


def test_need_vs_spend_parity(conn, core, limits, team):
    _world(conn, core, limits, team)
    sql = conn.execute(am.need_vs_spend_sql(sm.spend_cte("t.ac_id = %(nv_ac)s")), am.area_params(core, "ac-32")).fetchall()
    py = _python_side(conn, core)
    keys = ("area_id", "electors", "open_reports", "open_issues", "spend", "need_per_1000", "spend_per_1000",
            "need_rank", "spend_rank", "rank_gap")
    assert [{k: r[k] for k in keys} for r in sql] == [{k: r[k] for k in keys} for r in py]
    by = {r["area_id"]: r for r in sql}
    a3 = by["area-a3"]
    assert a3["electors"] is None and a3["open_reports"] == 2 and a3["spend"] == D("999.00")
    assert a3["need_per_1000"] is None and a3["need_rank"] is None and a3["rank_gap"] is None
    # seven areas take part; four have no open reports: a tie at the bottom, rank 0
    assert [by[a]["need_rank"] for a in ("area-b2", "area-b3", "area-c1", "area-c2")] == [D("0.0")] * 4
    assert by["area-a1"]["need_rank"] == D("100.0")                     # 3 open in 4210 electors: the highest
    assert by["area-a2"]["spend"] == D("0.00")                          # the void entry is not spend
    assert by["area-a2"]["rank_gap"] == by["area-a2"]["need_rank"] - by["area-a2"]["spend_rank"]
    assert sql[0]["rank_gap"] == max(r["rank_gap"] for r in sql if r["rank_gap"] is not None)
    assert by["area-a1"]["open_issues"] == 1


def test_reports_open_parity(conn, core, limits, team):
    _world(conn, core, limits, team)
    rows = conn.execute("SELECT status, area_id, category_id FROM field.report").fetchall()
    sql = {(r["area_id"], r["category_id"]): r["n"] for r in conn.execute(am.reports_open_sql("r.ac_id = 'ac-32'"))}
    assert sql == am.reports_open(rows) and sum(sql.values()) == 7


def test_member_active_days_parity(conn, core, limits, team):
    w = team["worker-a1"]
    now = utcnow()
    food = bucket_id(conn, "FOOD")
    for days_ago in (0, 0, 2, 13, 14, 20):
        ledger.create_expense(conn, core, w, ExpenseInput(amount=D("100"), bucket_id=food,
                                                          txn_date=(now - timedelta(days=days_ago + 1)).date()),
                              limits=limits, now=now - timedelta(days=days_ago))
    file_report(conn, core, w, now=now - timedelta(days=5))
    file_report(conn, core, w, now=now - timedelta(days=2))             # same day as an entry
    today = now.astimezone(act.IST).date()
    moments = [r["created_at"] for r in conn.execute(
        "SELECT created_at FROM field.money_txn WHERE created_by = %s AND status <> 'void'", (w,))]
    moments += [r["filed_at"] for r in conn.execute(
        "SELECT filed_at FROM field.report WHERE reporter_member_id = %s AND status <> 'draft'", (w,))]
    sql = conn.execute(act.active_days_sql(), {"member_id": w, "today": today}).fetchone()["active_days"]
    assert sql == act.member_active_days(moments, today) == 4             # days 0, 2, 5, 13
    assert conn.execute(act.active_days_sql(), {"member_id": team["worker-b1"], "today": today}).fetchone()["active_days"] == 0


def test_followup_done_pct_parity(conn, core, limits, team):
    assist = bucket_id(conn, "ASSIST")
    txns = [ledger.create_expense(conn, core, team["worker-a1"], ExpenseInput(amount=D("500"), bucket_id=assist),
                                  limits=limits) for _ in range(5)]
    now = utcnow()
    states = [("asked", "done"), ("asked", "partial"), ("asked", None), ("asked", "done"), ("cancelled", None)]
    for t, (s, answer) in zip(txns, states):
        conn.execute("UPDATE field.txn_followup SET asked_at = %s, answer = %s, answered_at = %s, cancelled_at = %s "
                     "WHERE txn_id = %s", (now, answer, now if answer else None, now if s == "cancelled" else None,
                                           t["txn_id"]))
    rows = conn.execute("SELECT * FROM field.txn_followup").fetchall()
    sql = conn.execute(act.followup_sql()).fetchone()
    py = act.followup_done_pct(rows)
    assert dict(sql) == py
    assert py == {"asked": 4, "answered": 3, "done": 2, "done_pct": D("66.67"), "coverage_pct": D("75.00")}
    conn.execute("UPDATE field.txn_followup SET answer = NULL, answered_at = NULL")
    empty = act.followup_done_pct(conn.execute("SELECT * FROM field.txn_followup").fetchall())
    assert empty["done_pct"] is None and empty["coverage_pct"] == D("0.00")   # unanswered: missing, not "no"
