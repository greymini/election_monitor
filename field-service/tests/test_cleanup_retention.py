"""chat_cleanup and media_retention (slice 2c): money cards go after the configured hours, alert cards on
open reports stay, nothing older than Telegram's 48-hour window is tried; retention deletes the file
and keeps the asset row and the report."""
from __future__ import annotations

from datetime import timedelta

from pulse import settings_ac
from pulse.bot import texts_hi as T
from pulse.common.clock import utcnow
from pulse.reports import lifecycle
from pulse.worker import maintenance
from pulse.worker.jobs import TIMED
from pulse.worker.runner import run_queue
from tests import updates as U
from tests.helpers import link
from tests.test_flow_report import Reporter
from tests.test_storage_imagemeta import make_jpeg
from tests.triage_world import alerts_for, file_report


def test_jobs_are_scheduled():
    every = {name: secs for name, secs, _ in TIMED}
    assert every["chat_cleanup"] == 3600 and every["media_retention"] == 86400


def _recorded(conn, chat_id):
    return conn.execute("SELECT * FROM field.bot_message WHERE chat_id = %s ORDER BY sent_at", (chat_id,)).fetchall()


def test_money_cards_go_alert_cards_on_open_reports_stay(conn, core, world, worker):
    world.rep.say("500 chai nashta")
    assert world.rep.tap(T.BTN_CONFIRM) == "posted"
    [card] = [b for b in _recorded(conn, U.USER_C["id"]) if b["purpose"] == "money"]
    r = file_report(conn, core, world.team["worker-a1"], urgency="urgent")
    world.drain()
    alerts = alerts_for(conn, r["report_id"])
    assert alerts and all(a["status"] == "sent" for a in alerts)

    later = utcnow() + timedelta(hours=25)                               # default chat_cleanup_hours is 24
    counts = maintenance.chat_cleanup(worker, later)
    assert (U.USER_C["id"], card["message_id"]) in world.fake.deleted
    alert_ids = {(a["chat_id"], a["message_id"]) for a in alerts}
    assert not alert_ids & set(world.fake.deleted)                       # open report: its alert cards stay
    assert all(a["deleted_at"] is None for a in alerts_for(conn, r["report_id"]))
    assert counts["deleted"] >= 1

    # once the report is closed its cards are cleaned up too
    lifecycle.change(conn, r["report_id"], "acknowledged", actor=world.team["principal"], now=later)
    lifecycle.change(conn, r["report_id"], "resolved", actor=world.team["principal"], now=later)
    maintenance.chat_cleanup(worker, later)
    assert alert_ids <= set(world.fake.deleted)
    assert all(a["deleted_at"] is not None for a in alerts_for(conn, r["report_id"]))


def test_not_before_the_configured_age(conn, world, worker):
    world.rep.say("500 chai nashta")
    world.rep.tap(T.BTN_CONFIRM)
    assert maintenance.chat_cleanup(worker, utcnow() + timedelta(hours=23))["deleted"] == 0
    settings_ac.change(conn, "ac-32", {"chat_cleanup_hours": 2}, actor_user_id="usr-principal")
    assert maintenance.chat_cleanup(worker, utcnow() + timedelta(hours=3))["deleted"] == 1


def test_older_than_48_hours_is_not_tried(conn, world, worker):
    world.rep.say("500 chai nashta")
    world.rep.tap(T.BTN_CONFIRM)
    before = len(world.fake.deleted)
    counts = maintenance.chat_cleanup(worker, utcnow() + timedelta(hours=49))
    assert counts["too_old"] >= 1 and len(world.fake.deleted) == before
    assert all(b["delete_error"] == "too_old" for b in _recorded(conn, U.USER_C["id"]))
    assert maintenance.chat_cleanup(worker, utcnow() + timedelta(hours=50))["too_old"] == 0   # marked once


def test_open_draft_card_is_left_alone(conn, world, worker):
    world.rep.say("500 chai nashta")                                     # confirm card, draft still open
    card = world.rep.card["message_id"]
    conn.execute("UPDATE field.tg_session SET expires_at = now() + interval '3 days'")   # still being worked on
    assert maintenance.chat_cleanup(worker, utcnow() + timedelta(hours=25))["deleted"] == 0
    assert (U.USER_C["id"], card) not in world.fake.deleted
    conn.execute("UPDATE field.tg_session SET expires_at = now()")                        # draft over
    assert maintenance.chat_cleanup(worker, utcnow() + timedelta(hours=25))["deleted"] == 1


def test_refused_delete_is_recorded(conn, world, worker):
    world.rep.say("500 chai nashta")
    world.rep.tap(T.BTN_CONFIRM)
    world.fake.refuse_delete = True
    counts = maintenance.chat_cleanup(worker, utcnow() + timedelta(hours=25))
    assert counts["failed"] == 1
    assert _recorded(conn, U.USER_C["id"])[0]["delete_error"].startswith("telegram")


# ---- media retention --------------------------------------------------------------------------

def _stored_photo(pool, bot, team, worker, fake_tg, file_id="ret1"):
    link(pool, bot, team["worker-a1"])
    fake_tg.files[file_id] = make_jpeg()
    Reporter(pool, bot).photo(file_id)
    run_queue(worker)


def test_retention_purges_file_keeps_report(conn, pool, bot, team, worker, fake_tg, storage):
    _stored_photo(pool, bot, team, worker, fake_tg)
    a = conn.execute("SELECT * FROM field.media_asset").fetchone()
    assert a["status"] == "stored" and storage.exists(a["storage_key"])
    assert maintenance.media_retention(worker, utcnow() + timedelta(days=400)) == {"purged": 0, "files_deleted": 0}

    settings_ac.change(conn, "ac-32", {"media_retention_days": 30}, actor_user_id="usr-principal")
    assert maintenance.media_retention(worker, utcnow() + timedelta(days=29))["purged"] == 0
    assert maintenance.media_retention(worker, utcnow() + timedelta(days=31)) == {"purged": 1, "files_deleted": 1}
    after = conn.execute("SELECT * FROM field.media_asset WHERE asset_id = %s", (a["asset_id"],)).fetchone()
    assert after["status"] == "purged" and after["storage_key"] is None and after["purged_at"] is not None
    assert after["sha256"] == a["sha256"]                                # the row is kept
    assert not storage.exists(a["storage_key"])
    report_id = conn.execute("SELECT report_id FROM field.report_media WHERE asset_id = %s",
                             (a["asset_id"],)).fetchone()["report_id"]
    assert conn.execute("SELECT 1 FROM field.report WHERE report_id = %s", (report_id,)).fetchone()
    assert conn.execute("SELECT count(*) AS n FROM field.report_event WHERE report_id = %s AND kind = 'media_purged'",
                        (report_id,)).fetchone()["n"] == 1


def test_shared_file_kept_while_another_asset_needs_it(conn, pool, bot, team, worker, fake_tg, storage):
    _stored_photo(pool, bot, team, worker, fake_tg, "same-a")
    first = conn.execute("SELECT * FROM field.media_asset").fetchone()
    fake_tg.files["same-b"] = make_jpeg()
    Reporter(pool, bot).photo("same-b")
    run_queue(worker)
    conn.execute("UPDATE field.media_asset SET delete_after = now() - interval '1 day' WHERE asset_id = %s",
                 (first["asset_id"],))
    assert maintenance.media_retention(worker, utcnow()) == {"purged": 1, "files_deleted": 0}
    assert storage.exists(first["storage_key"])
