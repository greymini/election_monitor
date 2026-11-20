"""Assign, start, close with a note and photo, merge, and what the reporter hears back."""
from __future__ import annotations

from datetime import timedelta

from pulse.bot import texts_hi as T
from pulse.common.clock import utcnow
from tests.test_alerts import _buttons
from tests.triage_world import alerts_for, file_report

W2 = "डेमो कार्यकर्ता क2"


def _report(conn, rid):
    return conn.execute("SELECT * FROM field.report WHERE report_id = %s", (rid,)).fetchone()


def test_assign_start_and_close_with_note_and_photo(conn, core, world):
    r = file_report(conn, core, world.team["worker-a1"], urgency="urgent")
    world.drain()
    card = world.incharge.mine[-1]
    assert world.incharge.tap(T.BTN_ASSIGN, card=card) == "report_pick_assignee"
    picker = world.incharge.mine[-1]
    names = _buttons(picker)
    assert names[0] == "डेमो कार्यकर्ता क1"                          # scope covers area-a1: listed first
    assert world.incharge.tap(W2, card=picker) == "report_assigned"
    row = _report(conn, r["report_id"])
    assert row["status"] == "assigned" and row["assigned_to"] == world.team["worker-a2"]
    assert row["ack_by"] == world.team["incharge-a"]                    # assigning from new also acknowledges
    assert T.FEEDBACK_SEEN.format(report_no=r["report_no"]) in [m["text"] for m in world.rep.mine]

    job_card = world.wk2.mine[-1]
    assert job_card["text"].startswith(T.ALERT_ASSIGNEE.format(category="बिजली-पानी-सड़क"))
    assert _buttons(job_card) == [T.BTN_WORK_START, T.BTN_WORK_DONE, T.BTN_WORK_CANNOT]
    assert world.wk2.tap(T.BTN_WORK_START, card=job_card) == "report_started"
    assert _report(conn, r["report_id"])["status"] == "in_progress"
    assert world.wk2.tap(T.BTN_WORK_DONE, card=job_card) == "close_ask_note"
    assert world.wk2.mine[-1]["text"] == T.ASK_CLOSE_NOTE
    assert world.wk2.say("chapakal theek kara diya, call 9876543210") == "close_ask_photo"
    assert world.wk2.photo("closing-ph") == "report_closed"
    row = _report(conn, r["report_id"])
    assert row["status"] == "resolved" and row["resolved_at"] is not None
    assert "9876543210" not in row["resolution_note"] and row["resolution_note"].startswith("chapakal theek")
    role = conn.execute("""SELECT m.role FROM field.report_media m JOIN field.media_asset a USING (asset_id)
                           WHERE m.report_id = %s AND a.tg_file_id = 'closing-ph'""", (r["report_id"],)).fetchone()
    assert role["role"] == "closing"
    feedback = [m["text"] for m in world.rep.mine]
    assert any(t.startswith(T.FEEDBACK_RESOLVED.format(report_no=r["report_no"])) and "chapakal theek" in t
               for t in feedback)


def test_cannot_be_done_with_skipped_note_and_photo(conn, core, world):
    r = file_report(conn, core, world.team["worker-a1"])
    world.drain()
    world.incharge.tap(T.BTN_ASSIGN, card=world.incharge.mine[-1])
    world.incharge.tap(W2)
    job_card = world.wk2.mine[-1]
    world.wk2.tap(T.BTN_WORK_CANNOT, card=job_card)
    assert world.wk2.tap(T.BTN_SKIP) == "close_ask_photo"
    assert world.wk2.tap(T.BTN_SKIP) == "report_closed"
    row = _report(conn, r["report_id"])
    assert row["status"] == "closed_no_action" and row["resolution_note"] is None
    assert T.FEEDBACK_CLOSED.format(report_no=r["report_no"]) in [m["text"] for m in world.rep.mine]


def test_only_assignee_works_and_only_triage_assigns(conn, core, world):
    file_report(conn, core, world.team["worker-a1"])
    world.drain()
    world.incharge.tap(T.BTN_ASSIGN, card=world.incharge.mine[-1])
    world.incharge.tap(W2)
    job_card = world.wk2.mine[-1]
    data = world.wk2.data_for(T.BTN_WORK_START, job_card)
    assert world.rep.tap_data(data, job_card["message_id"]) == "not_allowed"   # the reporter is not the assignee
    assert world.rep.toast == T.NOT_ALLOWED
    assign_data = world.incharge.data_for(T.BTN_ASSIGN, world.incharge.card)   # the in-charge's alert card
    assert world.wk2.tap_data(assign_data, 1) == "not_allowed"                 # the assignee cannot triage


def test_merge_into_suggested_original(conn, core, world):
    t0 = utcnow()
    first = file_report(conn, core, world.team["worker-a2"], note="handpump kharab", now=t0 - timedelta(hours=2),
                        area=("blk-a", "area-a1"))
    second = file_report(conn, core, world.team["worker-a1"], note="chapakal se pani nahi", photo="dup-ph", now=t0)
    sug = conn.execute("SELECT suggested_id FROM field.report_duplicate WHERE report_id = %s", (second["report_id"],)).fetchall()
    assert [s["suggested_id"] for s in sug] == [first["report_id"]]
    world.drain()
    card = [m for m in world.incharge.mine if second["report_no"] in (m["text"] or "")][-1]
    assert T.ALERT_DUPLICATE_HINT.format(numbers=first["report_no"]) in card["text"]
    merge_label = T.BTN_MERGE_INTO.format(report_no=first["report_no"])
    assert world.incharge.tap(merge_label, card=card) == "report_merged"
    row = _report(conn, second["report_id"])
    assert row["status"] == "duplicate" and row["duplicate_of"] == first["report_id"]
    assert conn.execute("SELECT count(*) AS n FROM field.report_media WHERE report_id = %s",
                        (second["report_id"],)).fetchone()["n"] == 1                 # media kept
    kinds = {e["kind"] for e in conn.execute("SELECT kind FROM field.report_event WHERE report_id = %s",
                                             (first["report_id"],))}
    assert "merged_from" in kinds
    assert T.FEEDBACK_DUPLICATE.format(report_no=second["report_no"], into=first["report_no"]) in \
        [m["text"] for m in world.rep.mine]


def test_no_duplicate_suggestion_outside_24_hours_or_other_area(conn, core, world):
    t0 = utcnow()
    file_report(conn, core, world.team["worker-a2"], now=t0 - timedelta(hours=25), area=("blk-a", "area-a1"))
    file_report(conn, core, world.team["worker-a2"], now=t0 - timedelta(hours=1), area=("blk-a", "area-a2"))
    later = file_report(conn, core, world.team["worker-a1"], now=t0)
    assert conn.execute("SELECT count(*) AS n FROM field.report_duplicate WHERE report_id = %s",
                        (later["report_id"],)).fetchone()["n"] == 0


def test_alerts_for_reassignment_redraw_cards(conn, core, world):
    r = file_report(conn, core, world.team["worker-a1"], urgency="urgent")
    world.drain()
    principal_card = world.principal.mine[-1]
    world.incharge.tap(T.BTN_ASSIGN, card=world.incharge.mine[-1])
    world.incharge.tap(W2)
    redrawn = [m for m in world.fake.edits if m["message_id"] == principal_card["message_id"]][-1]
    assert T.ALERT_ASSIGNED_TO.format(name=W2) in redrawn["text"]
    assert len(alerts_for(conn, r["report_id"], "assignee")) == 1
