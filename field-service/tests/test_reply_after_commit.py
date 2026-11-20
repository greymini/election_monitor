"""Bot replies never roll back saved input (pulse/bot/outgoing.py).

For every flow: Telegram fails the reply with a 429, a network error or a 5xx. The member's input stays
saved; the reply is queued (alert_message kind 'reply'), delivered once when Telegram is back, and says it
was already saved; a duplicate delivery of the same update creates nothing new. Only a failure before the
commit says "not saved, send again"."""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from pulse.bot import texts_hi as T
from pulse.bot.dispatch import dispatch
from pulse.bot.ui import CB_MENU_GIVE, CB_MENU_ME
from pulse.common.clock import utcnow
from pulse.ledger import service as ledger
from tests import updates as U
from tests.helpers import link
from tests.test_flow_expense import Chat, _ids
from tests.test_flow_report import Reporter
from tests.test_followup import ASK_DAY, _expense, _run
from tests.triage_world import file_report, world  # noqa: F401  (fixture)
from tests.test_flow_return_ack import pair  # noqa: F401  (fixture)
from tests.test_pin import PIN, pinbot, worker_chat  # noqa: F401  (fixtures)
from tests.test_stale_return import _to_confirm

MODES = ["429", "network", "5xx"]
LATER = timedelta(minutes=10)


@pytest.fixture(params=MODES)
def mode(request):
    return request.param


def _replies(conn, status=None):
    sql = "SELECT * FROM field.alert_message WHERE kind = 'reply'" + (" AND status = %s" if status else "")
    return conn.execute(sql + " ORDER BY alert_id", (status,) if status else ()).fetchall()


def _count(conn, sql, *args):
    return conn.execute(sql, args).fetchone()["n"]


def _delivered_after_outage(conn, fake, drain, chat_id, confirm):
    """Telegram is back: the queued reply goes out once, saying the input was already saved."""
    queued = _replies(conn, "queued")
    assert queued and confirm in [r["body"]["confirm"] for r in queued]
    assert all(set(r["body"]) <= {"render", "confirm", "flow", "sid"} for r in queued)     # ids and codes only
    fake.come_back()
    before = len(fake.sent)
    drain(utcnow() + LATER)
    saved = [m for m in fake.sent[before:] if m["chat_id"] == chat_id and T.REPLY_SAVED in (m["text"] or "")]
    assert len(saved) == 1, [m["text"] for m in fake.sent[before:]]
    assert not _replies(conn, "queued")
    again = len(fake.sent)
    drain(utcnow() + 2 * LATER)
    assert not [m for m in fake.sent[again:] if T.REPLY_SAVED in (m["text"] or "")]     # once only
    return saved[0]


# ---- money --------------------------------------------------------------------------------------

@pytest.fixture
def wchat(pool, bot, team):
    """worker A1's chat (USER_A)."""
    link(pool, bot, team["worker-a1"])
    return Chat(pool, bot)


def test_expense(conn, wchat, drain, mode):
    wchat.say("1500 chai")
    card = wchat.card
    data, uid = wchat.data_for(T.BTN_CONFIRM, card), next(_ids)
    wchat.fake.go_down(mode)
    assert wchat.tap_data(data, card["message_id"], update_id=uid) == "posted"
    assert _count(conn, "SELECT count(*) AS n FROM field.money_txn") == 1                   # saved, not rolled back
    msg = _delivered_after_outage(conn, wchat.fake, drain, U.USER_A["id"], "txn")
    assert T.ALREADY_POSTED.format(txn_no="32-E-000001") in msg["text"]
    assert wchat.tap_data(data, card["message_id"], update_id=uid) == "duplicate"
    assert _count(conn, "SELECT count(*) AS n FROM field.money_txn") == 1


def test_saved_entry_shows_in_my_account_and_on_the_web_without_its_confirmation(conn, wchat, core, team):
    wchat.say("1500 chai")
    wchat.fake.go_down("network")
    assert wchat.tap(T.BTN_CONFIRM) == "posted"
    wchat.fake.come_back()                                     # the confirmation never arrived (still queued)
    assert wchat.tap_data(CB_MENU_ME, 1) == "me"
    assert "₹1,500" in wchat.text                               # "मेरा हिसाब"
    from pulse.ledger import queries
    from pulse.ledger.links import viewer_for

    rows, total = queries.list_txns(conn, viewer_for(conn, team["worker-a1"]), queries.TxnFilter())
    assert total == 1 and rows[0]["txn_no"] == "32-E-000001"   # what GET /txns returns


