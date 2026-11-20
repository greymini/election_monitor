"""Ground reports by bot (LLD §5.3), from recorded updates with a fake client."""
from __future__ import annotations

import itertools
from datetime import timedelta

import pytest

from pulse.bot import texts_hi as T
from pulse.bot.dispatch import dispatch
from pulse.common.clock import utcnow
from pulse.common.pii import MASK
from pulse.worker.runner import run_queue, run_timed
from tests import updates as U
from tests.helpers import link
from tests.test_flow_expense import Chat

_ids = itertools.count(300_000)


class Reporter(Chat):
    def send(self, update, now=None):
        return self._done(dispatch(self.pool, self.bot, update, now=now))

    def photo(self, file_id, now=None, **kw):
        return self.send(U.photo_msg(next(_ids), file_id, user=self.user, **kw), now)


@pytest.fixture
def rep(pool, bot, team):
    """worker-a1 on USER_A: a single scope (area-a1), so the area is known without asking."""
    link(pool, bot, team["worker-a1"])
    return Reporter(pool, bot)


def _reports(conn):
    return conn.execute("SELECT * FROM field.report ORDER BY created_at").fetchall()


def _events(conn, report_id):
    return [r["kind"] for r in conn.execute(
        "SELECT kind FROM field.report_event WHERE report_id = %s ORDER BY event_id", (report_id,))]


def _sent(rep):
    return [m for m in rep.fake.sent if m["chat_id"] == rep.user["id"]]


# ---- intake shapes ----------------------------------------------------------------------------

def test_single_photo_to_filed(conn, rep):
    assert rep.photo("ph1") == "report_item"
    [r] = _reports(conn)
    assert r["status"] == "draft" and r["report_no"] is None and r["area_id"] == "area-a1"
    assert r["loc_source"] == "area_default"
    card = rep.card
    assert card["text"].splitlines()[0] == T.REPORT_RECEIVED.format(items=T.ITEM_PHOTO.format(n=1))
    assert T.ASK_CATEGORY in card["text"]
    assert len(rep.buttons()) == 15
    asset = conn.execute("SELECT * FROM field.media_asset").fetchone()
    assert asset["status"] == "pending" and asset["tg_file_id"] == "ph1" and asset["kind"] == "photo"
    assert conn.execute("SELECT kind FROM field.job").fetchone()["kind"] == "media_fetch"

    assert rep.tap("बिजली-पानी-सड़क") == "report_category"
    assert T.ASK_URGENT in rep.card["text"]
    assert rep.tap(T.BTN_URGENT_NO) == "report_filed"
    [r] = _reports(conn)
    assert r["status"] == "new" and r["report_no"] == "32-R-000001" and r["urgency"] == "normal"
    assert r["restricted"] is False and r["filed_at"] is not None
    assert rep.card["text"].startswith(T.REPORT_FILED.format(report_no="32-R-000001"))
    assert rep.buttons()[-3:] == [T.BTN_CHANGE_PLACE, T.BTN_SEND_LOCATION, T.BTN_ADD_MORE]
    assert _events(conn, r["report_id"]) == ["draft_opened", "area_set", "item_added", "category_set",
                                             "urgency_set", "filed"]
    assert len(_sent(rep)) == 1                                  # one card, edited in place


def test_album_of_three_is_one_draft_with_one_card(conn, rep):
    for i in range(3):
        assert rep.photo(f"al{i}", group="album-1") == "report_item"
    [r] = _reports(conn)
    assert conn.execute("SELECT count(*) AS n FROM field.report_media").fetchone()["n"] == 3
    assert len(_sent(rep)) == 1
    assert rep.card["text"].startswith(T.REPORT_RECEIVED.format(items=T.ITEM_PHOTO.format(n=3)))


def test_voice_only(conn, rep):
    assert rep.send(U.voice_msg(next(_ids), "vo1", duration=40)) == "report_item"
    assert rep.card["text"].startswith(T.REPORT_RECEIVED.format(items=T.ITEM_VOICE.format(n=1)))
    assert conn.execute("SELECT kind, duration_s FROM field.media_asset").fetchone() == {"kind": "voice", "duration_s": 40}


