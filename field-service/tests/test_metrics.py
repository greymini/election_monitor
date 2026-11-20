"""Single-source metrics: the Python evaluator and the generated SQL must agree on the same rows."""
from decimal import Decimal

from pulse.metrics.ledger import (in_transit, in_transit_sql, member_balance, member_balance_sql, spend_total,
                                  spend_total_sql)
from tests.helpers import bucket_id, raw_txn


def _fixture_rows(conn, team):
    """Every kind and status, across three members."""
    p, a, w = team["principal"], team["incharge-a"], team["worker-a1"]
    food = bucket_id(conn, "FOOD")
    specs = [
        dict(kind="inflow", amount="200000", to_member_id=p, fund_source="party", created_by=p),
        dict(kind="advance", amount="50000", from_member_id=p, to_member_id=a, status="posted", created_by=p),
        dict(kind="advance", amount="20000", from_member_id=p, to_member_id=a, status="pending_ack", created_by=p),
        dict(kind="advance", amount="7000", from_member_id=p, to_member_id=a, status="disputed", created_by=p),
        dict(kind="advance", amount="9000", from_member_id=p, to_member_id=a, status="void", created_by=p),
        dict(kind="advance", amount="10000", from_member_id=a, to_member_id=w, status="posted", created_by=a),
        dict(kind="advance", amount="3000", from_member_id=a, to_member_id=w, status="pending_ack", created_by=a),
        dict(kind="expense", amount="1500", from_member_id=w, bucket_id=food, created_by=w),
        dict(kind="expense", amount="12000.50", from_member_id=w, bucket_id=food, created_by=w),
        dict(kind="expense", amount="800", from_member_id=w, bucket_id=food, status="void", created_by=w),
        dict(kind="expense", amount="4000", from_member_id=a, bucket_id=food, created_by=a),
        dict(kind="return", amount="1000", from_member_id=w, to_member_id=a, status="posted", created_by=w),
        dict(kind="return", amount="500", from_member_id=w, to_member_id=a, status="pending_ack", created_by=w),
        dict(kind="adjustment", amount="250", to_member_id=w, remark="correction", created_by=p),
        dict(kind="adjustment", amount="100", from_member_id=a, remark="correction", created_by=p),
    ]
    return [raw_txn(conn, **s) for s in specs]


def test_balance_parity_python_vs_sql(conn, team):
    rows = _fixture_rows(conn, team)
    for member in team.values():
        sql = conn.execute(member_balance_sql(), {"member_id": member}).fetchone()["balance"]
        assert sql == member_balance(rows, member), member


def test_in_transit_parity(conn, team):
    rows = _fixture_rows(conn, team)
    for member in team.values():
        sql = conn.execute(in_transit_sql(), {"member_id": member}).fetchone()["in_transit"]
        assert sql == in_transit(rows, member)
    assert in_transit(rows, team["principal"]) == Decimal("20000")


def test_spend_parity(conn, team):
    rows = _fixture_rows(conn, team)
    assert conn.execute(spend_total_sql()).fetchone()["spend_total"] == spend_total(rows) == Decimal("17500.50")


def test_balance_values(conn, team):
    rows = _fixture_rows(conn, team)
    a, w = team["incharge-a"], team["worker-a1"]
    # in-charge: +50000 posted advance +1000 return −10000 −3000 onward advances −4000 expense −100 adjustment
    assert member_balance(rows, a) == Decimal("33900.00")
    # worker: +10000 advance +250 adjustment −1500 −12000.50 expenses −1000 −500 returns
    assert member_balance(rows, w) == Decimal("-4750.50")


def test_only_kind_expense_counts_as_spend(conn, team):
    """Money given is not money spent: advances, returns, inflows and adjustments never count."""
    p, a = team["principal"], team["incharge-a"]
    raw_txn(conn, kind="inflow", amount="100000", to_member_id=p, fund_source="party", created_by=p)
    raw_txn(conn, kind="advance", amount="50000", from_member_id=p, to_member_id=a, created_by=p)
    raw_txn(conn, kind="return", amount="5000", from_member_id=a, to_member_id=p, created_by=a)
    raw_txn(conn, kind="adjustment", amount="300", to_member_id=a, remark="x", created_by=p)
    assert conn.execute(spend_total_sql()).fetchone()["spend_total"] == Decimal("0.00")
    raw_txn(conn, kind="expense", amount="700", from_member_id=a, bucket_id=bucket_id(conn, "FOOD"), created_by=a)
    assert conn.execute(spend_total_sql()).fetchone()["spend_total"] == Decimal("700.00")


def test_no_entries_means_zero_balance(conn, team):
    assert conn.execute(member_balance_sql(), {"member_id": team["worker-b1"]}).fetchone()["balance"] == Decimal("0.00")