def test_advance(conn, pool, bot, team, drain, mode):
    link(pool, bot, team["principal"], U.USER_A)
    link(pool, bot, team["incharge-a"], U.USER_B)
    p = Chat(pool, bot, U.USER_A)
    p.say("namaste")
    p.tap_data(CB_MENU_GIVE, p.mine[-1]["message_id"])
    p.tap(p.buttons()[0])
    assert p.say("5000") == "confirm"
    card = p.card
    data, uid = p.data_for(T.BTN_RECORD, card), next(_ids)
    p.fake.go_down(mode)
    assert p.tap_data(data, card["message_id"], update_id=uid) == "posted"
    assert _count(conn, "SELECT count(*) AS n FROM field.money_txn WHERE kind = 'advance'") == 1
    _delivered_after_outage(conn, p.fake, drain, U.USER_A["id"], "txn")
    assert p.tap_data(data, card["message_id"], update_id=uid) == "duplicate"
    assert _count(conn, "SELECT count(*) AS n FROM field.money_txn") == 1


def test_acknowledgement(conn, pool, bot, core, limits, team, worker, drain, mode):
    link(pool, bot, team["incharge-a"], U.USER_B)
    receiver = Chat(pool, bot, U.USER_B)
    from pulse.bot import flow_ack
    from pulse.worker.jobs import ctx_for

    row = ledger.create_advance(conn, core, team["principal"], to_member_id=team["incharge-a"], amount=Decimal("5000"),
                                limits=limits)
    flow_ack.request_ack(ctx_for(worker, conn, utcnow()), row)
    drain()
    card = receiver.card
    data, uid = receiver.data_for(T.BTN_ACK_YES, card), next(_ids)
    receiver.fake.go_down(mode)
    assert receiver.tap_data(data, card["message_id"], update_id=uid) == "acknowledged"
    assert conn.execute("SELECT status FROM field.money_txn").fetchone()["status"] == "posted"
    _delivered_after_outage(conn, receiver.fake, drain, U.USER_B["id"], "txn")
    assert receiver.tap_data(data, card["message_id"], update_id=uid) == "duplicate"


def test_return(conn, team, pair, drain, mode):  # noqa: F811
    _, w = pair
    card = _to_confirm(w)
    data, uid = w.data_for(T.BTN_RECORD, card), next(_ids)
    w.fake.go_down(mode)
    assert w.tap_data(data, card["message_id"], update_id=uid) == "posted"
    assert _count(conn, "SELECT count(*) AS n FROM field.money_txn WHERE kind = 'return'") == 1
    _delivered_after_outage(conn, w.fake, drain, U.USER_C["id"], "txn")
    assert w.tap_data(data, card["message_id"], update_id=uid) == "duplicate"
    assert _count(conn, "SELECT count(*) AS n FROM field.money_txn WHERE kind = 'return'") == 1


# ---- reports ------------------------------------------------------------------------------------

@pytest.fixture
def rep(pool, bot, team):
    link(pool, bot, team["worker-a1"])
    return Reporter(pool, bot)


def test_report_draft_item(conn, rep, drain, mode):
    """A photo joining a draft edits the draft card in place. If that edit fails, the card is redrawn later
    from the database by the coalesced report_card job (a fast album must not become a stream of messages);
    the redrawn card counts what was saved."""
    rep.fake.files.update({"ph1": b"\xff\xd8a", "ph2": b"\xff\xd8b"})
    rep.photo("ph1")
    card_id = rep.card["message_id"]
    rep.fake.go_down(mode)
    upd = U.photo_msg(next(_ids), "ph2", user=rep.user)
    assert rep.send(upd) == "report_item"
    assert _count(conn, "SELECT count(*) AS n FROM field.report_media") == 2                # the photo is kept
    assert _count(conn, "SELECT count(*) AS n FROM field.job WHERE kind = 'report_card'") == 1
    shown = _alert_card_redrawn_once(conn, rep.fake, drain, card_id)
    assert T.ITEM_PHOTO.format(n=2) in shown["text"]
    assert rep.send(upd) == "duplicate"
    assert _count(conn, "SELECT count(*) AS n FROM field.report_media") == 2


