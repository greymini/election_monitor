"""Spend metrics: Python evaluator against SQL on the same rows (single source, LLD §7)."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from pulse.metrics import spend as sm
from pulse.metrics.ledger import balance_components, balance_components_sql
from tests.helpers import bucket_id, raw_txn
from tests.test_metrics import _fixture_rows

D = Decimal
THRESHOLD = D("2000")


def _expenses(conn, team):
    w, a = team["worker-a1"], team["incharge-a"]
    spec = [  # amount, bucket, reach
        ("150", "FOOD.TEA", None), ("800", "FOOD", None), ("2000", "TRAVEL.FUEL", None), ("2000.01", "OTHER", None),
        ("12000", "ASSIST.MEDICAL", (3, "family")), ("30000", "EVENT", (400, "person")), ("500", "OTHER", None),
        ("45000", "WORKS.WATER", (2, "village")), ("1200", "ASSIST", (1, "family")), ("99.50", "TEAM", None),
        ("700", "FOOD", None),
    ]
    rows = []
    for i, (amount, code, reach) in enumerate(spec):
        extra = {"reach_count": reach[0], "reach_unit": reach[1]} if reach else {}
        if code == "OTHER":
            extra["custom_label"] = "misc"
        rows.append(raw_txn(conn, kind="expense", amount=amount, from_member_id=w if i % 2 else a,
                            bucket_id=bucket_id(conn, code), created_by=w if i % 2 else a, **extra))
    raw_txn(conn, kind="expense", amount="9999", from_member_id=w, bucket_id=bucket_id(conn, "FOOD"), created_by=w,
            status="void")                                     # never counted
    raw_txn(conn, kind="advance", amount="50000", from_member_id=team["principal"], to_member_id=a,
            created_by=team["principal"])                      # never counted
    return rows


def _py_rows(conn):
    return conn.execute(sm.spend_rows_sql()).fetchall()


def test_totals_parity(conn, team):
    _expenses(conn, team)
    rows = _py_rows(conn)
    sql = conn.execute(sm.totals_sql(), {"tail_threshold": THRESHOLD}).fetchone()
    assert len(rows) == 11
    assert sql["spend_total"] == sm.total(rows) == D("94449.51")
    assert sql["unclassified_pct"] == sm.unclassified_pct(rows)
    tail = sm.tail(rows, THRESHOLD)
    assert sql["tail_count_pct"] == tail["tail_count_pct"] == D("63.64")        # 7 of 11 at or below 2000
    assert sql["tail_value_pct"] == tail["tail_value_pct"]
    assert sql["top_decile_value_pct"] == sm.top_decile_value_pct(rows)         # ceil(1.1) = 2 entries


def test_reach_parity_per_unit(conn, team):
    _expenses(conn, team)
    rows = _py_rows(conn)
    sql = [dict(r) for r in conn.execute(sm.reach_sql()).fetchall()]
    py = sm.reach(rows)
    assert sql == py
    family = next(r for r in py if r["reach_unit"] == "family")
    assert family["reach_total"] == 4 and family["cost_per_reach"] == D("3300.00")
    assert {r["reach_unit"] for r in py} == {"family", "person", "village"}     # units never added together


def test_size_band_parity(conn, team):
    _expenses(conn, team)
    rows = _py_rows(conn)
    sql = [dict(r) for r in conn.execute(sm.size_bands_sql()).fetchall()]
    assert sql == sm.size_bands(rows)
    assert sum(b["count"] for b in sql) == 11
    assert sm.band_of(D("2000")) == "501-2000" and sm.band_of(D("2000.01")) == "2001-10000"


def test_empty_filter_gives_null_ratios(conn, team):
    sql = conn.execute(sm.totals_sql(), {"tail_threshold": THRESHOLD}).fetchone()
    assert sql["spend_total"] == 0 and sql["entries"] == 0
    for k in ("unclassified_pct", "tail_count_pct", "tail_value_pct", "top_decile_value_pct"):
        assert sql[k] is None
    assert sm.unclassified_pct([]) is None and sm.top_decile_value_pct([]) is None
    assert conn.execute(sm.reach_sql()).fetchall() == [] and sm.reach([]) == []


def test_spend_per_1000_electors_null_when_unknown(core):
    assert sm.spend_per_1000_electors(D("8420"), core.electors_for_area("area-a1")) == D("2000.00")
    assert core.electors_for_area("area-a3") is None
    assert sm.spend_per_1000_electors(D("8420"), core.electors_for_area("area-a3")) is None
    assert sm.spend_per_1000_electors(D("8420"), 0) is None


def test_logging_lag_parity(conn, team):
    w = team["worker-a1"]
    base = datetime(2026, 11, 19, 20, 0, tzinfo=timezone.utc)          # 01:30 IST on the 20th
    for days_back in (0, 1, 3, 10):
        raw_txn(conn, kind="expense", amount="100", from_member_id=w, bucket_id=bucket_id(conn, "FOOD"), created_by=w,
                txn_date=date(2026, 11, 20) - timedelta(days=days_back), created_at=base)
    raw_txn(conn, kind="expense", amount="100", from_member_id=w, bucket_id=bucket_id(conn, "FOOD"), created_by=w,
            txn_date=date(2026, 10, 1), created_at=base, status="void")            # excluded
    entries = conn.execute("SELECT * FROM field.money_txn WHERE created_by = %s AND status <> 'void'", (w,)).fetchall()
    sql = conn.execute(sm.logging_lag_sql(), {"member_id": w}).fetchone()["logging_lag_days"]
    assert sql == sm.logging_lag_days(entries) == D("2.0")                # median of 0, 1, 3, 10
    assert conn.execute(sm.logging_lag_sql(), {"member_id": team["worker-b1"]}).fetchone()["logging_lag_days"] is None


def test_ceiling_parity(conn, team, core, limits):
    from pulse.ledger import service as ledger
    from pulse.ledger.service import ExpenseInput

    conn.execute("""INSERT INTO field.ac_election_window (ac_id, starts_on, ends_on, ceiling_amount)
                    VALUES ('ac-32', current_date - 10, current_date + 10, 4000000)""")
    food = bucket_id(conn, "FOOD")
    for amount, counts in (("100000", None), ("50000", True), ("30000", False)):
        row = ledger.create_expense(conn, core, team["worker-a1"], ExpenseInput(amount=D(amount), bucket_id=food),
                                    limits=limits)
        if counts is not None:
            ledger.set_ceiling(conn, row["txn_id"], team["principal"], counts)
    window = conn.execute("SELECT * FROM field.ac_election_window").fetchone()
    sql = conn.execute(sm.ceiling_sql(), {"ac_id": "ac-32"}).fetchone()
    assert sql["ceiling_used_pct"] == sm.ceiling_used_pct(_py_rows(conn), window) == D("3.75")   # 150000 / 40 lakh
    conn.execute("UPDATE field.ac_election_window SET ceiling_amount = NULL")
    assert conn.execute(sm.ceiling_sql(), {"ac_id": "ac-32"}).fetchone()["ceiling_used_pct"] is None


def test_balance_components_parity_and_sum(conn, team):
    rows = _fixture_rows(conn, team)
    for m in team.values():
        sql = dict(conn.execute(balance_components_sql(), {"member_id": m}).fetchone())
        py = balance_components(rows, m)
        assert sql == py
        assert py["balance"] == py["received"] - py["spent"] - py["passed_on"] - py["returned"] - py["adjusted_out"]