def test_location_only(conn, rep):
    assert rep.send(U.location_msg(next(_ids))) == "report_item"
    [r] = _reports(conn)
    assert r["loc_source"] == "shared" and float(r["lat"]) == 24.1912
    assert rep.card["text"].startswith(T.REPORT_RECEIVED.format(items=T.ITEM_LOCATION))


def test_mixed_order_joins_one_report(conn, rep):
    rep.send(U.voice_msg(next(_ids), "m-v"))
    rep.say("pani nahi aa raha, chapakal kharab")
    rep.photo("m-p")
    rep.send(U.location_msg(next(_ids)))
    [r] = _reports(conn)
    text = rep.card["text"]
    for part in (T.ITEM_PHOTO.format(n=1), T.ITEM_VOICE.format(n=1), T.ITEM_LOCATION, T.ITEM_NOTE):
        assert part in text
    assert r["note_text"] == "pani nahi aa raha, chapakal kharab"
    assert rep.buttons()[0] == "बिजली-पानी-सड़क " + T.SUGGESTED     # keyword suggestion first


def test_rolling_window(conn, rep):
    t0 = utcnow()
    rep.photo("w1", now=t0)
    rep.photo("w2", now=t0 + timedelta(minutes=2))
    rep.photo("w3", now=t0 + timedelta(minutes=4))              # 2 minutes after the last one: still joins
    assert len(_reports(conn)) == 1
    first_card = rep.card
    rep.photo("w4", now=t0 + timedelta(minutes=8))              # 4-minute gap: a new report
    old, new = _reports(conn)
    assert old["status"] == "new" and old["report_no"] == "32-R-000001" and old["category_id"] is None
    assert new["status"] == "draft"
    assert [e for e in _events(conn, old["report_id"]) if e == "filed"] == ["filed"]
    superseded = [m for m in rep.fake.edits if m["message_id"] == first_card["message_id"]][-1]
    assert superseded["text"].startswith(T.REPORT_SUPERSEDED.format(report_no="32-R-000001"))


def test_urgent_caption_files_at_once_and_album_still_joins(conn, rep):
    assert rep.photo("u1", caption="turant: aag lagi hai, ghar jal gaya", group="g-u") == "report_item"
    [r] = _reports(conn)
    assert r["status"] == "new" and r["urgency"] == "urgent" and r["note_text"] == "aag lagi hai, ghar jal gaya"
    assert rep.card["text"].startswith(T.REPORT_FILED_URGENT.format(report_no="32-R-000001"))
    assert rep.buttons()[0] == "दुर्घटना / आपदा " + T.SUGGESTED  # asks the category afterwards
    rep.photo("u2", group="g-u")                                  # rest of the album
    assert conn.execute("SELECT count(*) AS n FROM field.report_media").fetchone()["n"] == 2
    assert "more_added" in _events(conn, r["report_id"])
    assert rep.tap("दुर्घटना / आपदा " + T.SUGGESTED) == "report_category"
    assert _reports(conn)[0]["category_id"] is not None


def test_urgent_text_without_media(conn, rep):
    assert rep.say("urgent sadak par hadsa, do log ghayal") == "report_urgent"
    [r] = _reports(conn)
    assert r["urgency"] == "urgent" and r["status"] == "new" and r["note_text"] == "sadak par hadsa, do log ghayal"


def test_text_with_a_number_joins_the_open_draft(conn, rep):
    rep.photo("n1")
    assert rep.say("1500 log prabhavit") == "report_note"
    assert conn.execute("SELECT count(*) AS n FROM field.money_txn").fetchone()["n"] == 0
    assert _reports(conn)[0]["note_text"] == "1500 log prabhavit"
    assert conn.execute("SELECT flow FROM field.tg_session").fetchone()["flow"] == "report"


def test_note_text_is_masked_never_refused(conn, rep):
    rep.photo("pii")
    assert rep.say("ramesh ka number 9876543210, aadhaar 2345 6789 0123") == "report_note"
    note = _reports(conn)[0]["note_text"]
    assert "9876543210" not in note and "2345 6789 0123" not in note and note.count(MASK) == 2
    data = conn.execute("SELECT data FROM field.report_event WHERE kind = 'note_added'").fetchone()["data"]
    assert data["masked"] is True


def test_restricted_category_flag_is_carried(conn, rep):
    rep.photo("law")
    rep.tap("कानून-व्यवस्था")
    rep.tap(T.BTN_URGENT_YES)
    r = _reports(conn)[0]
    assert r["restricted"] is True and r["urgency"] == "urgent"


