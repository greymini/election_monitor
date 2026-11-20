from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from pulse.config import LedgerLimits
from pulse.ledger import service as ledger
from pulse.ledger.errors import Forbidden, Invalid, PiiRejected
from pulse.ledger.service import ExpenseInput
from pulse.metrics.ledger import member_balance_sql, spend_total_sql
from tests.helpers import bucket_id

NOW = datetime(2026, 11, 19, 6, 30, tzinfo=timezone.utc)      # 12:00 IST
TODAY = date(2026, 11, 19)


def _create(conn, core, limits, member, now=NOW, **kw):
    kw.setdefault("bucket_id", bucket_id(conn, "FOOD"))
    kw.setdefault("amount", Decimal("1500"))
    return ledger.create_expense(conn, core, member, ExpenseInput(**kw), limits=limits, now=now)


def test_defaults_and_shape(conn, core, limits, team):
    row = _create(conn, core, limits, team["worker-a1"], remark=" chai nashta ")
    assert row["txn_no"] == "32-E-000001"
    assert row["kind"] == "expense" and row["status"] == "posted"
    assert row["from_member_id"] == team["worker-a1"] and row["to_member_id"] is None
    assert row["created_by"] == team["worker-a1"]
    assert row["txn_date"] == TODAY
    assert row["fund_source"] == "advance" and row["pay_mode"] == "cash"
    assert row["amount"] == Decimal("1500.00") and isinstance(row["amount"], Decimal)
    assert row["remark"] == "chai nashta"
    assert row["flagged"] == [] and row["in_election_window"] is False
    assert row["counts_to_ceiling"] is None          # not decided: NULL, not false
    assert row["suggest_source"] == "user" and row["suggest_conf"] is None


def test_principal_defaults_to_own_funds(conn, core, limits, team):
    assert _create(conn, core, limits, team["principal"])["fund_source"] == "own"


def test_today_is_the_ist_date(conn, core, limits, team):
    late = datetime(2026, 11, 19, 19, 0, tzinfo=timezone.utc)   # 00:30 IST on the 20th
    assert _create(conn, core, limits, team["worker-a1"], now=late)["txn_date"] == date(2026, 11, 20)


def test_rule_suggestion_is_recorded(conn, core, limits, team):
    row = _create(conn, core, limits, team["worker-a1"], suggest_source="rule", suggest_conf=Decimal("0.667"))
    assert row["suggest_source"] == "rule" and row["suggest_conf"] == Decimal("0.667")
    with pytest.raises(Invalid):
        _create(conn, core, limits, team["worker-a1"], suggest_source="user", suggest_conf=Decimal("0.5"))


def test_validation(conn, core, limits, team):
    w = team["worker-a1"]
    with pytest.raises(Invalid):
        _create(conn, core, limits, w, amount=1.5)                          # float refused
    with pytest.raises(Invalid):
        _create(conn, core, limits, w, amount=Decimal("0"))
    with pytest.raises(Invalid):
        _create(conn, core, limits, w, txn_date=TODAY + timedelta(days=1))   # future
    with pytest.raises(Invalid):
        _create(conn, core, limits, w, area_id="area-zz")
    with pytest.raises(Invalid):
        _create(conn, core, limits, w, block_id="blk-b", area_id="area-a1")
    with pytest.raises(Invalid):
        _create(conn, core, limits, w, bucket_id=bucket_id(conn, "OTHER"))  # OTHER needs a label
    with pytest.raises(Invalid):
        _create(conn, core, limits, w, custom_label="x")                    # label only for OTHER
    with pytest.raises(Invalid):
        _create(conn, core, limits, w, bucket_id=bucket_id(conn, "OTHER"), custom_label="x" * 41)
    with pytest.raises(Invalid):
        _create(conn, core, limits, w, reach_count=5)


def test_finance_scope_none_cannot_record(conn, core, limits, team):
    conn.execute("UPDATE field.member SET finance_scope = 'none' WHERE member_id = %s", (team["worker-a1"],))
    with pytest.raises(Forbidden):
        _create(conn, core, limits, team["worker-a1"])


