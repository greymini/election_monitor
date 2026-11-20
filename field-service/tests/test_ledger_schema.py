"""Per-kind CHECK constraints, the append-only trigger and the concurrency-safe txn_no counter."""
import threading

import psycopg
import pytest

from pulse.ledger import service as ledger
from pulse.ledger.service import ExpenseInput
from tests.helpers import bucket_id, raw_txn

E = psycopg.errors


def _bad(conn, err=E.CheckViolation, **kw):
    with pytest.raises(err):
        with conn.transaction():
            raw_txn(conn, **kw)


def test_expense_shape(conn, team):
    p, w = team["principal"], team["worker-a1"]
    food = bucket_id(conn, "FOOD")
    raw_txn(conn, kind="expense", from_member_id=w, bucket_id=food, created_by=w)
    _bad(conn, kind="expense", from_member_id=w, created_by=w)                                   # no bucket
    _bad(conn, kind="expense", bucket_id=food, created_by=w)                                     # no payer
    _bad(conn, kind="expense", from_member_id=w, to_member_id=p, bucket_id=food, created_by=w)   # has receiver
    _bad(conn, kind="expense", from_member_id=w, bucket_id=food, created_by=w, status="pending_ack")


def test_advance_and_return_shape(conn, team):
    p, w = team["principal"], team["worker-a1"]
    for kind in ("advance", "return"):
        raw_txn(conn, kind=kind, from_member_id=p, to_member_id=w, created_by=p, status="pending_ack")
        _bad(conn, kind=kind, from_member_id=p, to_member_id=p, created_by=p)            # to self
        _bad(conn, kind=kind, from_member_id=p, created_by=p)                            # no receiver
        _bad(conn, kind=kind, from_member_id=p, to_member_id=w, created_by=p, bucket_id=bucket_id(conn, "FOOD"))


def test_inflow_shape(conn, team):
    p = team["principal"]
    raw_txn(conn, kind="inflow", to_member_id=p, fund_source="party", created_by=p)
    _bad(conn, kind="inflow", to_member_id=p, created_by=p)                                  # no fund source
    _bad(conn, kind="inflow", from_member_id=p, to_member_id=team["worker-a1"], fund_source="party", created_by=p)


def test_adjustment_shape(conn, team):
    p = team["principal"]
    raw_txn(conn, kind="adjustment", to_member_id=p, remark="treasurer correction", created_by=p)
    _bad(conn, kind="adjustment", to_member_id=p, created_by=p)                               # no remark
    _bad(conn, kind="adjustment", created_by=p, remark="x")                                   # nobody
    _bad(conn, kind="adjustment", to_member_id=p, remark="x", created_by=p, status="pending_ack")


def test_value_lists_and_other_checks(conn, team):
    w = team["worker-a1"]
    food = bucket_id(conn, "FOOD")
    base = dict(kind="expense", from_member_id=w, bucket_id=food, created_by=w)
    _bad(conn, **base, amount="0")
    _bad(conn, **base, amount="-5")
    _bad(conn, **base, pay_mode="bitcoin")
    _bad(conn, **base, fund_source="lottery")
    _bad(conn, **base, reach_count=5)                         # count without unit
    _bad(conn, **base, reach_count=0, reach_unit="person")
    _bad(conn, **base, flagged=["mystery"])
    _bad(conn, **base, custom_label="x" * 41)
    _bad(conn, **base, suggest_source="user", suggest_conf="0.9")   # confidence only for machine output
    _bad(conn, kind="advance", from_member_id=w, to_member_id=team["principal"], created_by=w, custom_label="x")
    _bad(conn, **base, status="void", void_reason=None)       # void without a reason
    with pytest.raises(E.NumericValueOutOfRange):
        with conn.transaction():
            raw_txn(conn, **base, amount="10000000000")


