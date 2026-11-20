"""Advances, acknowledgement, dispute, returns, inflows, adjustments and the ceiling decision."""
import threading
from decimal import Decimal

import pytest

from pulse.ledger import service as ledger
from pulse.ledger.errors import Forbidden, Invalid
from pulse.ledger.service import ExpenseInput
from pulse.metrics.ledger import balance_components_sql, member_balance_sql
from pulse.team import service as team_service
from tests.helpers import bucket_id

D = Decimal


def bal(conn, m):
    return conn.execute(member_balance_sql(), {"member_id": m}).fetchone()["balance"]


def comp(conn, m):
    return conn.execute(balance_components_sql(), {"member_id": m}).fetchone()


def adv(conn, core, limits, giver, to, amount="50000", **kw):
    return ledger.create_advance(conn, core, giver, to_member_id=to, amount=D(amount), limits=limits, **kw)


# ---- advance rules ----------------------------------------------------------------------------

def test_advance_starts_pending_and_is_in_transit(conn, core, limits, team):
    p, a = team["principal"], team["incharge-a"]
    row = adv(conn, core, limits, p, a)
    assert row["kind"] == "advance" and row["status"] == "pending_ack" and row["txn_no"] == "32-A-000001"
    assert row["flagged"] == ["large"] and row["bucket_id"] is None
    assert comp(conn, p)["passed_on"] == D("50000") and comp(conn, p)["in_transit"] == D("50000")
    assert bal(conn, a) == D("0.00")                        # not credited until acknowledged


def test_giver_needs_can_give_advance(conn, core, limits, team):
    with pytest.raises(Forbidden):
        adv(conn, core, limits, team["worker-a1"], team["worker-a2"])


def test_receiver_must_be_in_givers_subtree(conn, core, limits, team):
    a = team["incharge-a"]
    adv(conn, core, limits, a, team["worker-a1"], "1000")
    with pytest.raises(Forbidden):
        adv(conn, core, limits, a, team["worker-b1"], "1000")        # other branch
    with pytest.raises(Forbidden):
        adv(conn, core, limits, a, team["principal"], "1000")        # upwards
    adv(conn, core, limits, team["principal"], team["worker-b1"], "1000")   # principal: anyone


def test_finance_all_gives_outside_subtree(conn, core, limits, team):
    b, w = team["incharge-b"], team["worker-a1"]                       # worker A1 is in the other branch
    with pytest.raises(Forbidden):
        adv(conn, core, limits, b, w, "1000")                           # finance scope team: refused
    conn.execute("UPDATE field.member SET finance_scope = 'all' WHERE member_id = %s", (b,))
    row = adv(conn, core, limits, b, w, "1000")                         # finance scope all: allowed
    assert (row["from_member_id"], row["to_member_id"], row["status"]) == (b, w, "pending_ack")
    assert row["amount"] == D("1000.00")
    assert comp(conn, b)["passed_on"] == D("1000") and comp(conn, b)["in_transit"] == D("1000")
    assert bal(conn, w) == D("0.00")                                    # credited only on acknowledgement
    ledger.acknowledge(conn, row["txn_id"], w)
    assert bal(conn, w) == D("1000.00")


def test_receiver_checks(conn, core, limits, team):
    p = team["principal"]
    with pytest.raises(Invalid):
        adv(conn, core, limits, p, p)
    conn.execute("UPDATE field.member SET finance_scope = 'none' WHERE member_id = %s", (team["worker-b1"],))
    with pytest.raises(Invalid):
        adv(conn, core, limits, p, team["worker-b1"])
    conn.execute("UPDATE field.member SET active = false, deactivated_at = now() WHERE member_id = %s", (team["worker-a2"],))
    with pytest.raises(Forbidden):
        adv(conn, core, limits, p, team["worker-a2"])


def test_eligible_receivers_direct_reports_first(conn, core, limits, team):
    names = [r["display_name"] for r in ledger.eligible_receivers(conn, team["incharge-a"])]
    assert set(names) == {"डेमो कार्यकर्ता क1", "डेमो कार्यकर्ता क2"}
    adv(conn, core, limits, team["principal"], team["incharge-b"], "100")
    rows = ledger.eligible_receivers(conn, team["principal"])
    assert [r["is_direct"] for r in rows][:2] == [True, True]
    assert rows[0]["member_id"] == team["incharge-b"]                    # most recently given first
    assert len(rows) == 5


# ---- acknowledgement and dispute --------------------------------------------------------------

