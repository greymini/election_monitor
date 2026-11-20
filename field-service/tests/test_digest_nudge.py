"""Daily digest at 20:00 IST (finance part only to finance scope all) and the idle-balance nudge."""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from decimal import Decimal

from pulse.bot import texts_hi as T
from pulse.common.clock import IST, today_ist
from pulse.ledger import service as ledger
from pulse.ledger.service import ExpenseInput
from pulse.worker.jobs import balance_nudge, digest_daily
from tests.helpers import bucket_id
from tests.triage_world import file_report


def ist(h: int, m: int = 0, days: int = 0) -> datetime:
    return datetime.combine(today_ist() + timedelta(days=days), time(h, m), tzinfo=IST).astimezone(timezone.utc)


def _digests(conn, core=None, now=None):
    """Each queued digest drawn as the sender would draw it (the outbox stores no text)."""
    from pulse.core_adapter.stub import StubCoreAdapter
    from pulse.notify import render

    core = core or StubCoreAdapter()
    rows = conn.execute("""SELECT a.*, m.display_name FROM field.alert_message a
                           JOIN field.member m ON m.member_id = a.recipient_member_id WHERE a.kind = 'digest'""").fetchall()
    for r in rows:
        assert set(r["body"]) == {"render"}                       # nothing but the template name
    return {r["display_name"]: render.render(conn, core, r, now or ist(20, 1))[0] for r in rows}


def test_digest_contents_and_finance_part_only_for_finance_all(conn, core, limits, world, worker):
    conn.execute("UPDATE field.member SET finance_scope = 'all' WHERE member_id = %s", (world.team["worker-b1"],))
    noon = ist(12)
    file_report(conn, core, world.team["worker-a1"], urgency="urgent", now=noon)
    file_report(conn, core, world.team["worker-a2"], category="EDU", now=noon, area=("blk-a", "area-a2"))
    file_report(conn, core, world.team["worker-a1"], category="LAW", now=noon)
    ledger.create_expense(conn, core, world.team["worker-a1"], ExpenseInput(amount=Decimal("1500"),
                          bucket_id=bucket_id(conn, "FOOD")), limits=limits, now=noon)
    ledger.create_advance(conn, core, world.team["principal"], to_member_id=world.team["incharge-a"],
                          amount=Decimal("5000"), limits=limits, now=noon)

    assert digest_daily(worker, ist(19, 59)) == {"queued": 0}            # not yet
    assert digest_daily(worker, ist(20, 1)) == {"queued": 3}
    assert digest_daily(worker, ist(20, 6)) == {"queued": 0}             # once a day
    d = _digests(conn)
    assert set(d) == {"डेमो प्रत्याशी", "डेमो प्रभारी ख", "डेमो कार्यकर्ता ख1"}
    principal, coord, treasurer = d["डेमो प्रत्याशी"], d["डेमो प्रभारी ख"], d["डेमो कार्यकर्ता ख1"]
    assert T.DIGEST_REPORTS_TODAY.format(n=3) in principal and T.DIGEST_OPEN_URGENT.format(n=1) in principal
    assert T.DIGEST_LINE.format(name="कानून-व्यवस्था", n=1) in principal
    assert T.DIGEST_SPEND_TODAY.format(amount="₹1,500") in principal
    assert T.DIGEST_UNCONFIRMED.format(n=1, amount="₹5,000") in principal
    assert T.DIGEST_REPORTS_TODAY.format(n=3) in coord
    assert "₹1,500" not in coord and T.DIGEST_SPEND_TODAY.split(":")[0] not in coord   # no finance part
    assert T.DIGEST_SPEND_TODAY.format(amount="₹1,500") in treasurer
    assert T.DIGEST_REPORTS_TODAY.split(":")[0] not in treasurer         # finance only
    world.drain()
    assert any(T.DIGEST_TITLE.split("(")[0] in (m["text"] or "") for m in world.principal.mine)


def test_digest_counts_escalations(conn, core, world, worker):
    r = file_report(conn, core, world.team["worker-a1"], urgency="urgent", now=ist(12))
    conn.execute("UPDATE field.report SET escalation_level = 2, escalated_at = %s WHERE report_id = %s",
                 (ist(13), r["report_id"]))
    digest_daily(worker, ist(20, 2))
    assert T.DIGEST_ESCALATED.format(n=1) in _digests(conn)["डेमो प्रत्याशी"]


def _funded(conn, core, limits, team, days_ago: int):
    at = ist(9, days=-days_ago)
    row = ledger.create_advance(conn, core, team["incharge-a"], to_member_id=team["worker-a1"], amount=Decimal("8000"),
                                limits=limits, now=at)
    ledger.acknowledge(conn, row["txn_id"], team["worker-a1"], now=at)


def _nudges(conn):
    return conn.execute("SELECT * FROM field.alert_message WHERE kind = 'nudge'").fetchall()


def test_nudge_once_per_five_days(conn, core, limits, world, worker):
    _funded(conn, core, limits, world.team, days_ago=6)
    assert balance_nudge(worker, ist(9, 30)) == {"queued": 0}            # before 10:00 IST
    assert balance_nudge(worker, ist(10, 5)) == {"queued": 1}
    assert balance_nudge(worker, ist(15)) == {"queued": 0}
    assert balance_nudge(worker, ist(10, 5, days=3)) == {"queued": 0}
    [n] = _nudges(conn)
    assert n["recipient_member_id"] == world.team["worker-a1"] and n["body"] == {"render": "nudge"}
    world.drain()
    assert world.rep.mine[-1]["text"] == T.NUDGE.format(amount="₹8,000")
    assert balance_nudge(worker, ist(10, 10, days=5)) == {"queued": 1}   # five days later, again


def test_no_nudge_after_a_recent_expense_or_for_the_principal(conn, core, limits, world, worker):
    _funded(conn, core, limits, world.team, days_ago=6)
    ledger.create_expense(conn, core, world.team["worker-a1"], ExpenseInput(amount=Decimal("100"),
                          bucket_id=bucket_id(conn, "FOOD")), limits=limits, now=ist(9, days=-2))
    assert balance_nudge(worker, ist(10, 5)) == {"queued": 0}
    assert all(n["recipient_member_id"] != world.team["principal"] for n in _nudges(conn))
