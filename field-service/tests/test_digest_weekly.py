"""Weekly digest (slice 3): Monday 09:00 Asia/Kolkata to the principal, once a week; reports and issues
opened and closed, median acknowledgement time, quiet members, and the need-versus-spend top five
only for finance scope all."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from pulse.bot import texts_hi as T
from pulse.ledger import service as ledger
from pulse.ledger.service import ExpenseInput
from pulse.notify import alerts, digest, render
from pulse.notify import digest_weekly as weekly
from pulse.reports import access, issues, lifecycle
from pulse.worker.jobs import TIMED
from tests.helpers import bucket_id
from tests.triage_world import file_report

MONDAY_9 = datetime(2026, 11, 16, 3, 30, tzinfo=timezone.utc)          # Monday 16-11-2026 09:00 IST
IN_WEEK = datetime(2026, 11, 10, 6, 0, tzinfo=timezone.utc)            # Tuesday of the week before
BEFORE = datetime(2026, 11, 5, 6, 0, tzinfo=timezone.utc)


def _weekly_rows(conn):
    return conn.execute("SELECT * FROM field.alert_message WHERE kind = 'digest_weekly' ORDER BY alert_id").fetchall()


def _text(conn, core, alert):
    text, markup, photo = render.render(conn, core, alert, MONDAY_9)
    assert markup is None and photo is None
    return text


def test_seeded_and_scheduled(conn):
    rule = alerts.rule(conn, "digest_weekly")
    assert rule["trigger"] == "weekly_digest" and rule["recipients"] == ["principal"]
    assert str(rule["at_local_time"]) == "09:00:00"
    assert ("digest_weekly", 300) in {(n, s) for n, s, _ in TIMED}


def test_once_a_week_from_monday_nine(conn, core, team):
    assert weekly.send_weekly(conn, core, MONDAY_9 - timedelta(minutes=1)) == 0     # Monday 08:59
    assert weekly.send_weekly(conn, core, MONDAY_9) == 1
    assert weekly.send_weekly(conn, core, MONDAY_9 + timedelta(hours=3)) == 0
    assert weekly.send_weekly(conn, core, MONDAY_9 + timedelta(days=2)) == 0       # Wednesday: already sent
    [row] = _weekly_rows(conn)
    assert row["recipient_member_id"] == team["principal"] and str(row["local_date"]) == "2026-11-16"
    assert row["body"] == {"render": "digest_weekly"}                              # no text in the outbox
    assert weekly.send_weekly(conn, core, MONDAY_9 + timedelta(days=7)) == 1
    # the daily digest on a Monday is not blocked by the weekly one
    assert digest.send_daily(conn, core, MONDAY_9 + timedelta(hours=11)) >= 1


def test_catch_up_later_in_the_week(conn, core, team):
    assert weekly.send_weekly(conn, core, MONDAY_9 + timedelta(days=1)) == 1       # worker was down Monday
    assert str(_weekly_rows(conn)[0]["local_date"]) == "2026-11-16"


def test_contents_and_finance_gating(conn, core, limits, team):
    w = team["worker-a1"]
    r1 = file_report(conn, core, w, now=IN_WEEK)
    r2 = file_report(conn, core, w, now=IN_WEEK + timedelta(hours=1))
    file_report(conn, core, w, now=BEFORE)                                         # previous week: not counted
    lifecycle.change(conn, r1["report_id"], "acknowledged", actor=team["principal"], now=IN_WEEK + timedelta(minutes=30))
    lifecycle.change(conn, r1["report_id"], "resolved", actor=team["principal"], now=IN_WEEK + timedelta(days=1))
    lifecycle.change(conn, r2["report_id"], "acknowledged", actor=team["principal"], now=IN_WEEK + timedelta(hours=1, minutes=50))
    v = access.viewer_for(conn, team["principal"])
    issues.create(conn, core, v, now=IN_WEEK, title="डेमो", area_id="area-a1")
    ledger.create_expense(conn, core, team["incharge-b"], ExpenseInput(
        amount=Decimal("900"), bucket_id=bucket_id(conn, "FOOD"), block_id="blk-b", area_id="area-b1"),
        limits=limits, now=IN_WEEK)

    weekly.send_weekly(conn, core, MONDAY_9)
    text = _text(conn, core, _weekly_rows(conn)[0])
    assert text.splitlines()[0] == T.WEEKLY_TITLE.format(start="09-11", end="15-11-2026")
    assert T.WEEKLY_REPORTS.format(filed=2, closed=1) in text
    assert T.WEEKLY_ISSUES.format(opened=1, closed=0) in text
    assert T.WEEKLY_ACK.format(minutes=Decimal("40.0")) in text                    # median of 30 and 50
    assert T.WEEKLY_QUIET.format(n=3) in text                                      # in-charge A, workers A2 and B1
    lines = text.splitlines()
    quiet = lines[lines.index(T.WEEKLY_QUIET.format(n=3)) + 1]
    assert set(quiet.split(", ")) == {"डेमो प्रभारी क", "डेमो कार्यकर्ता क2", "डेमो कार्यकर्ता ख1"}
    assert T.WEEKLY_GAP_TITLE in text and T.WEEKLY_GAP_NOTE in text
    gap_lines = [ln for ln in text.splitlines() if "अंतर" in ln and ":" in ln and "शीर्ष" not in ln]
    assert len(gap_lines) == 5 and gap_lines[0].startswith("डेमो पंचायत क1")

    # without finance scope all, no money and no ranking in the weekly digest
    conn.execute("UPDATE field.member SET finance_scope = 'team' WHERE member_id = %s", (team["principal"],))
    text = _text(conn, core, _weekly_rows(conn)[0])
    assert T.WEEKLY_GAP_TITLE not in text and "₹" not in text and T.WEEKLY_REPORTS.format(filed=2, closed=1) in text


def test_quiet_week(conn, core, team):
    weekly.send_weekly(conn, core, MONDAY_9)
    text = _text(conn, core, _weekly_rows(conn)[0])
    assert T.WEEKLY_REPORTS.format(filed=0, closed=0) in text and T.WEEKLY_ACK_NONE in text
    assert T.WEEKLY_QUIET.format(n=5) in text
