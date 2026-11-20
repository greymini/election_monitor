"""Impact follow-up (slice 3, LLD §7): ASSIST and WORKS expenses are asked about once after
followup_days, reminded once three days later, then left: unanswered stays NULL, never "no"."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from pulse import settings_ac
from pulse.bot import texts_hi as T
from pulse.ledger import service as ledger
from pulse.ledger.service import ExpenseInput
from pulse.worker.jobs import TIMED, followup_ask
from tests import updates as U
from tests.helpers import bucket_id, link
from tests.test_flow_report import Reporter

D = Decimal
# 11:00 Asia/Kolkata on a fixed day; entries are made 14 days earlier
ASK_DAY = datetime(2026, 11, 2, 5, 30, tzinfo=timezone.utc)
ENTERED = ASK_DAY - timedelta(days=14)


@pytest.fixture
def linked(pool, bot, team):
    link(pool, bot, team["worker-a1"])
    return Reporter(pool, bot)


def _expense(conn, core, limits, member, code="ASSIST", now=ENTERED, amount="2500"):
    return ledger.create_expense(conn, core, member, ExpenseInput(amount=D(amount), bucket_id=bucket_id(conn, code)),
                                 limits=limits, now=now)


def _f(conn, txn):
    return conn.execute("SELECT * FROM field.txn_followup WHERE txn_id = %s", (txn["txn_id"],)).fetchone()


def _run(worker, drain, now):
    counts = followup_ask(worker, now)
    drain(now)
    return counts


def test_scheduled():
    assert ("followup_ask", 900) in {(n, s) for n, s, _ in TIMED}


def test_only_assist_and_works(conn, core, limits, team):
    for code in ("ASSIST", "WORKS", "FOOD", "EVENT"):
        _expense(conn, core, limits, team["worker-a1"], code)
    rows = conn.execute("SELECT t.txn_no FROM field.txn_followup f JOIN field.money_txn t ON t.txn_id = f.txn_id").fetchall()
    assert len(rows) == 2
    f = conn.execute("SELECT * FROM field.txn_followup LIMIT 1").fetchone()
    assert f["member_id"] == team["worker-a1"] and f["due_at"] == ENTERED + timedelta(days=14)


def test_asked_once_reminded_once_then_null(conn, core, limits, team, worker, drain, linked, fake_tg):
    txn = _expense(conn, core, limits, team["worker-a1"])
    assert _run(worker, drain, ASK_DAY - timedelta(days=1)) == {"asked": 0, "reminded": 0}     # not due yet
    assert _run(worker, drain, ASK_DAY) == {"asked": 1, "reminded": 0}
    ask = [m for m in fake_tg.to(U.USER_A["id"]) if m["reply_markup"]][-1]
    assert ask["text"].startswith("काम पूरा हुआ?") and txn["txn_no"] in ask["text"] and "₹2,500" in ask["text"]
    assert [b["text"] for b in ask["reply_markup"]["inline_keyboard"][0]] == [
        T.BTN_FOLLOWUP_DONE, T.BTN_FOLLOWUP_PARTIAL, T.BTN_FOLLOWUP_NOT_DONE]
    assert _run(worker, drain, ASK_DAY + timedelta(minutes=15)) == {"asked": 0, "reminded": 0}  # once
    assert _run(worker, drain, ASK_DAY + timedelta(days=2)) == {"asked": 0, "reminded": 0}
    assert _run(worker, drain, ASK_DAY + timedelta(days=3)) == {"asked": 0, "reminded": 1}
    assert fake_tg.to(U.USER_A["id"])[-1]["text"].startswith(T.FOLLOWUP_REMINDER_PREFIX)
    for days in (4, 7, 30):
        assert _run(worker, drain, ASK_DAY + timedelta(days=days)) == {"asked": 0, "reminded": 0}
    f = _f(conn, txn)
    assert f["answer"] is None and f["answered_at"] is None                 # unanswered: NULL, not "no"
    assert f["asked_at"] == ASK_DAY and f["reminded_at"] == ASK_DAY + timedelta(days=3)


def test_answer_with_photo_and_no_reminder(conn, core, limits, team, worker, drain, linked, fake_tg):
    txn = _expense(conn, core, limits, team["worker-a1"], "WORKS")
    _run(worker, drain, ASK_DAY)
    assert linked.tap(T.BTN_FOLLOWUP_PARTIAL) == "followup_answered"
    fake_tg.files["fu-photo"] = b"\xff\xd8demo"
    assert linked.photo("fu-photo") == "followup_photo"
    f = _f(conn, txn)
    assert f["answer"] == "partial" and f["photo_asset_id"] is not None
    assert conn.execute("SELECT count(*) AS n FROM field.report").fetchone()["n"] == 0        # not a new report
    assert _run(worker, drain, ASK_DAY + timedelta(days=3)) == {"asked": 0, "reminded": 0}


def test_first_answer_stands(conn, core, limits, team, worker, drain, linked, fake_tg):
    txn = _expense(conn, core, limits, team["worker-a1"])
    _run(worker, drain, ASK_DAY)
    card = [m for m in fake_tg.to(U.USER_A["id"]) if m["reply_markup"]][-2:][0]
    assert linked.tap(T.BTN_FOLLOWUP_DONE, card=card) == "followup_answered"
    assert linked.tap(T.BTN_FOLLOWUP_NOT_DONE, card=card) == "followup_already"
    assert _f(conn, txn)["answer"] == "done"


def test_no_questions_at_night_and_void_cancels(conn, core, limits, team, worker, drain, linked):
    keep = _expense(conn, core, limits, team["worker-a1"])
    gone = _expense(conn, core, limits, team["worker-a1"], now=ENTERED + timedelta(minutes=1))
    ledger.void(conn, gone["txn_id"], team["worker-a1"], "undo", limits=limits, now=ENTERED + timedelta(minutes=2))
    night = ASK_DAY + timedelta(hours=12)                                   # 23:00 IST
    assert _run(worker, drain, night) == {"asked": 0, "reminded": 0}
    assert _run(worker, drain, ASK_DAY + timedelta(days=1)) == {"asked": 1, "reminded": 0}
    assert _f(conn, gone)["cancelled_at"] is not None and _f(conn, gone)["asked_at"] is None
    assert _f(conn, keep)["asked_at"] is not None


def test_reclassify_moves_in_and_out(conn, core, limits, team):
    txn = _expense(conn, core, limits, team["principal"], "FOOD", now=None)
    assert _f(conn, txn) is None
    ledger.reclassify(conn, txn["txn_id"], team["principal"], bucket_id(conn, "WORKS"), limits=limits)
    assert _f(conn, txn)["cancelled_at"] is None
    ledger.reclassify(conn, txn["txn_id"], team["principal"], bucket_id(conn, "FOOD"), limits=limits)
    assert _f(conn, txn)["cancelled_at"] is not None


def test_followup_days_per_ac(conn, core, limits, team):
    settings_ac.change(conn, "ac-32", {"followup_days": 7}, actor_user_id="usr-principal")
    eff = settings_ac.effective(conn, "ac-32", settings_ac.Defaults(ledger=limits))
    txn = _expense(conn, core, eff.ledger, team["worker-a1"])
    assert _f(conn, txn)["due_at"] == ENTERED + timedelta(days=7)
