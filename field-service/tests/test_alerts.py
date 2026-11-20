"""Report alerts (LLD §5.4): recipients, card contents, first acknowledgement wins, delivery problems."""
from __future__ import annotations

from datetime import timedelta

from pulse.bot import texts_hi as T
from pulse.common.clock import utcnow
from pulse.worker.runner import run_queue
from tests import updates as U
from tests.triage_world import alerts_for, file_report

P, COORD, INCH = "डेमो प्रत्याशी", "डेमो प्रभारी ख", "डेमो प्रभारी क"


def _buttons(msg):
    return [b["text"] for row in (msg["reply_markup"] or {}).get("inline_keyboard", []) for b in row]


def test_urgent_alert_recipients_and_card(conn, core, world):
    r = file_report(conn, core, world.team["worker-a1"], urgency="urgent", photo="ph-urgent", note="sadak par hadsa")
    world.drain()
    rows = alerts_for(conn, r["report_id"])
    assert {x["display_name"] for x in rows} == {INCH, COORD, P}            # parent, coordinator, principal
    assert all(x["status"] == "sent" and x["is_media"] for x in rows)
    card = world.principal.mine[-1]
    assert card["photo"] == "ph-urgent"                                      # first photo re-sent by file_id
    text = card["text"]
    assert text.splitlines()[0] == T.ALERT_URGENT.format(category="बिजली-पानी-सड़क")
    assert "डेमो पंचायत क1" in text and T.ALERT_REPORTER.format(name="डेमो कार्यकर्ता क1") in text
    assert "sadak par hadsa" in text and r["report_no"] in text
    assert _buttons(card) == [T.BTN_SEEN, T.BTN_ASSIGN, T.BTN_ISSUE_LINK, T.BTN_TALK_TO_REPORTER]   # issue: principal
    url = card["reply_markup"]["inline_keyboard"][-1][0]["url"]
    assert url == f"tg://user?id={U.USER_C['id']}"                          # a link, never a phone number
    assert world.rep.mine == [] or all("🔴" not in (m["text"] or "") for m in world.rep.mine)   # reporter not alerted


def test_restricted_alert_is_text_only_and_narrower(conn, core, world):
    r = file_report(conn, core, world.team["worker-a1"], category="LAW", urgency="urgent", photo="ph-law")
    world.drain()
    rows = alerts_for(conn, r["report_id"])
    assert {x["display_name"] for x in rows} == {COORD, P}                   # not the reporter's parent
    assert not any(x["is_media"] for x in rows)
    card = world.principal.mine[-1]
    assert "photo" not in card and card["text"].startswith(T.ALERT_RESTRICTED.format(category="कानून-व्यवस्था"))


def test_normal_report_goes_to_the_parent_only(conn, core, world):
    r = file_report(conn, core, world.team["worker-a1"])
    world.drain()
    assert [x["display_name"] for x in alerts_for(conn, r["report_id"])] == [INCH]
    assert world.incharge.mine[-1]["text"].startswith(T.ALERT_NORMAL.format(category="बिजली-पानी-सड़क"))


def test_autofiled_unsorted_goes_to_coordinators(conn, core, world):
    r = file_report(conn, core, world.team["worker-a1"], category=None, how="auto")
    world.drain()
    rows = alerts_for(conn, r["report_id"], "unsorted")
    assert [x["display_name"] for x in rows] == [COORD]
    card = world.coord.mine[-1]
    assert card["text"].startswith(T.ALERT_UNSORTED.format(report_no=r["report_no"]))
    assert "शिक्षा" in _buttons(card)                                          # can sort it from the card
    assert world.coord.tap("शिक्षा", card=card) == "report_category"
    assert conn.execute("SELECT category_id FROM field.report WHERE report_id = %s", (r["report_id"],)).fetchone()["category_id"]