@pytest.mark.parametrize("field", ["remark", "payee_label"])
def test_pii_is_rejected(conn, core, limits, team, field):
    with pytest.raises(PiiRejected) as e:
        _create(conn, core, limits, team["worker-a1"], **{field: "call 9876543210"})
    assert e.value.field == field and e.value.kinds == ["mobile"]


def test_pii_in_custom_label_is_rejected(conn, core, limits, team):
    with pytest.raises(PiiRejected):
        _create(conn, core, limits, team["worker-a1"], bucket_id=bucket_id(conn, "OTHER"), custom_label="ABC1234567")


def test_custom_label_is_counted(conn, core, limits, team):
    other = bucket_id(conn, "OTHER")
    _create(conn, core, limits, team["worker-a1"], bucket_id=other, custom_label="Ghadi Marammat")
    _create(conn, core, limits, team["worker-a2"], bucket_id=other, custom_label="ghadi  marammat")
    rows = conn.execute("SELECT label_norm, count FROM field.exp_custom_label").fetchall()
    assert len(rows) == 1 and rows[0]["count"] == 2


# ---- flags ------------------------------------------------------------------------------------

def test_large_flag_at_threshold(conn, core, limits, team):
    assert _create(conn, core, limits, team["worker-a1"], amount=Decimal("24999.99"))["flagged"] == []
    assert _create(conn, core, limits, team["worker-a1"], amount=Decimal("25000"))["flagged"] == ["large"]


def test_backdated_flag_after_seven_days(conn, core, limits, team):
    assert _create(conn, core, limits, team["worker-a1"], txn_date=TODAY - timedelta(days=7))["flagged"] == []
    assert _create(conn, core, limits, team["worker-a1"], txn_date=TODAY - timedelta(days=8))["flagged"] == ["backdated"]


def test_window_flags(conn, core, limits, team):
    conn.execute("""INSERT INTO field.ac_election_window (ac_id, starts_on, ends_on, ceiling_amount, cash_limit)
                    VALUES ('ac-32', '2026-11-01', '2026-11-30', 4000000, 10000)""")
    w = team["worker-a1"]
    row = _create(conn, core, limits, w, amount=Decimal("10000"))
    assert row["in_election_window"] is True and row["flagged"] == []          # at the cash limit: fine
    assert _create(conn, core, limits, w, amount=Decimal("10001"))["flagged"] == ["cash_over_limit"]
    assert _create(conn, core, limits, w, amount=Decimal("10001"), pay_mode="upi")["flagged"] == []
    assist = bucket_id(conn, "ASSIST.MEDICAL")      # sub-head of a sensitive bucket
    assert _create(conn, core, limits, w, bucket_id=assist, amount=Decimal("500"))["flagged"] == ["sensitive_in_window"]
    old = _create(conn, core, limits, w, bucket_id=assist, txn_date=date(2026, 10, 20))   # before the window
    assert old["in_election_window"] is False and old["flagged"] == ["backdated"]


def test_sensitive_outside_window_is_not_flagged(conn, core, limits, team):
    assert _create(conn, core, limits, team["worker-a1"], bucket_id=bucket_id(conn, "ASSIST"))["flagged"] == []


def test_limits_come_from_config(conn, core, team):
    tight = LedgerLimits(large_entry=Decimal("1000"), backdated_days=1)
    row = _create(conn, core, tight, team["worker-a1"], txn_date=TODAY - timedelta(days=2))
    assert row["flagged"] == ["large", "backdated"]


# ---- void and undo ----------------------------------------------------------------------------

def test_undo_by_creator_within_ten_minutes(conn, core, limits, team):
    row = _create(conn, core, limits, team["worker-a1"])
    voided = ledger.void(conn, row["txn_id"], team["worker-a1"], limits=limits, now=NOW + timedelta(minutes=10))
    assert voided["status"] == "void" and voided["void_reason"] == "undo"
    assert voided["voided_by"] == team["worker-a1"]
    audit = conn.execute("SELECT action, old, new FROM field.txn_audit").fetchone()
    assert audit == {"action": "void", "old": {"status": "posted"}, "new": {"status": "void"}}


