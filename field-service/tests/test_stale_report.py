"""Stale buttons in the report flow (pulse/bot/flow_report.py): draft cards after the draft was filed,
auto-filed or replaced, an older card version, the same callback delivered twice, filed-report buttons
tapped by someone else, after the report was merged or linked to an issue, and by an unlinked member.
Report drafts never expire: they file themselves, and their old cards stop acting."""
from __future__ import annotations

import itertools
from datetime import timedelta

import pytest

from pulse.bot import texts_hi as T
from pulse.common.clock import utcnow
from pulse.reports import access, issues, triage
from pulse.team import linking
from pulse.worker.runner import run_queue, run_timed
from tests import updates as U
from tests.helpers import link
from tests.test_flow_report import Reporter, _events, _reports

_ids = itertools.count(920_000)
SERVICES = "बिजली-पानी-सड़क"


@pytest.fixture
def rep(pool, bot, team):
    link(pool, bot, team["worker-a1"])
    return Reporter(pool, bot)


def _filed(rep):
    rep.photo("f1")
    rep.tap(SERVICES)
    rep.tap(T.BTN_URGENT_NO)
    return rep.card


def test_draft_card_after_the_draft_auto_filed(conn, pool, core, storage, limits, report_limits, rep):
    from pulse.worker.jobs import WorkerDeps
    from tests.conftest import FakeTelegramClient

    t0 = utcnow()
    rep.photo("af", now=t0)
    draft_card = rep.card
    deps = WorkerDeps(pool=pool, client=FakeTelegramClient(), core=core, storage=storage, ledger_limits=limits,
                      report_limits=report_limits, worker_id="autofile")
    assert run_timed(deps, t0 + timedelta(minutes=12))["report_autofile"] == {"filed": 1}
    run_queue(deps)
    assert rep.tap(SERVICES, card=draft_card, now=t0 + timedelta(minutes=13)) == "card_stale"
    assert rep.toast == T.CARD_STALE
    [r] = _reports(conn)
    assert r["status"] == "new" and r["category_id"] is None                 # the old card set nothing
    assert _events(conn, r["report_id"]).count("filed") == 1


def test_draft_card_from_an_older_version(conn, rep):
    rep.photo("v1")
    v0 = rep.card
    old_category = rep.data_for(SERVICES, v0)
    assert rep.tap(SERVICES) == "report_category"                             # card moves on to the urgency question
    assert rep.tap_data(old_category, v0["message_id"]) == "card_stale"
    assert rep.toast == T.CARD_STALE
    [r] = _reports(conn)
    assert r["status"] == "draft" and _events(conn, r["report_id"]).count("category_set") == 1


def test_urgency_button_of_a_filed_report(conn, rep):
    rep.photo("u1")
    rep.tap(SERVICES)
    urgency_card = rep.card
    no = rep.data_for(T.BTN_URGENT_NO, urgency_card)
    yes = rep.data_for(T.BTN_URGENT_YES, urgency_card)
    assert rep.tap_data(no, urgency_card["message_id"]) == "report_filed"
    assert rep.tap_data(yes, urgency_card["message_id"]) == "card_stale"      # filed: the question is over
    assert rep.toast == T.CARD_STALE
    [r] = _reports(conn)
    assert r["urgency"] == "normal" and r["report_no"] == "32-R-000001"
    assert _events(conn, r["report_id"]).count("filed") == 1


def test_card_of_a_replaced_draft(conn, rep):
    t0 = utcnow()
    rep.photo("a1", now=t0)
    first_card = rep.card
    rep.photo("b1", now=t0 + timedelta(minutes=5))                            # outside the join window: new draft
    first, second = _reports(conn)
    assert first["status"] == "new" and second["status"] == "draft"           # the first was filed (superseded)
    assert rep.tap(SERVICES, card=first_card, now=t0 + timedelta(minutes=6)) == "card_stale"
    assert rep.toast == T.CARD_STALE
    first, second = _reports(conn)
    assert first["category_id"] is None and second["category_id"] is None


