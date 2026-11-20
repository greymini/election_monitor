"""Stale buttons on report alert cards, assignee cards and the close steps (pulse/bot/flow_triage.py):
the report moved on, the issue closed, the tapper lost the right, the close session expired, and the same
callback delivered twice. Every tap gets an answer and nothing changes wrongly."""
from __future__ import annotations

import itertools
from datetime import timedelta

from pulse.bot import texts_hi as T
from pulse.common.clock import utcnow
from pulse.reports import access, issues, triage
from pulse.team import service as team_service
from tests.triage_world import category_id, file_report

W2 = "डेमो कार्यकर्ता क2"
_ids = itertools.count(910_000)


def _report(conn, rid):
    return conn.execute("SELECT * FROM field.report WHERE report_id = %s", (rid,)).fetchone()


def _events(conn, rid, kind):
    """Events of one kind; acknowledged / assigned are status changes to that status."""
    if kind in ("acknowledged", "assigned"):
        return conn.execute("SELECT count(*) AS n FROM field.report_event WHERE report_id = %s "
                            "AND kind = 'status_changed' AND data->>'to' = %s", (rid, kind)).fetchone()["n"]
    return conn.execute("SELECT count(*) AS n FROM field.report_event WHERE report_id = %s AND kind = %s",
                        (rid, kind)).fetchone()["n"]


def _card_with(phone, label):
    return next(m for m in reversed(phone.mine) if m["reply_markup"] and label in phone.buttons(m))


def _viewer(conn, member_id):
    return access.viewer_for(conn, member_id)


def test_second_acknowledgement_is_told_who_saw_it_first(conn, core, world):
    r = file_report(conn, core, world.team["worker-a1"], urgency="urgent")
    world.drain()
    mine, theirs = _card_with(world.incharge, T.BTN_SEEN), _card_with(world.principal, T.BTN_SEEN)
    assert world.incharge.tap(T.BTN_SEEN, card=mine) == "report_acknowledged"
    first = _report(conn, r["report_id"])
    assert world.principal.tap_data(world.principal.data_for(T.BTN_SEEN, theirs), theirs["message_id"]) \
        == "report_already_seen"
    assert world.principal.toast.startswith(T.ALREADY_SEEN.split("{")[0])
    after = _report(conn, r["report_id"])
    assert (after["ack_by"], after["ack_at"], after["status"]) == (first["ack_by"], first["ack_at"], "acknowledged")
    assert _events(conn, r["report_id"], "acknowledged") == 1


def test_same_callback_delivered_twice_changes_nothing(conn, core, world):
    r = file_report(conn, core, world.team["worker-a1"], urgency="urgent")
    world.drain()
    card = _card_with(world.incharge, T.BTN_SEEN)
    data, uid = world.incharge.data_for(T.BTN_SEEN, card), next(_ids)
    assert world.incharge.tap_data(data, card["message_id"], update_id=uid) == "report_acknowledged"
    answered = len(world.fake.answered)
    assert world.incharge.tap_data(data, card["message_id"], update_id=uid) == "duplicate"
    assert len(world.fake.answered) == answered                       # the retry is not answered a second time
    assert _events(conn, r["report_id"], "acknowledged") == 1


def test_assign_from_an_old_picker_after_the_report_was_closed(conn, core, world):
    r = file_report(conn, core, world.team["worker-a1"])
    world.drain()
    world.incharge.tap(T.BTN_ASSIGN, card=_card_with(world.incharge, T.BTN_ASSIGN))
    picker = world.incharge.mine[-1]
    world.incharge.tap(W2, card=picker)                                # assigned to worker A2
    job = world.wk2.mine[-1]
    world.wk2.tap(T.BTN_WORK_CANNOT, card=job)
    world.wk2.tap(T.BTN_SKIP)
    assert world.wk2.tap(T.BTN_SKIP) == "report_closed"
    closed = _report(conn, r["report_id"])
    assert closed["status"] == "closed_no_action"
    # the in-charge taps a name on the picker that is still on their screen
    assert world.incharge.tap(W2, card=picker) == "transition_refused"
    assert world.incharge.toast == T.STATUS_NOT_ALLOWED
    assert _report(conn, r["report_id"])["status"] == "closed_no_action"
    assert _events(conn, r["report_id"], "assigned") == 1


