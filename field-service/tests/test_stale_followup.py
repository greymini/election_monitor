"""Stale buttons on impact follow-up cards (pulse/bot/flow_followup.py): the question answered already,
the expense voided after the question went out, the card tapped by someone else or by an unlinked
member, the same callback delivered twice, an old "no photo" button, and a photo after the window."""
from __future__ import annotations

import itertools
from datetime import timedelta

from pulse.bot import texts_hi as T
from pulse.ledger import service as ledger
from pulse.team import linking
from tests import updates as U
from tests.helpers import link
from tests.test_flow_report import Reporter
from tests.test_followup import ASK_DAY, ENTERED, _expense, _f, _run, linked  # noqa: F401 (fixture)

_ids = itertools.count(930_000)


def _asks(fake_tg, user=U.USER_A):
    return [m for m in fake_tg.to(user["id"]) if m["reply_markup"] and m["text"].startswith("काम पूरा हुआ?")]


def test_same_callback_delivered_twice(conn, core, limits, team, worker, drain, linked, fake_tg):
    txn = _expense(conn, core, limits, team["worker-a1"])
    _run(worker, drain, ASK_DAY)
    [card] = _asks(fake_tg)
    data, uid = linked.data_for(T.BTN_FOLLOWUP_DONE, card), next(_ids)
    assert linked.tap_data(data, card["message_id"], update_id=uid) == "followup_answered"
    answered_at = _f(conn, txn)["answered_at"]
    assert linked.tap_data(data, card["message_id"], update_id=uid) == "duplicate"
    assert _f(conn, txn)["answered_at"] == answered_at
    assert conn.execute("SELECT count(*) AS n FROM field.tg_session WHERE flow = 'followup'").fetchone()["n"] == 1


def test_answer_after_the_expense_was_voided(conn, core, limits, team, worker, drain, linked, fake_tg):
    """Voided after the question went out: the card says the question is closed, not "already answered"."""
    txn = _expense(conn, core, limits, team["worker-a1"])
    _run(worker, drain, ASK_DAY)
    [card] = _asks(fake_tg)
    ledger.void(conn, txn["txn_id"], team["principal"], "entered twice", limits=limits, now=ASK_DAY + timedelta(hours=1))
    assert linked.tap(T.BTN_FOLLOWUP_DONE, card=card, now=ASK_DAY + timedelta(hours=2)) == "followup_closed"
    assert fake_tg.edits[-1]["text"] == T.FOLLOWUP_CLOSED
    f = _f(conn, txn)
    assert f["answer"] is None and f["cancelled_at"] is not None
    assert conn.execute("SELECT count(*) AS n FROM field.tg_session WHERE flow = 'followup'").fetchone()["n"] == 0


def test_card_tapped_by_another_member(conn, pool, bot, core, limits, team, worker, drain, linked, fake_tg):
    txn = _expense(conn, core, limits, team["worker-a1"])
    _run(worker, drain, ASK_DAY)
    [card] = _asks(fake_tg)
    data = linked.data_for(T.BTN_FOLLOWUP_DONE, card)
    link(pool, bot, team["worker-a2"], U.USER_B)
    other = Reporter(pool, bot, U.USER_B)
    assert other.tap_data(data, card["message_id"]) == "not_allowed"
    assert other.toast == T.NOT_ALLOWED
    assert _f(conn, txn)["answer"] is None


def test_unlinked_member_taps_the_card(conn, core, limits, team, worker, drain, linked, fake_tg):
    txn = _expense(conn, core, limits, team["worker-a1"])
    _run(worker, drain, ASK_DAY)
    [card] = _asks(fake_tg)
    linking.unlink(conn, "ac-32", team["worker-a1"])
    assert linked.tap(T.BTN_FOLLOWUP_DONE, card=card) == "unlinked"
    assert linked.text == T.UNREGISTERED
    assert _f(conn, txn)["answer"] is None


def test_old_no_photo_button_leaves_a_newer_answer_alone(conn, core, limits, team, worker, drain, linked, fake_tg):
    first = _expense(conn, core, limits, team["worker-a1"])
    second = _expense(conn, core, limits, team["worker-a1"], "WORKS", now=ENTERED + timedelta(minutes=5))
    _run(worker, drain, ASK_DAY + timedelta(minutes=10))
    one, two = sorted(_asks(fake_tg), key=lambda m: first["txn_no"] not in m["text"])
    assert linked.tap(T.BTN_FOLLOWUP_DONE, card=one) == "followup_answered"
    skip_one = linked.card                                                   # "no photo" for the first answer
    assert linked.tap(T.BTN_FOLLOWUP_PARTIAL, card=two) == "followup_answered"
    assert linked.tap(T.BTN_SKIP, card=skip_one) == "followup_no_photo"      # the old skip: first answer only
    fake_tg.files["fu-2"] = b"\xff\xd8demo"
    assert linked.photo("fu-2") == "followup_photo"                          # still goes to the second answer
    assert _f(conn, second)["photo_asset_id"] is not None and _f(conn, first)["photo_asset_id"] is None
    assert conn.execute("SELECT count(*) AS n FROM field.report").fetchone()["n"] == 0


def test_photo_after_the_window_starts_a_report_and_is_not_lost(conn, core, limits, team, worker, drain, linked, fake_tg):
    txn = _expense(conn, core, limits, team["worker-a1"])
    _run(worker, drain, ASK_DAY)
    [card] = _asks(fake_tg)
    linked.tap(T.BTN_FOLLOWUP_DONE, card=card, now=ASK_DAY)
    assert linked.photo("late", now=ASK_DAY + timedelta(minutes=11)) == "report_item"
    assert _f(conn, txn)["photo_asset_id"] is None
    assert conn.execute("SELECT count(*) AS n FROM field.report").fetchone()["n"] == 1
