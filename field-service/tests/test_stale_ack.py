"""Acknowledgement card (flow_ack): taps on a card whose entry moved on, by the wrong person, by a member
who was deactivated or unlinked, and redelivered taps. Never silent, never a second state change."""
from decimal import Decimal

import pytest

from pulse.bot import texts_hi as T
from pulse.ledger import service as ledger
from pulse.metrics.ledger import member_balance_sql
from pulse.team import linking
from pulse.team import service as team_service
from tests import updates as U
from tests.helpers import link
from tests.test_flow_expense import Chat

D = Decimal


@pytest.fixture
def setup(pool, bot, team, conn, core, limits):
    """In-charge A (USER_B) gave worker A1 (USER_C) ₹20,000; waiting for A1's acknowledgement."""
    link(pool, bot, team["incharge-a"], U.USER_B)
    link(pool, bot, team["worker-a1"], U.USER_C)
    row = ledger.create_advance(conn, core, team["incharge-a"], to_member_id=team["worker-a1"], amount=D("20000"),
                                limits=limits)
    return row, Chat(pool, bot, U.USER_B), Chat(pool, bot, U.USER_C)


def _data(row, op):
    return f"k:{row['txn_id'].hex}:{op}"


def _txn(conn, row):
    return conn.execute("SELECT * FROM field.money_txn WHERE txn_id = %s", (row["txn_id"],)).fetchone()


def _bal(conn, member_id):
    return conn.execute(member_balance_sql(), {"member_id": member_id}).fetchone()["balance"]


def _count(conn):
    return conn.execute("SELECT count(*) AS n FROM field.money_txn").fetchone()["n"]


def test_acknowledging_twice_changes_it_once(conn, team, setup):
    row, _, worker = setup
    assert worker.tap_data(_data(row, "y"), 501) == "acknowledged"
    assert _bal(conn, team["worker-a1"]) == D("20000.00")
    assert worker.tap_data(_data(row, "y"), 501) == "ack_already_posted"
    assert worker.toast == T.ALREADY_ACKED
    assert _txn(conn, row)["status"] == "posted" and _bal(conn, team["worker-a1"]) == D("20000.00")
    assert _count(conn) == 1


def test_dispute_after_acknowledge_is_refused(conn, team, setup):
    row, _, worker = setup
    worker.tap_data(_data(row, "y"), 501)
    for op in ("d", "n"):
        assert worker.tap_data(_data(row, op), 501) == "ack_already_posted"
        assert worker.toast == T.ALREADY_ACKED
    assert _txn(conn, row)["status"] == "posted"                         # no regression to disputed


def test_acknowledge_after_dispute_is_refused(conn, team, setup):
    row, _, worker = setup
    assert worker.tap_data(_data(row, "n"), 501) == "disputed"
    assert worker.tap_data(_data(row, "y"), 501) == "ack_already_disputed"
    assert worker.toast == T.ALREADY_DISPUTED
    assert _txn(conn, row)["status"] == "disputed"
    assert _bal(conn, team["worker-a1"]) == D("0.00")                    # a disputed advance credits nobody


def test_tap_after_the_giver_undid_it(conn, team, limits, setup):
    row, _, worker = setup
    ledger.void(conn, row["txn_id"], team["incharge-a"], limits=limits)  # giver's undo while still pending
    assert worker.tap_data(_data(row, "y"), 501) == "ack_already_void"
    assert worker.toast == T.ENTRY_REMOVED
    assert _txn(conn, row)["status"] == "void" and _bal(conn, team["worker-a1"]) == D("0.00")


def test_card_tapped_by_someone_else(conn, team, setup):
    """A forwarded or copied card: the giver (or anyone but the receiver) cannot acknowledge it."""
    row, incharge, _ = setup
    assert incharge.tap_data(_data(row, "y"), 777) == "card_stale"
    assert incharge.toast == T.CARD_STALE
    assert _txn(conn, row)["status"] == "pending_ack"


def test_malformed_or_unknown_card(conn, setup):
    row, _, worker = setup
    for data in ("k:zz:y", f"k:{row['txn_id'].hex}:q", "k:" + "0" * 32 + ":y", "k:only"):
        assert worker.tap_data(data, 501) == "card_stale"
        assert worker.toast == T.CARD_STALE
    assert _txn(conn, row)["status"] == "pending_ack"


def test_deactivated_receiver_cannot_tap(conn, core, team, setup):
    row, _, worker = setup
    team_service.update_member(conn, core, "ac-32", team["worker-a1"], {"active": False})
    assert worker.tap_data(_data(row, "y"), 501) == "unlinked"
    assert worker.text == T.UNREGISTERED and worker.fake.answered            # told, and the tap is answered
    assert _txn(conn, row)["status"] == "pending_ack"


def test_unlinked_receiver_cannot_tap(conn, team, setup):
    row, _, worker = setup
    linking.unlink(conn, "ac-32", team["worker-a1"])
    assert worker.tap_data(_data(row, "y"), 501) == "unlinked"
    assert worker.text == T.UNREGISTERED
    assert _txn(conn, row)["status"] == "pending_ack"


def test_redelivered_tap_changes_nothing(conn, team, setup):
    row, _, worker = setup
    assert worker.tap_data(_data(row, "y"), 501, update_id=990001) == "acknowledged"
    sent = len(worker.fake.sent)
    assert worker.tap_data(_data(row, "y"), 501, update_id=990001) == "duplicate"
    assert len(worker.fake.sent) == sent                                 # nothing sent again
    audit = conn.execute("SELECT count(*) AS n FROM field.txn_audit WHERE txn_id = %s AND action = 'ack'",
                         (row["txn_id"],)).fetchone()["n"]
    assert audit == 1 and _bal(conn, team["worker-a1"]) == D("20000.00")