def test_acknowledge_credits_receiver(conn, core, limits, team):
    p, a = team["principal"], team["incharge-a"]
    row = adv(conn, core, limits, p, a)
    out = ledger.acknowledge(conn, row["txn_id"], a)
    assert out["status"] == "posted"
    assert bal(conn, a) == D("50000.00") and comp(conn, p)["in_transit"] == 0
    audit = conn.execute("SELECT action, actor_member_id, new FROM field.txn_audit").fetchone()
    assert audit["action"] == "ack" and audit["actor_member_id"] == a and audit["new"]["on_behalf_of"] is None
    with pytest.raises(Invalid):
        ledger.acknowledge(conn, row["txn_id"], a)                       # exactly once


def test_only_receiver_or_finance_all_may_answer(conn, core, limits, team):
    row = adv(conn, core, limits, team["incharge-a"], team["worker-a1"], "2000")
    with pytest.raises(Forbidden):
        ledger.acknowledge(conn, row["txn_id"], team["incharge-a"])      # the giver cannot
    with pytest.raises(Forbidden):
        ledger.acknowledge(conn, row["txn_id"], team["worker-a2"])
    ledger.acknowledge(conn, row["txn_id"], team["principal"], note="confirmed by phone")
    audit = conn.execute("SELECT new, note FROM field.txn_audit WHERE action = 'ack'").fetchone()
    assert audit["new"]["on_behalf_of"] == str(team["worker-a1"]) and audit["note"] == "confirmed by phone"


def test_dispute_counts_on_neither_side(conn, core, limits, team):
    p, a = team["principal"], team["incharge-a"]
    row = adv(conn, core, limits, p, a)
    out = ledger.dispute(conn, row["txn_id"], a, "not_received")
    assert out["status"] == "disputed"
    assert comp(conn, p)["passed_on"] == 0 and bal(conn, a) == 0
    assert comp(conn, p)["disputed"] == D("50000") and comp(conn, a)["disputed"] == D("50000")
    assert conn.execute("SELECT new FROM field.txn_audit").fetchone()["new"]["reason"] == "not_received"
    with pytest.raises(Invalid):
        ledger.acknowledge(conn, row["txn_id"], a)                       # resolved by void and re-entry
    with pytest.raises(Invalid):
        ledger.dispute(conn, adv(conn, core, limits, p, a)["txn_id"], a, "wrong")


# ---- returns ----------------------------------------------------------------------------------

def _funded_worker(conn, core, limits, team, amount="10000"):
    row = adv(conn, core, limits, team["incharge-a"], team["worker-a1"], amount)
    ledger.acknowledge(conn, row["txn_id"], team["worker-a1"])
    return team["worker-a1"]


def test_return_to_most_recent_giver_and_confirmed_by_them(conn, core, limits, team):
    w = _funded_worker(conn, core, limits, team)
    row = ledger.create_return(conn, core, w, amount=D("4000"), limits=limits)
    assert row["to_member_id"] == team["incharge-a"] and row["status"] == "pending_ack" and row["txn_no"] == "32-L-000001"
    assert bal(conn, w) == D("6000.00")
    ledger.acknowledge(conn, row["txn_id"], team["incharge-a"])
    assert bal(conn, team["incharge-a"]) == D("-6000.00")                # gave 10000, got 4000 back


def test_return_rules(conn, core, limits, team):
    with pytest.raises(Invalid):                                          # nobody gave them anything
        ledger.create_return(conn, core, team["worker-b1"], amount=D("10"), limits=limits)
    w = _funded_worker(conn, core, limits, team)
    with pytest.raises(Invalid):
        ledger.create_return(conn, core, w, amount=D("10000.01"), limits=limits)
    with pytest.raises(Invalid):
        ledger.create_return(conn, core, w, amount=D("10"), to_member_id=team["principal"], limits=limits)
    ledger.create_expense(conn, core, w, ExpenseInput(amount=D("9000"), bucket_id=bucket_id(conn, "FOOD")), limits=limits)
    with pytest.raises(Invalid):
        ledger.create_return(conn, core, w, amount=D("1001"), limits=limits)
    ledger.create_return(conn, core, w, amount=D("1000"), limits=limits)


def test_pending_advance_cannot_be_returned(conn, core, limits, team):
    adv(conn, core, limits, team["incharge-a"], team["worker-a1"], "5000")   # not acknowledged
    with pytest.raises(Invalid):
        ledger.create_return(conn, core, team["worker-a1"], amount=D("100"), limits=limits)