def test_append_only(conn, team):
    w = team["worker-a1"]
    row = raw_txn(conn, kind="expense", from_member_id=w, bucket_id=bucket_id(conn, "FOOD"), created_by=w)
    for sql in ("UPDATE field.money_txn SET amount = 1 WHERE txn_id = %s",
                "UPDATE field.money_txn SET kind = 'advance' WHERE txn_id = %s",
                "UPDATE field.money_txn SET txn_date = txn_date - 1 WHERE txn_id = %s",
                "UPDATE field.money_txn SET remark = 'edited' WHERE txn_id = %s",
                "DELETE FROM field.money_txn WHERE txn_id = %s"):
        with pytest.raises(E.RestrictViolation):
            with conn.transaction():
                conn.execute(sql, (row["txn_id"],))
    # re-classification is the permitted change
    conn.execute("UPDATE field.money_txn SET bucket_id = %s WHERE txn_id = %s", (bucket_id(conn, "TRAVEL"), row["txn_id"]))
    # and a void row is frozen
    conn.execute("UPDATE field.money_txn SET status='void', void_reason='x', voided_by=%s, voided_at=now() WHERE txn_id=%s",
                 (w, row["txn_id"]))
    with pytest.raises(E.RestrictViolation):
        with conn.transaction():
            conn.execute("UPDATE field.money_txn SET bucket_id = %s WHERE txn_id = %s", (bucket_id(conn, "FOOD"), row["txn_id"]))


def test_replacement_must_point_at_a_void_entry(conn, team):
    w = team["worker-a1"]
    food = bucket_id(conn, "FOOD")
    live = raw_txn(conn, kind="expense", from_member_id=w, bucket_id=food, created_by=w)
    _bad(conn, kind="expense", from_member_id=w, bucket_id=food, created_by=w, voids_txn_id=live["txn_id"])
    gone = raw_txn(conn, kind="expense", from_member_id=w, bucket_id=food, created_by=w, status="void")
    raw_txn(conn, kind="expense", from_member_id=w, bucket_id=food, created_by=w, voids_txn_id=gone["txn_id"])


def test_election_windows_must_not_overlap(conn):
    conn.execute("INSERT INTO field.ac_election_window (ac_id, starts_on, ends_on) VALUES ('ac-32', '2026-10-01', '2026-11-30')")
    with pytest.raises(E.ExclusionViolation):
        with conn.transaction():
            conn.execute("INSERT INTO field.ac_election_window (ac_id, starts_on, ends_on) VALUES ('ac-32', '2026-11-30', '2026-12-10')")
    conn.execute("INSERT INTO field.ac_election_window (ac_id, starts_on, ends_on) VALUES ('ac-99', '2026-11-01', '2026-11-30')")
    with pytest.raises(E.CheckViolation):
        with conn.transaction():
            conn.execute("INSERT INTO field.ac_election_window (ac_id, starts_on, ends_on) VALUES ('ac-32', '2027-02-02', '2027-02-01')")


def test_txn_no_unique_and_gapless_under_concurrent_inserts(pool, core, limits, team):
    """20 threads post at once; every number 32-E-000001..000020 appears exactly once."""
    with pool.connection() as c:
        food = bucket_id(c, "FOOD")
    members = [team["worker-a1"], team["worker-a2"], team["worker-b1"], team["incharge-a"]]
    barrier = threading.Barrier(20)
    results, errors = [], []

    def post(i):
        try:
            barrier.wait()
            with pool.connection() as conn, conn.transaction():
                row = ledger.create_expense(conn, core, members[i % 4], ExpenseInput(amount=100 + i, bucket_id=food),
                                            limits=limits)
                results.append(row["txn_no"])
        except Exception as e:  # pragma: no cover - surfaced by the assert below
            errors.append(e)

    threads = [threading.Thread(target=post, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert sorted(results) == [f"32-E-{n:06d}" for n in range(1, 21)]


def test_txn_no_rolls_back_with_its_insert(pool, core, limits, team):
    with pool.connection() as c:
        food = bucket_id(c, "FOOD")
    with pytest.raises(RuntimeError):
        with pool.connection() as conn, conn.transaction():
            ledger.create_expense(conn, core, team["worker-a1"], ExpenseInput(amount=100, bucket_id=food), limits=limits)
            raise RuntimeError("abort")
    with pool.connection() as conn, conn.transaction():
        row = ledger.create_expense(conn, core, team["worker-a1"], ExpenseInput(amount=100, bucket_id=food), limits=limits)
    assert row["txn_no"] == "32-E-000001"