def test_same_callback_delivered_twice(conn, rep):
    rep.photo("d1")
    rep.tap(SERVICES)
    card = rep.card
    data, uid = rep.data_for(T.BTN_URGENT_NO, card), next(_ids)
    assert rep.tap_data(data, card["message_id"], update_id=uid) == "report_filed"
    answered = len(rep.fake.answered)
    assert rep.tap_data(data, card["message_id"], update_id=uid) == "duplicate"
    assert len(rep.fake.answered) == answered
    [r] = _reports(conn)
    assert r["report_no"] == "32-R-000001"
    assert conn.execute("SELECT count(*) AS n FROM field.report_counter").fetchone()["n"] <= 1
    assert _events(conn, r["report_id"]).count("filed") == 1


def test_filed_card_tapped_by_someone_else(conn, pool, bot, team, rep):
    card = _filed(rep)
    data = rep.data_for(T.BTN_CHANGE_PLACE, card)
    link(pool, bot, team["worker-a2"], U.USER_B)
    other = Reporter(pool, bot, U.USER_B)
    assert other.tap_data(data, card["message_id"]) == "card_stale"
    assert other.toast == T.CARD_STALE
    assert _reports(conn)[0]["loc_source"] != "chosen"


def test_unlinked_reporter_taps_the_draft_card(conn, team, rep):
    rep.photo("ul")
    card = rep.card
    linking.unlink(conn, "ac-32", team["worker-a1"])
    assert rep.tap(SERVICES, card=card) == "unlinked"
    assert rep.text == T.UNREGISTERED
    [r] = _reports(conn)
    assert r["status"] == "draft" and r["category_id"] is None                # kept: it files itself later


def test_filed_card_after_the_report_was_merged(conn, core, team, rep):
    """The reporter's report was merged into another: its card no longer changes place, location or items."""
    from tests.triage_world import file_report

    original = file_report(conn, core, team["worker-a2"], area=("blk-a", "area-a1"))
    card = _filed(rep)
    mine = _reports(conn)[-1]
    v = access.viewer_for(conn, team["incharge-a"])
    triage.merge(conn, v, mine["report_id"], original["report_id"], now=utcnow())
    for label, outcome in ((T.BTN_CHANGE_PLACE, "report_merged_already"), (T.BTN_SEND_LOCATION, "report_merged_already"),
                           (T.BTN_ADD_MORE, "report_merged_already")):
        assert rep.tap(label, card=card) == outcome
        assert rep.toast == T.REPORT_MERGED_ALREADY.format(report_no=original["report_no"])
    row = conn.execute("SELECT * FROM field.report WHERE report_id = %s", (mine["report_id"],)).fetchone()
    assert row["status"] == "duplicate" and row["duplicate_of"] == original["report_id"]
    kinds = _events(conn, mine["report_id"])
    assert "more_opened" not in kinds and kinds.count("area_set") == 1


def test_change_place_of_a_report_linked_to_an_issue(conn, core, pool, bot, team):
    """An issue holds reports of one area. Moving a linked report to another area from an old card would
    break that, so the card refuses; choosing the area it already has is fine."""
    link(pool, bot, team["worker-a2"], U.USER_B)                              # two scopes: area-a2, area-a3
    rep2 = Reporter(pool, bot, U.USER_B)
    rep2.photo("is1")
    rep2.tap(SERVICES)
    rep2.tap(T.BTN_URGENT_NO)
    filed = rep2.card
    rep2.tap(T.BTN_CHANGE_PLACE, card=filed)
    rep2.tap("डेमो पंचायत क3")                                                # area-a3
    r = _reports(conn)[-1]
    assert r["area_id"] == "area-a3"
    v = access.viewer_for(conn, team["principal"])
    issue = issues.create(conn, core, v, now=utcnow(), from_report_id=r["report_id"])
    assert rep2.tap(T.BTN_CHANGE_PLACE, card=filed) == "report_pick_area"
    picker = rep2.card
    assert rep2.tap("डेमो पंचायत क2", card=picker) == "report_area_locked"
    assert rep2.toast == T.REPORT_AREA_LOCKED.format(issue_no=issue["issue_no"])
    row = conn.execute("SELECT area_id, issue_id FROM field.report WHERE report_id = %s", (r["report_id"],)).fetchone()
    assert (row["area_id"], row["issue_id"]) == ("area-a3", issue["issue_id"])
    assert rep2.tap("डेमो पंचायत क3", card=picker) == "report_area"              # same area: allowed