def test_merge_tapped_again_after_merging(conn, core, world):
    t0 = utcnow()
    first = file_report(conn, core, world.team["worker-a2"], note="handpump kharab", now=t0 - timedelta(hours=2),
                        area=("blk-a", "area-a1"))
    second = file_report(conn, core, world.team["worker-a1"], note="chapakal se pani nahi", now=t0)
    world.drain()
    card = [m for m in world.incharge.mine if second["report_no"] in (m["text"] or "") and m["reply_markup"]][-1]
    label = T.BTN_MERGE_INTO.format(report_no=first["report_no"])
    data = world.incharge.data_for(label, card)
    assert world.incharge.tap_data(data, card["message_id"]) == "report_merged"
    outcome = world.incharge.tap_data(data, card["message_id"])
    assert outcome in ("transition_refused", "card_stale")
    assert world.incharge.toast in (T.STATUS_NOT_ALLOWED, T.CARD_STALE)
    row = _report(conn, second["report_id"])
    assert row["status"] == "duplicate" and row["duplicate_of"] == first["report_id"]
    assert _events(conn, first["report_id"], "merged_from") == 1


def test_assignee_card_after_reassignment(conn, core, world):
    r = file_report(conn, core, world.team["worker-a1"])
    world.drain()
    world.incharge.tap(T.BTN_ASSIGN, card=_card_with(world.incharge, T.BTN_ASSIGN))
    world.incharge.tap(W2, card=world.incharge.mine[-1])
    job = world.wk2.mine[-1]
    v = _viewer(conn, world.team["incharge-a"])
    triage.assign(conn, v, r["report_id"], world.team["worker-a1"], now=utcnow())   # reassigned elsewhere
    world.drain()
    assert world.wk2.tap(T.BTN_WORK_START, card=job) == "not_allowed"
    assert world.wk2.toast == T.NOT_ALLOWED
    row = _report(conn, r["report_id"])
    assert row["status"] == "assigned" and row["assigned_to"] == world.team["worker-a1"]


def test_close_skip_after_the_close_session_expired(conn, core, world):
    """The close steps (note, photo) live in a 15-minute session; a skip tapped after it ran out must
    not close the report on its own, and must say so."""
    t0 = utcnow()
    r = file_report(conn, core, world.team["worker-a1"])
    world.drain()
    world.incharge.tap(T.BTN_ASSIGN, card=_card_with(world.incharge, T.BTN_ASSIGN))
    world.incharge.tap(W2, card=world.incharge.mine[-1])
    job = world.wk2.mine[-1]
    assert world.wk2.tap(T.BTN_WORK_DONE, card=job, now=t0) == "close_ask_note"
    skip = world.wk2.mine[-1]
    assert world.wk2.tap(T.BTN_SKIP, card=skip, now=t0 + timedelta(minutes=16)) == "card_expired"
    assert world.wk2.toast == T.CLOSE_EXPIRED
    assert _report(conn, r["report_id"])["status"] == "assigned"
    # the assignee starts again from the job card and it works
    assert world.wk2.tap(T.BTN_WORK_DONE, card=job, now=t0 + timedelta(minutes=17)) == "close_ask_note"
    assert world.wk2.tap(T.BTN_SKIP, now=t0 + timedelta(minutes=17)) == "close_ask_photo"
    assert world.wk2.tap(T.BTN_SKIP, now=t0 + timedelta(minutes=17)) == "report_closed"


def test_close_skip_after_someone_else_closed_it(conn, core, world):
    r = file_report(conn, core, world.team["worker-a1"])
    world.drain()
    world.incharge.tap(T.BTN_ASSIGN, card=_card_with(world.incharge, T.BTN_ASSIGN))
    world.incharge.tap(W2, card=world.incharge.mine[-1])
    world.wk2.tap(T.BTN_WORK_DONE, card=world.wk2.mine[-1])
    world.wk2.tap(T.BTN_SKIP)                                         # now on the photo step
    v = _viewer(conn, world.team["incharge-a"])
    triage.close(conn, v, r["report_id"], "closed_no_action", now=utcnow())
    assert world.wk2.tap(T.BTN_SKIP) == "transition_refused"
    assert T.STATUS_NOT_ALLOWED in [m["text"] for m in world.wk2.mine[-3:]]
    assert _report(conn, r["report_id"])["status"] == "closed_no_action"
    assert conn.execute("SELECT count(*) AS n FROM field.tg_session WHERE flow = 'close'").fetchone()["n"] == 0


def test_issue_button_after_the_issue_closed(conn, core, world):
    r1 = file_report(conn, core, world.team["worker-a1"], urgency="urgent")
    world.drain()
    principal = world.principal
    v = _viewer(conn, world.team["principal"])
    issue = issues.create(conn, core, v, now=utcnow(), area_id="area-a1", title="demo issue")
    r2 = file_report(conn, core, world.team["worker-a1"], urgency="urgent")
    world.drain()
    card = next(m for m in reversed(principal.mine) if m["reply_markup"] and r2["report_no"] in (m["text"] or "")
                and T.BTN_ISSUE_LINK in principal.buttons(m))
    assert principal.tap(T.BTN_ISSUE_LINK, card=card) == "report_pick_issue"
    pick = next(m for m in reversed(world.fake.cards) if m["reply_markup"] and
                any(issue["issue_no"] in b["text"] for row in m["reply_markup"]["inline_keyboard"] for b in row))
    label = next(b["text"] for row in pick["reply_markup"]["inline_keyboard"] for b in row if issue["issue_no"] in b["text"])
    issues.close(conn, v, issue["issue_id"], now=utcnow())
    assert principal.tap(label, card=pick) == "issue_refused"
    assert principal.toast == T.ISSUE_NOT_POSSIBLE
    assert _report(conn, r2["report_id"])["issue_id"] is None
    assert _report(conn, r1["report_id"])["issue_id"] is None