# ---- limits and refusals ----------------------------------------------------------------------

def test_oversize_file_gets_a_shorter_clip_reply(conn, rep, report_limits):
    assert rep.send(U.video_msg(next(_ids), "big", size=report_limits.max_download_bytes + 1)) == "media_refused"
    assert _sent(rep)[-1]["text"] == T.MEDIA_TOO_BIG.format(mb=20)
    assert _reports(conn) == [] and conn.execute("SELECT count(*) AS n FROM field.media_asset").fetchone()["n"] == 0


def test_eleventh_photo_is_refused_once(conn, rep):
    for i in range(12):
        rep.photo(f"lim{i}", group="big-album")
    assert conn.execute("SELECT count(*) AS n FROM field.report_media").fetchone()["n"] == 10
    notices = [m for m in _sent(rep) if m["text"] == T.PHOTO_LIMIT.format(n=10)]
    assert len(notices) == 1                                     # not one per refused item
    assert _events(conn, _reports(conn)[0]["report_id"]).count("item_refused") == 2


def test_voice_limit_is_five_minutes_per_report(conn, rep):
    rep.send(U.voice_msg(next(_ids), "v1", duration=200))
    assert rep.send(U.voice_msg(next(_ids), "v2", duration=120)) == "media_refused"
    assert T.VOICE_LIMIT.format(minutes=5) in [m["text"] for m in _sent(rep)]


def test_unsupported_files(conn, rep):
    assert rep.send(U.document_msg(next(_ids), "heic", "image/heic")) == "media_refused"
    assert _sent(rep)[-1]["text"] == T.MEDIA_IMAGE_UNSUPPORTED
    assert rep.send(U.document_msg(next(_ids), "zip", "application/zip")) == "media_refused"
    assert _sent(rep)[-1]["text"] == T.MEDIA_TYPE_UNSUPPORTED
    assert rep.send(U.document_msg(next(_ids), "pdf", "application/pdf")) == "report_item"


# ---- auto-file --------------------------------------------------------------------------------