def test_simultaneous_returns_cannot_overdraw(pool, core, limits, team):
    with pool.connection() as conn, conn.transaction():
        w = _funded_worker(conn, core, limits, team, "1000")
    barrier = threading.Barrier(2)
    results = []

    def go():
        barrier.wait()
        try:
            with pool.connection() as conn, conn.transaction():
                ledger.create_return(conn, core, w, amount=D("1000"), limits=limits)
            results.append("ok")
        except Invalid:
            results.append("refused")

    threads = [threading.Thread(target=go) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == ["ok", "refused"]


# ---- undo of transfers ------------------------------------------------------------------------

def test_transfer_undo_only_while_pending(conn, core, limits, team):
    a, w = team["incharge-a"], team["worker-a1"]
    pending = adv(conn, core, limits, a, w, "500")
    assert ledger.void(conn, pending["txn_id"], a, limits=limits)["void_reason"] == "undo"
    acked = adv(conn, core, limits, a, w, "500")
    ledger.acknowledge(conn, acked["txn_id"], w)
    with pytest.raises(Forbidden):
        ledger.void(conn, acked["txn_id"], a, limits=limits)
    ledger.void(conn, acked["txn_id"], team["principal"], "entered twice", limits=limits)


# ---- treasurer entries ------------------------------------------------------------------------

def test_inflow_and_adjustment_need_finance_all(conn, core, limits, team):
    p, a = team["principal"], team["incharge-a"]
    with pytest.raises(Forbidden):
        ledger.create_inflow(conn, core, a, to_member_id=a, amount=D("100"), fund_source="party", limits=limits)
    row = ledger.create_inflow(conn, core, p, to_member_id=p, amount=D("200000"), fund_source="party", limits=limits)
    assert row["kind"] == "inflow" and row["status"] == "posted" and row["txn_no"] == "32-I-000001"
    with pytest.raises(Invalid):
        ledger.create_inflow(conn, core, p, to_member_id=p, amount=D("1"), fund_source="advance", limits=limits)
    with pytest.raises(Invalid):
        ledger.create_adjustment(conn, core, p, amount=D("50"), remark=" ", to_member_id=a, limits=limits)
    with pytest.raises(Invalid):
        ledger.create_adjustment(conn, core, p, amount=D("50"), remark="x", limits=limits)
    adj = ledger.create_adjustment(conn, core, p, amount=D("50"), remark="cash count", to_member_id=a, limits=limits)
    assert adj["txn_no"] == "32-J-000001" and bal(conn, a) == D("50.00")


def test_ceiling_decision(conn, core, limits, team):
    p, w = team["principal"], team["worker-a1"]
    food = bucket_id(conn, "FOOD")
    outside = ledger.create_expense(conn, core, w, ExpenseInput(amount=D("100"), bucket_id=food), limits=limits)
    with pytest.raises(Invalid):
        ledger.set_ceiling(conn, outside["txn_id"], p, True)
    conn.execute("INSERT INTO field.ac_election_window (ac_id, starts_on, ends_on, ceiling_amount) "
                 "VALUES ('ac-32', current_date - 5, current_date + 5, 4000000)")
    inside = ledger.create_expense(conn, core, w, ExpenseInput(amount=D("100"), bucket_id=food), limits=limits)
    with pytest.raises(Forbidden):
        ledger.set_ceiling(conn, inside["txn_id"], w, False)
    assert ledger.set_ceiling(conn, inside["txn_id"], p, False)["counts_to_ceiling"] is False
    assert conn.execute("SELECT new FROM field.txn_audit WHERE action = 'ceiling'").fetchone()["new"] == {"counts_to_ceiling": False}


def test_expense_on_behalf_needs_finance_all(conn, core, limits, team):
    food = bucket_id(conn, "FOOD")
    row = ledger.create_expense(conn, core, team["principal"], ExpenseInput(amount=D("300"), bucket_id=food,
                                created_via="web"), limits=limits, payer_member_id=team["worker-a1"])
    assert row["from_member_id"] == team["worker-a1"] and row["created_by"] == team["principal"]
    assert row["fund_source"] == "advance"                               # payer's default, not the actor's
    with pytest.raises(Forbidden):
        ledger.create_expense(conn, core, team["incharge-a"], ExpenseInput(amount=D("300"), bucket_id=food),
                              limits=limits, payer_member_id=team["worker-a1"])


# ---- housekeeping -----------------------------------------------------------------------------

def test_new_member_defaults_to_finance_own(conn, core):
    row = team_service.create_member(conn, core, "ac-32", user_id="usr-principal", display_name="x", is_principal=True)
    assert row["finance_scope"] == "own"
    conn.execute("INSERT INTO field.member (ac_id, user_id, display_name, is_principal) VALUES ('ac-99', 'u-x', 'y', true)")
    assert conn.execute("SELECT finance_scope FROM field.member WHERE user_id = 'u-x'").fetchone()["finance_scope"] == "own"