def test_report_filing(conn, rep, drain, mode):
    rep.fake.files["ph1"] = b"\xff\xd8a"
    rep.photo("ph1")
    rep.tap("बिजली-पानी-सड़क")
    card = rep.card
    data, uid = rep.data_for(T.BTN_URGENT_NO, card), next(_ids)
    rep.fake.go_down(mode)
    assert rep.tap_data(data, card["message_id"], update_id=uid) == "report_filed"
    assert conn.execute("SELECT status, report_no FROM field.report").fetchone() == {"status": "new",
                                                                                     "report_no": "32-R-000001"}
    msg = _delivered_after_outage(conn, rep.fake, drain, U.USER_A["id"], "report")
    assert "32-R-000001" in msg["text"]
    assert rep.tap_data(data, card["message_id"], update_id=uid) == "duplicate"


def _alert_card_redrawn_once(conn, fake, drain, message_id):
    """Triage replies are edits of the alert card: queued as a tg_edit, which draws the card from the
    database, so it shows what was saved."""
    fake.come_back()
    before = len(fake.edits)
    drain(utcnow() + LATER)
    redrawn = [e for e in fake.edits[before:] if e["message_id"] == message_id]
    assert len(redrawn) == 1
    return redrawn[0]


def test_triage_acknowledge(conn, core, world, mode):  # noqa: F811
    r = file_report(conn, core, world.team["worker-a1"], urgency="urgent")
    world.drain()
    card = world.incharge.mine[-1]
    data, uid = world.incharge.data_for(T.BTN_SEEN, card), next(_ids)
    world.fake.go_down(mode)
    assert world.incharge.tap_data(data, card["message_id"], update_id=uid) == "report_acknowledged"
    assert conn.execute("SELECT status FROM field.report WHERE report_id = %s", (r["report_id"],)).fetchone()["status"] \
        == "acknowledged"
    shown = _alert_card_redrawn_once(conn, world.fake, world.drain, card["message_id"])
    assert T.BTN_SEEN not in [b["text"] for row in (shown["reply_markup"] or {}).get("inline_keyboard", []) for b in row]
    assert world.incharge.tap_data(data, card["message_id"], update_id=uid) == "duplicate"


def test_issue_linking(conn, core, world, mode):  # noqa: F811
    r = file_report(conn, core, world.team["worker-a1"], urgency="urgent")
    world.drain()
    principal = world.principal
    card = next(m for m in reversed(principal.mine) if m["reply_markup"] and T.BTN_ISSUE_LINK in principal.buttons(m))
    assert principal.tap(T.BTN_ISSUE_LINK, card=card) == "report_pick_issue"
    picker = principal.mine[-1] if principal.mine[-1]["reply_markup"] else card
    data, uid = principal.data_for(T.BTN_ISSUE_NEW, picker), next(_ids)
    world.fake.go_down(mode)
    assert principal.tap_data(data, picker["message_id"], update_id=uid) == "report_issue_created"
    assert conn.execute("SELECT issue_id FROM field.report WHERE report_id = %s", (r["report_id"],)).fetchone()["issue_id"]
    issue_no = conn.execute("SELECT issue_no FROM field.issue").fetchone()["issue_no"]
    shown = _alert_card_redrawn_once(conn, world.fake, world.drain, picker["message_id"])
    assert issue_no in shown["text"]
    assert principal.tap_data(data, picker["message_id"], update_id=uid) == "duplicate"
    assert _count(conn, "SELECT count(*) AS n FROM field.issue") == 1