def test_unsorted_falls_back_to_principal_without_coordinators(conn, core, world):
    conn.execute("UPDATE field.member SET is_coordinator = false")
    r = file_report(conn, core, world.team["worker-a1"], category=None, how="auto")
    world.drain()
    assert [x["display_name"] for x in alerts_for(conn, r["report_id"], "unsorted")] == [P]


def test_first_acknowledgement_wins(conn, core, world):
    r = file_report(conn, core, world.team["worker-a1"], urgency="urgent")
    world.drain()
    coord_card = world.coord.mine[-1]
    assert world.principal.tap(T.BTN_SEEN) == "report_acknowledged"
    row = conn.execute("SELECT * FROM field.report WHERE report_id = %s", (r["report_id"],)).fetchone()
    assert row["status"] == "acknowledged" and row["ack_by"] == world.team["principal"]
    # the other cards were redrawn through the queue: who and when, no acknowledge button
    redrawn = [m for m in world.fake.edits if m["message_id"] == coord_card["message_id"]][-1]
    assert T.ALERT_SEEN_BY.format(name=P, time="").rstrip() in redrawn["text"]
    assert T.BTN_SEEN not in _buttons(redrawn) and T.BTN_ASSIGN in _buttons(redrawn)
    assert world.coord.tap(T.BTN_SEEN, card=coord_card) == "report_already_seen"
    assert world.coord.toast.startswith(T.ALREADY_SEEN.split("{")[0] + P)
    feedback = [m["text"] for m in world.rep.mine]
    assert feedback.count(T.FEEDBACK_SEEN.format(report_no=r["report_no"])) == 1
    assert conn.execute("SELECT count(*) AS n FROM field.report_event WHERE kind = 'status_changed'").fetchone()["n"] == 1


def test_blocked_recipient_does_not_stop_the_others(conn, core, world):
    world.fake.fail_chats.add(U.USER_A["id"])                               # the principal blocked the bot
    r = file_report(conn, core, world.team["worker-a1"], urgency="urgent", photo="ph-b")
    world.drain()
    status = {x["display_name"]: x["status"] for x in alerts_for(conn, r["report_id"])}
    assert status == {P: "blocked", COORD: "sent", INCH: "sent"}
    ev = conn.execute("SELECT data FROM field.report_event WHERE kind = 'alert_not_delivered'").fetchone()["data"]
    assert ev["reason"] == "blocked" and ev["recipient"] == str(world.team["principal"])


def test_unlinked_recipient_is_recorded(conn, core, world, pool):
    conn.execute("UPDATE field.tg_link SET revoked_at = now() WHERE member_id = %s", (world.team["incharge-a"],))
    r = file_report(conn, core, world.team["worker-a1"])
    world.drain()
    assert alerts_for(conn, r["report_id"])[0]["status"] == "no_link"
    assert conn.execute("SELECT data FROM field.report_event WHERE kind = 'alert_not_delivered'").fetchone()["data"]["reason"] == "no_link"


def test_rate_limit_waits_exactly_retry_after(conn, core, world, worker):
    file_report(conn, core, world.team["worker-a1"])
    world.fake.rate_limit_sends = 1
    world.fake.retry_after = 7
    t = utcnow()
    assert dict(run_queue(worker, t)).popitem()[1] == "deferred"
    job = conn.execute("SELECT * FROM field.job WHERE kind = 'tg_send'").fetchone()
    assert job["status"] == "queued" and job["attempts"] == 0
    assert abs((job["run_after"] - t).total_seconds() - 7) < 1
    assert run_queue(worker, t + timedelta(seconds=3)) == []                 # not before retry_after
    assert [res for _, res in run_queue(worker, t + timedelta(seconds=8))] == ["sent"]


def test_card_resent_without_contact_button_when_refused(conn, core, world):
    world.fake.refuse_user_buttons = True
    r = file_report(conn, core, world.team["worker-a1"])
    world.drain()
    row = alerts_for(conn, r["report_id"])[0]
    assert row["status"] == "sent" and row["body"]["no_contact"] is True
    assert T.BTN_TALK_TO_REPORTER not in _buttons(world.incharge.mine[-1])