def test_undo_after_ten_minutes_is_refused(conn, core, limits, team):
    row = _create(conn, core, limits, team["worker-a1"])
    with pytest.raises(Forbidden):
        ledger.void(conn, row["txn_id"], team["worker-a1"], limits=limits, now=NOW + timedelta(minutes=10, seconds=1))


def test_finance_all_may_void_later_with_a_reason(conn, core, limits, team):
    row = _create(conn, core, limits, team["worker-a1"])
    later = NOW + timedelta(days=2)
    with pytest.raises(Invalid):
        ledger.void(conn, row["txn_id"], team["principal"], limits=limits, now=later)          # no reason
    with pytest.raises(Forbidden):
        ledger.void(conn, row["txn_id"], team["incharge-a"], "wrong", limits=limits, now=later)  # finance team
    voided = ledger.void(conn, row["txn_id"], team["principal"], "duplicate entry", limits=limits, now=later)
    assert voided["void_reason"] == "duplicate entry"
    with pytest.raises(Invalid):
        ledger.void(conn, row["txn_id"], team["principal"], "again", limits=limits, now=later)


def test_void_is_excluded_from_balance_and_spend(conn, core, limits, team):
    w = team["worker-a1"]
    keep = _create(conn, core, limits, w, amount=Decimal("1000"))
    gone = _create(conn, core, limits, w, amount=Decimal("400"))
    ledger.void(conn, gone["txn_id"], w, limits=limits, now=NOW)
    assert conn.execute(member_balance_sql(), {"member_id": w}).fetchone()["balance"] == Decimal("-1000.00")
    assert conn.execute(spend_total_sql("ac_id = %(ac)s"), {"ac": "ac-32"}).fetchone()["spend_total"] == Decimal("1000.00")
    assert keep["status"] == "posted"


# ---- reclassify -------------------------------------------------------------------------------

def test_reclassify_writes_audit_and_recomputes_sensitive_flag(conn, core, limits, team):
    conn.execute("INSERT INTO field.ac_election_window (ac_id, starts_on, ends_on) VALUES ('ac-32', '2026-11-01', '2026-11-30')")
    w = team["worker-a1"]
    row = _create(conn, core, limits, w, suggest_source="rule", suggest_conf=Decimal("0.8"))
    out = ledger.reclassify(conn, row["txn_id"], w, bucket_id(conn, "WORKS"), limits=limits, now=NOW)
    assert out["bucket_id"] == bucket_id(conn, "WORKS")
    assert out["suggest_source"] == "user" and out["suggest_conf"] is None
    assert out["flagged"] == ["sensitive_in_window"]
    audit = conn.execute("SELECT action, old, new FROM field.txn_audit").fetchone()
    assert audit["action"] == "reclassify"
    assert audit["old"]["bucket_id"] == bucket_id(conn, "FOOD") and audit["old"]["suggest_source"] == "rule"
    assert out["amount"] == row["amount"] and out["txn_no"] == row["txn_no"]


def test_reclassify_permissions(conn, core, limits, team):
    row = _create(conn, core, limits, team["worker-a1"])
    later = NOW + timedelta(hours=1)
    with pytest.raises(Forbidden):
        ledger.reclassify(conn, row["txn_id"], team["worker-a1"], bucket_id(conn, "TEAM"), limits=limits, now=later)
    ledger.reclassify(conn, row["txn_id"], team["principal"], bucket_id(conn, "TEAM"), limits=limits, now=later)
    ledger.void(conn, row["txn_id"], team["principal"], "x", limits=limits, now=later)
    with pytest.raises(Invalid):
        ledger.reclassify(conn, row["txn_id"], team["principal"], bucket_id(conn, "FOOD"), limits=limits, now=later)