def test_followup_answer(conn, core, limits, team, wchat, worker, drain, mode):
    txn = _expense(conn, core, limits, team["worker-a1"], "WORKS")
    _run(worker, drain, ASK_DAY)
    card = wchat.card
    data, uid = wchat.data_for(T.BTN_FOLLOWUP_PARTIAL, card), next(_ids)
    wchat.fake.go_down(mode)
    assert wchat.tap_data(data, card["message_id"], update_id=uid) == "followup_answered"
    assert conn.execute("SELECT answer FROM field.txn_followup WHERE txn_id = %s", (txn["txn_id"],)).fetchone()["answer"] \
        == "partial"
    _delivered_after_outage(conn, wchat.fake, drain, U.USER_A["id"], "followup")
    assert wchat.tap_data(data, card["message_id"], update_id=uid) == "duplicate"


# ---- PIN ----------------------------------------------------------------------------------------

def test_pin_saved_and_pin_messages_deleted_on_retry(conn, worker_chat, team, drain, mode):
    from pulse.bot.flow_pin import CB_SET
    from pulse.team import pin as pins

    assert worker_chat.tap_data(CB_SET, 1) == "pin_ask_new"
    assert worker_chat.say(PIN) == "pin_ask_confirm"
    uid = next(_ids)
    upd = U.text(uid, PIN, worker_chat.user)
    worker_chat.fake.go_down(mode)
    assert dispatch(worker_chat.pool, worker_chat.bot, upd) == "pin_saved"
    assert pins.has_pin(conn, team["worker-a1"])
    assert (U.USER_A["id"], upd["message"]["message_id"]) not in worker_chat.fake.deleted     # delete failed...
    assert conn.execute("SELECT count(*) AS n FROM field.job WHERE kind = 'tg_delete'").fetchone()["n"] >= 1
    _delivered_after_outage(conn, worker_chat.fake, drain, U.USER_A["id"], "pin")
    assert (U.USER_A["id"], upd["message"]["message_id"]) in worker_chat.fake.deleted         # ...and was retried
    assert not any(PIN in (m.get("text") or "") for m in worker_chat.fake.sent)
    assert dispatch(worker_chat.pool, worker_chat.bot, upd) == "duplicate"


def test_pin_delete_that_keeps_failing_asks_the_member_to_delete_it(conn, worker_chat, team, drain):
    from pulse.bot.flow_pin import CB_SET

    worker_chat.tap_data(CB_SET, 1)
    worker_chat.fake.refuse_delete = True                      # Telegram refuses every delete
    assert worker_chat.say(PIN) == "pin_ask_confirm"
    for i in range(10):
        drain(utcnow() + timedelta(hours=i + 1))
    assert T.PIN_NOT_DELETED in [m["text"] for m in worker_chat.fake.sent]


# ---- order, and the one case that says "not saved" ----------------------------------------------

def test_queued_replies_keep_their_order(conn, wchat, drain):
    wchat.say("1500 chai")
    wchat.fake.go_down("network")
    assert wchat.tap(T.BTN_CONFIRM) == "posted"
    wchat.say("200 samosa")                                   # a second draft while the first reply waits
    assert len(_replies(conn, "queued")) == 2                  # queued behind it, not sent ahead of it
    wchat.fake.come_back()
    before = len(wchat.fake.sent)
    drain(utcnow() + LATER)
    texts = [m["text"] for m in wchat.fake.sent[before:] if m["chat_id"] == U.USER_A["id"]]
    assert T.REPLY_SAVED in texts[0] and T.REPLY_RECEIVED in texts[1]
    assert "₹200" in texts[1]                                  # the open draft card, with its buttons
    assert wchat.card["message_id"] == wchat.fake.sent[-1]["message_id"]
    assert wchat.tap(T.BTN_CONFIRM) == "posted"               # and it works
    assert _count(conn, "SELECT count(*) AS n FROM field.money_txn") == 2


def test_failure_before_the_commit_still_says_not_saved(conn, wchat, monkeypatch):
    wchat.say("1500 chai")
    card = wchat.card
    monkeypatch.setattr(ledger, "create_expense", lambda *a, **kw: 1 / 0)
    assert wchat.tap(T.BTN_CONFIRM, card=card) == "error"
    assert wchat.fake.sent[-1]["text"] == T.FAILED_NOT_SAVED
    assert _count(conn, "SELECT count(*) AS n FROM field.money_txn") == 0 and not _replies(conn)
