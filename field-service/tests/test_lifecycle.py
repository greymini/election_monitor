"""Every allowed and refused status change, and who may make them."""
from __future__ import annotations

import itertools

import pytest

from pulse.common.clock import utcnow
from pulse.reports import access, lifecycle
from tests.triage_world import file_report

STATUSES = ("new", "acknowledged", "assigned", "in_progress", "resolved", "closed_no_action", "duplicate")
ALLOWED = {
    ("new", "acknowledged"), ("new", "assigned"), ("new", "duplicate"),
    ("acknowledged", "assigned"), ("acknowledged", "resolved"), ("acknowledged", "closed_no_action"),
    ("acknowledged", "duplicate"),
    ("assigned", "in_progress"), ("assigned", "assigned"), ("assigned", "resolved"),
    ("assigned", "closed_no_action"), ("assigned", "duplicate"),
    ("in_progress", "resolved"), ("in_progress", "closed_no_action"), ("in_progress", "assigned"),
    ("resolved", "acknowledged"), ("closed_no_action", "acknowledged"), ("duplicate", "acknowledged"),
}


def test_table_is_exactly_the_documented_one():
    got = {(f, t) for f in STATUSES for t in STATUSES if lifecycle.allowed(f, t)}
    assert got == ALLOWED
    assert not any(lifecycle.allowed("draft", t) for t in STATUSES)


@pytest.mark.parametrize("frm,to", list(itertools.product(STATUSES, STATUSES)))
def test_each_change_is_applied_or_refused(conn, core, team, frm, to):
    r = file_report(conn, core, team["worker-a1"])
    other = file_report(conn, core, team["worker-a2"], note="other")
    conn.execute("UPDATE field.report SET status = %s, assigned_to = %s, duplicate_of = %s WHERE report_id = %s",
                 (frm, team["worker-a2"] if frm in ("assigned", "in_progress") else None,
                  other["report_id"] if frm == "duplicate" else None, r["report_id"]))
    kw = {"assignee": team["worker-a2"]} if to == "assigned" else {}
    if to == "duplicate":
        kw["duplicate_of"] = other["report_id"]
    if (frm, to) in ALLOWED:
        row = lifecycle.change(conn, r["report_id"], to, actor=team["principal"], now=utcnow(), note="done", **kw)
        assert row["status"] == to
        ev = conn.execute("SELECT data FROM field.report_event WHERE report_id = %s AND kind = 'status_changed'",
                          (r["report_id"],)).fetchone()["data"]
        assert (ev["from"], ev["to"]) == (frm, to)
        if frm in lifecycle.CLOSED:
            assert ev["reopen"] and row["resolved_at"] is None and row["duplicate_of"] is None
    else:
        with pytest.raises(lifecycle.TransitionError):
            lifecycle.change(conn, r["report_id"], to, actor=team["principal"], now=utcnow(), **kw)


def test_assign_from_new_records_the_acknowledgement(conn, core, team):
    r = file_report(conn, core, team["worker-a1"])
    row = lifecycle.change(conn, r["report_id"], "assigned", actor=team["incharge-a"], now=utcnow(),
                           assignee=team["worker-a2"])
    assert row["ack_by"] == team["incharge-a"] and row["ack_at"] is not None


def test_who_may_triage_work_and_reopen(conn, core, team):
    conn.execute("UPDATE field.member SET is_coordinator = true WHERE member_id = %s", (team["incharge-b"],))
    r = file_report(conn, core, team["worker-a1"])
    law = file_report(conn, core, team["worker-a1"], category="LAW")

    def v(key):
        return access.viewer_for(conn, team[key])

    assert access.may_triage(conn, v("principal"), r) and access.may_triage(conn, v("incharge-b"), r)
    assert access.may_triage(conn, v("incharge-a"), r)               # above the reporter
    assert not access.may_triage(conn, v("worker-a1"), r)            # never your own report
    assert not access.may_triage(conn, v("worker-a2"), r)            # a peer, no team of their own
    assert not access.may_triage(conn, v("worker-b1"), r)
    assert not access.may_triage(conn, v("incharge-a"), law)         # restricted: principal and coordinators only
    assert access.may_triage(conn, v("incharge-b"), law)
    assert access.may_reopen(v("incharge-b"), r) and not access.may_reopen(v("incharge-a"), r)