def test_new_issue_tapped_twice_opens_one_issue(conn, core, world):
    r = file_report(conn, core, world.team["worker-a1"], urgency="urgent")
    world.drain()
    principal = world.principal
    card = _card_with(principal, T.BTN_ISSUE_LINK)
    principal.tap(T.BTN_ISSUE_LINK, card=card)
    pick = next(m for m in reversed(world.fake.cards) if m["reply_markup"] and T.BTN_ISSUE_NEW in principal.buttons(m))
    data = principal.data_for(T.BTN_ISSUE_NEW, pick)
    assert principal.tap_data(data, pick["message_id"]) == "report_issue_created"
    assert principal.tap_data(data, pick["message_id"]) == "issue_refused"     # a second tap, not a retry
    assert principal.toast == T.ISSUE_NOT_POSSIBLE
    assert conn.execute("SELECT count(*) AS n FROM field.issue").fetchone()["n"] == 1
    assert _report(conn, r["report_id"])["issue_id"] is not None


def test_coordinator_flag_removed_before_the_tap(conn, core, world):
    r = file_report(conn, core, world.team["worker-a1"], urgency="urgent")
    world.drain()
    card = _card_with(world.coord, T.BTN_ISSUE_LINK)
    conn.execute("UPDATE field.member SET is_coordinator = false WHERE member_id = %s", (world.team["incharge-b"],))
    outcome = world.coord.tap(T.BTN_ISSUE_LINK, card=card)
    assert outcome in ("not_allowed", "card_stale")                    # incharge-b no longer sees area-a1 at all
    assert world.coord.toast in (T.NOT_ALLOWED, T.CARD_STALE)
    assert conn.execute("SELECT count(*) AS n FROM field.issue").fetchone()["n"] == 0
    assert _report(conn, r["report_id"])["issue_id"] is None


def test_report_that_became_restricted(conn, core, world):
    """The in-charge's card was sent for a normal report; it was then moved to a restricted category,
    which only the principal and coordinators may see. The card no longer acts."""
    r = file_report(conn, core, world.team["worker-a1"])
    world.drain()
    card = _card_with(world.incharge, T.BTN_SEEN)
    restricted = conn.execute("SELECT category_id FROM field.report_category WHERE restricted LIMIT 1").fetchone()
    v = _viewer(conn, world.team["principal"])
    triage.set_category(conn, v, r["report_id"], restricted["category_id"], now=utcnow())
    assert world.incharge.tap(T.BTN_SEEN, card=card) == "card_stale"
    assert world.incharge.toast == T.CARD_STALE
    row = _report(conn, r["report_id"])
    assert row["restricted"] is True and row["ack_at"] is None


def test_deactivated_member_taps_an_old_card(conn, core, world):
    r = file_report(conn, core, world.team["worker-a1"])
    world.drain()
    card = _card_with(world.incharge, T.BTN_SEEN)
    team_service.update_member(conn, core, "ac-32", world.team["incharge-a"], {"active": False})
    assert world.incharge.tap(T.BTN_SEEN, card=card) == "unlinked"
    assert world.incharge.text == T.UNREGISTERED
    assert _report(conn, r["report_id"])["ack_at"] is None


def test_category_from_an_unsorted_card_after_someone_set_it(conn, core, world):
    r = file_report(conn, core, world.team["worker-a1"], category=None, how="auto")   # unsorted: sent to sort
    world.drain()
    card = next(m for m in reversed(world.fake.cards) if m["reply_markup"] and r["report_no"] in (m["text"] or "")
                and "बिजली-पानी-सड़क" in [b["text"] for row in m["reply_markup"]["inline_keyboard"] for b in row])
    owner = next(p for p in (world.principal, world.coord, world.incharge) if card in p.mine)
    v = _viewer(conn, world.team["principal"])
    triage.set_category(conn, v, r["report_id"], category_id(conn, "HEALTH"), now=utcnow())
    assert owner.tap("बिजली-पानी-सड़क", card=card) == "report_category_set_already"   # no silent overwrite
    assert owner.toast == T.CATEGORY_ALREADY_SET
    assert _report(conn, r["report_id"])["category_id"] == category_id(conn, "HEALTH")
    assert _events(conn, r["report_id"], "category_set") == 1