def test_autofile_after_ten_minutes_even_after_restart(conn, pool, core, storage, limits, report_limits, rep):
    from pulse.worker.jobs import WorkerDeps
    from tests.conftest import FakeTelegramClient

    t0 = utcnow()
    rep.photo("af", now=t0)
    rep.say("ghar mein aag", now=t0 + timedelta(minutes=1))
    # a brand-new process: new client, new deps; only the database carries the draft
    fresh = FakeTelegramClient()
    deps = WorkerDeps(pool=pool, client=fresh, core=core, storage=storage, ledger_limits=limits,
                      report_limits=report_limits, worker_id="after-restart")
    assert run_timed(deps, t0 + timedelta(minutes=10)).get("report_autofile") == {"filed": 0}   # 9 min untouched
    assert run_timed(deps, t0 + timedelta(minutes=10, seconds=30)) == {}                           # ran 30 s ago: not due
    assert run_timed(deps, t0 + timedelta(minutes=12))["report_autofile"] == {"filed": 1}
    run_queue(deps)                                                  # the notice goes out through the queue
    [r] = _reports(conn)
    assert r["status"] == "new" and r["category_id"] is None
    assert r["urgency"] == "urgent"                                 # from the keyword hint "aag"
    src = conn.execute("SELECT data FROM field.report_event WHERE kind = 'urgency_set'").fetchone()["data"]
    assert src == {"urgency": "urgent", "source": "rule"}
    notice = fresh.edits[-1]
    assert notice["text"].startswith(T.REPORT_AUTOFILED.format(report_no="32-R-000001"))
    data = notice["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    assert data.startswith("q:")
    assert rep.tap_data(data, notice["message_id"]) == "report_category"   # category still settable
    assert _reports(conn)[0]["category_id"] is not None
    assert rep.tap_data(data, notice["message_id"]) == "report_category_set_already"


# ---- after filing -----------------------------------------------------------------------------

def _filed(rep):
    rep.photo("f1")
    rep.tap("बिजली-पानी-सड़क")
    rep.tap(T.BTN_URGENT_NO)


def test_add_more_for_ten_minutes(conn, rep):
    t0 = utcnow()
    _filed(rep)
    rep.tap(T.BTN_ADD_MORE, now=t0)
    assert _sent(rep)[-1]["text"] == T.ADD_MORE_OPEN.format(minutes=10, report_no="32-R-000001")
    assert rep.photo("f2", now=t0 + timedelta(minutes=9)) == "report_item"
    assert rep.say("transformer jal gaya", now=t0 + timedelta(minutes=9)) == "report_note"
    assert len(_reports(conn)) == 1
    rep.photo("f3", now=t0 + timedelta(minutes=25))
    assert len(_reports(conn)) == 2                                # window over: a new draft


def test_change_place_and_send_location(conn, rep):
    _filed(rep)
    filed = rep.card
    assert rep.tap(T.BTN_CHANGE_PLACE, card=filed) == "report_pick_area"
    assert rep.tap("डेमो पंचायत क1") == "report_area"
    assert _reports(conn)[0]["loc_source"] == "chosen"
    assert rep.tap(T.BTN_SEND_LOCATION) == "report_ask_location"
    ask = _sent(rep)[-1]
    assert ask["reply_markup"]["keyboard"][0][0]["request_location"] is True
    assert rep.send(U.location_msg(next(_ids), lat=24.2, lon=86.3)) == "report_location"
    assert _sent(rep)[-1]["reply_markup"] == {"remove_keyboard": True}
    r = _reports(conn)[0]
    assert r["loc_source"] == "shared" and float(r["lat"]) == 24.2 and len(_reports(conn)) == 1


def test_area_is_asked_after_filing_when_unknown(conn, pool, bot, team):
    link(pool, bot, team["worker-a2"], U.USER_B)                  # two scopes, no history
    other = Reporter(pool, bot, U.USER_B)
    other.photo("x1")
    assert _reports(conn)[0]["area_id"] is None and _reports(conn)[0]["loc_source"] == "none"
    other.tap("शिक्षा")
    other.tap(T.BTN_URGENT_NO)                                     # filing never waits for the area
    assert T.REPORT_AREA.format(area="—") in other.card["text"]
    other.tap(T.BTN_CHANGE_PLACE)
    other.tap("डेमो पंचायत क3")
    assert _reports(conn)[0]["area_id"] == "area-a3"


def test_card_edit_rate_limited_is_refreshed_by_worker(conn, rep, worker, fake_tg):
    rep.photo("r1", group="rl")
    fake_tg.rate_limit_edits = True
    rep.photo("r2", group="rl")
    rep.photo("r3", group="rl")
    assert len(_sent(rep)) == 1                                    # never a new message per item
    jobs = conn.execute("SELECT * FROM field.job WHERE kind = 'report_card' AND status = 'queued'").fetchall()
    assert len(jobs) == 1                                          # coalesced
    fake_tg.rate_limit_edits = False
    run_queue(worker, utcnow() + timedelta(seconds=5))
    assert rep.fake.edits[-1]["text"].startswith(T.REPORT_RECEIVED.format(items=T.ITEM_PHOTO.format(n=3)))


def test_me_lists_last_reports(conn, rep):
    _filed(rep)
    rep.photo("d1", now=utcnow() + timedelta(minutes=30))
    rep.say("namaste")                                             # joins the open draft as a note
    shown = rep.send(U.callback(next(_ids), "menu:me", rep.user, message_id=1))
    assert shown == "me"
    text = rep.mine[-1]["text"]
    assert T.ME_REPORTS in text
    assert "32-R-000001" in text and T.REPORT_STATUS["new"] in text and "बिजली-पानी-सड़क" in text
    assert T.ME_REPORT_NO_NUMBER in text and T.REPORT_STATUS["draft"] in text


def test_album_items_arriving_at_once_open_one_draft(conn, rep):
    """Telegram delivers an album as separate updates; with a webhook they can be handled concurrently.
    The per-chat lock makes them queue, so there is one draft and one card."""
    import threading

    updates = [U.photo_msg(next(_ids), f"cc{i}", user=rep.user, group="burst") for i in range(4)]
    barrier = threading.Barrier(4)

    def go(u):
        barrier.wait()
        rep.send(u)

    threads = [threading.Thread(target=go, args=(u,)) for u in updates]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(_reports(conn)) == 1
    assert conn.execute("SELECT count(*) AS n FROM field.report_media").fetchone()["n"] == 4
    assert len(_sent(rep)) == 1
