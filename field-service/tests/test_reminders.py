"""Acknowledgement reminders at 24 h and flags at 48 h, run once by scripts/run_jobs.py."""
from datetime import timedelta
from decimal import Decimal

from pulse.bot import texts_hi as T
from pulse.common.clock import utcnow
from pulse.ledger import reminders
from pulse.ledger import service as ledger
from scripts.run_jobs import run
from tests import updates as U
from tests.helpers import link


def _pending(conn, core, limits, team, hours_ago, to="incharge-a"):
    return ledger.create_advance(conn, core, team["principal"], to_member_id=team[to], amount=Decimal("5000"),
                                 limits=limits, now=utcnow() - timedelta(hours=hours_ago))


def test_due_and_overdue_queries(conn, core, limits, team):
    young = _pending(conn, core, limits, team, 23)
    day = _pending(conn, core, limits, team, 25)
    two_days = _pending(conn, core, limits, team, 49)
    now = utcnow()
    assert {r["txn_id"] for r in reminders.advances_needing_reminder(conn, now, limits)} == {day["txn_id"], two_days["txn_id"]}
    assert {r["txn_id"] for r in reminders.advances_to_flag(conn, now, limits)} == {two_days["txn_id"]}
    assert young["txn_id"] not in {r["txn_id"] for r in reminders.advances_needing_reminder(conn, now, limits)}


def test_run_jobs_reminds_once_and_flags_once(pool, conn, core, limits, bot, fake_tg, team, drain):
    link(pool, bot, team["incharge-a"], U.USER_B)
    day = _pending(conn, core, limits, team, 30)
    two_days = _pending(conn, core, limits, team, 50, to="worker-b1")     # worker-b1 is not linked
    acked = _pending(conn, core, limits, team, 60)
    ledger.acknowledge(conn, acked["txn_id"], team["incharge-a"])

    counts = run(pool, fake_tg, core, limits, dry_run=False)
    drain()                                                            # reminders go out through the queue
    assert counts == {"reminded": 1, "not_delivered": 1, "flagged": 1}
    [card] = fake_tg.to(U.USER_B["id"])
    assert card["text"].startswith(T.ACK_REMINDER) and day["txn_no"] in card["text"]
    assert [b["text"] for b in card["reply_markup"]["inline_keyboard"][0]] == [T.BTN_ACK_YES]

    flagged = conn.execute("SELECT flagged FROM field.money_txn WHERE txn_id = %s", (two_days["txn_id"],)).fetchone()
    assert flagged["flagged"] == ["ack_overdue"]
    assert run(pool, fake_tg, core, limits, dry_run=False) == {"reminded": 0, "not_delivered": 0, "flagged": 0}
    actions = sorted(r["action"] for r in conn.execute("SELECT action FROM field.txn_audit WHERE actor_member_id IS NULL"))
    assert actions == ["flag", "remind", "remind"]


def test_dry_run_writes_nothing(pool, conn, core, limits, fake_tg, team):
    _pending(conn, core, limits, team, 50)
    run(pool, fake_tg, core, limits, dry_run=True)
    assert fake_tg.sent == []
    assert conn.execute("SELECT count(*) AS n FROM field.txn_audit").fetchone()["n"] == 0
