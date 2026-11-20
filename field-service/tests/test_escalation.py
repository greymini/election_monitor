"""Escalation of unacknowledged urgent reports at 30 and 60 minutes, with a fake clock."""
from __future__ import annotations

from datetime import timedelta

from pulse.bot import texts_hi as T
from pulse.common.clock import utcnow
from tests.triage_world import alerts_for, file_report


def _escalate(worker, now):
    """The urgent_escalate job body at a given moment (its 5-minute schedule is tested in test_media_worker)."""
    from pulse.worker.jobs import urgent_escalate
    return urgent_escalate(worker, now)


def test_escalation_is_scheduled_every_five_minutes():
    from pulse.worker.jobs import TIMED
    assert ("urgent_escalate", 300) in {(name, every) for name, every, _ in TIMED}


def test_escalates_at_30_and_60_then_marks(conn, core, world, worker):
    t0 = utcnow()
    r = file_report(conn, core, world.team["worker-a1"], urgency="urgent", now=t0)
    world.drain()
    first = len(alerts_for(conn, r["report_id"]))
    assert _escalate(worker, t0 + timedelta(minutes=29)) == {"escalated": 0}
    assert _escalate(worker, t0 + timedelta(minutes=31)) == {"escalated": 1}
    world.drain()
    level1 = alerts_for(conn, r["report_id"], "escalation")
    assert len(level1) == first and all(a["status"] == "sent" for a in level1)
    assert T.ALERT_ESCALATION.format(minutes=31, category="बिजली-पानी-सड़क") in world.principal.mine[-1]["text"]
    assert _escalate(worker, t0 + timedelta(minutes=37)) == {"escalated": 0}       # level 1 not repeated
    assert _escalate(worker, t0 + timedelta(minutes=61)) == {"escalated": 1}
    world.drain()
    assert T.ALERT_ESCALATION.format(minutes=61, category="बिजली-पानी-सड़क") in world.principal.mine[-1]["text"]
    row = conn.execute("SELECT * FROM field.report WHERE report_id = %s", (r["report_id"],)).fetchone()
    assert row["escalation_level"] == 2 and row["escalated_at"] is not None
    assert _escalate(worker, t0 + timedelta(minutes=120)) == {"escalated": 0}      # nothing after the last round
    levels = [e["data"]["level"] for e in conn.execute(
        "SELECT data FROM field.report_event WHERE kind = 'escalated' ORDER BY event_id")]
    assert levels == [1, 2]


def test_acknowledged_reports_do_not_escalate(conn, core, world, worker):
    t0 = utcnow()
    file_report(conn, core, world.team["worker-a1"], urgency="urgent", now=t0)
    world.drain()
    world.principal.tap(T.BTN_SEEN)
    assert _escalate(worker, t0 + timedelta(minutes=45)) == {"escalated": 0}


def test_normal_reports_never_escalate(conn, core, world, worker):
    t0 = utcnow()
    file_report(conn, core, world.team["worker-a1"], now=t0)
    assert _escalate(worker, t0 + timedelta(minutes=90)) == {"escalated": 0}


def test_restricted_escalation_stays_with_principal_and_coordinators(conn, core, world, worker):
    t0 = utcnow()
    r = file_report(conn, core, world.team["worker-a1"], category="LAW", urgency="urgent", now=t0)
    _escalate(worker, t0 + timedelta(minutes=31))
    world.drain()
    names = {a["display_name"] for a in alerts_for(conn, r["report_id"], "escalation")}
    assert names == {"डेमो प्रत्याशी", "डेमो प्रभारी ख"}


def test_catch_up_sends_only_the_latest_round(conn, core, world, worker):
    """The worker was down for 75 minutes: both rounds are overdue, only the 60-minute one is sent."""
    t0 = utcnow()
    r = file_report(conn, core, world.team["worker-a1"], urgency="urgent", now=t0)
    world.drain()
    first = len(alerts_for(conn, r["report_id"]))
    assert _escalate(worker, t0 + timedelta(minutes=75)) == {"escalated": 1}
    world.drain()
    sent = alerts_for(conn, r["report_id"], "escalation")
    assert len(sent) == first                                          # one round, not two
    assert T.ALERT_ESCALATION.format(minutes=75, category="बिजली-पानी-सड़क") in world.principal.mine[-1]["text"]
    row = conn.execute("SELECT * FROM field.report WHERE report_id = %s", (r["report_id"],)).fetchone()
    assert row["escalation_level"] == 2 and row["escalated_at"] is not None
    [ev] = conn.execute("SELECT data FROM field.report_event WHERE kind = 'escalated'").fetchall()
    assert ev["data"]["level"] == 2 and ev["data"]["skipped_rounds"] == 1
    assert _escalate(worker, t0 + timedelta(minutes=80)) == {"escalated": 0}


def test_catch_up_between_rounds_sends_the_first(conn, core, world, worker):
    t0 = utcnow()
    r = file_report(conn, core, world.team["worker-a1"], urgency="urgent", now=t0)
    assert _escalate(worker, t0 + timedelta(minutes=50)) == {"escalated": 1}
    row = conn.execute("SELECT escalation_level, escalated_at FROM field.report WHERE report_id = %s",
                       (r["report_id"],)).fetchone()
    assert row == {"escalation_level": 1, "escalated_at": None}
