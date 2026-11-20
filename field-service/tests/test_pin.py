"""PIN for large money entries in the bot (slice 2c): set, change, required at the limit, three wrong
entries lock, admin reset and unlock, prompt expiry, and the PIN never kept, echoed or logged."""
import logging
from datetime import timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from pulse.api.main import create_app
from pulse.bot import texts_hi as T
from pulse.bot.dispatch import BotDeps
from pulse.bot.flow_pin import CB_SET
from pulse.common.clock import utcnow
from pulse.config import LedgerLimits
from pulse.team import pin as pins
from tests import updates as U
from tests.helpers import link
from tests.test_flow_expense import Chat

PIN = "583914"          # six digits that do not occur in ids, hashes or seed data
OTHER = "730261"
WRONG = "461975"


@pytest.fixture(autouse=True)
def fast_bcrypt(monkeypatch):
    monkeypatch.setattr(pins, "ROUNDS", 4)


@pytest.fixture
def pinbot(fake_tg, core, report_limits) -> BotDeps:
    return BotDeps(client=fake_tg, core=core, limits=LedgerLimits(), reports=report_limits)   # PIN limit ₹50,000


@pytest.fixture
def worker_chat(pool, pinbot, team):
    link(pool, pinbot, team["worker-a1"])
    return Chat(pool, pinbot)


def _set_pin(chat, pin=PIN):
    assert chat.tap_data(CB_SET, 1) == "pin_ask_new"
    assert chat.say(pin) == "pin_ask_confirm"
    assert chat.say(pin) == "pin_saved"


def _post(chat, now=None):
    """Tap through the expense card: confirm, then past the large-entry warning."""
    out = chat.tap(T.BTN_CONFIRM, now=now)
    return chat.tap(T.BTN_POST_ANYWAY, now=now) if out == "warn" else out


def _retap(chat, now=None):
    """The draft is still open after a PIN step: tap its card again."""
    card = next(m for m in reversed(chat.mine) if m["reply_markup"] and T.BTN_POST_ANYWAY in chat.buttons(m))
    return chat.tap(T.BTN_POST_ANYWAY, now=now, card=card)


def _txns(conn):
    return conn.execute("SELECT count(*) AS n FROM field.money_txn").fetchone()["n"]


def _audit(conn):
    return [r["action"] for r in conn.execute("SELECT action FROM field.admin_audit ORDER BY audit_id")]


def _last_message_id(chat):
    return chat.last_update_message_id


@pytest.fixture(autouse=True)
def _track_message_ids(monkeypatch):
    """Remember the message_id of the last text sent, to check it was deleted."""
    orig = U.text

    def text(update_id, body, user=U.USER_A):
        u = orig(update_id, body, user)
        Chat.last_update_message_id = u["message"]["message_id"]
        return u
    monkeypatch.setattr(U, "text", text)


def test_set_pin_from_menu_deletes_each_entry(conn, worker_chat, fake_tg, team):
    assert worker_chat.tap_data(CB_SET, 1) == "pin_ask_new"
    assert worker_chat.say(PIN) == "pin_ask_confirm"
    first = _last_message_id(worker_chat)
    assert worker_chat.say(PIN) == "pin_saved"
    second = _last_message_id(worker_chat)
    assert (U.USER_A["id"], first) in fake_tg.deleted and (U.USER_A["id"], second) in fake_tg.deleted
    row = conn.execute("SELECT * FROM field.tg_link WHERE member_id = %s", (team["worker-a1"],)).fetchone()
    assert row["pin_hash"].startswith("$2") and row["pin_set_at"] is not None
    assert row["pin_stage"] is None and row["pin_pending_hash"] is None
    assert _audit(conn) == ["pin_set"]
    assert fake_tg.sent[-1]["text"] == T.PIN_SAVED


@pytest.mark.parametrize("weak", ["1111", "1234", "98765", "12a4", "123"])
def test_weak_pins_refused(worker_chat, weak):
    worker_chat.tap_data(CB_SET, 1)
    assert worker_chat.say(weak) == "pin_weak"


def test_confirm_mismatch_saves_nothing(conn, worker_chat, team):
    worker_chat.tap_data(CB_SET, 1)
    worker_chat.say(PIN)
    assert worker_chat.say(OTHER) == "pin_mismatch"
    assert not pins.has_pin(conn, team["worker-a1"])


def test_change_pin_needs_current(conn, worker_chat, team):
    _set_pin(worker_chat)
    assert worker_chat.tap_data(CB_SET, 1) == "pin_ask_current"
    assert worker_chat.say(WRONG) == "pin_wrong"
    assert worker_chat.say(PIN) == "pin_ask_new"
    assert worker_chat.say(OTHER) == "pin_ask_confirm"
    assert worker_chat.say(OTHER) == "pin_saved"
    assert _audit(conn) == ["pin_set", "pin_changed"]
    link_row = conn.execute("SELECT pin_hash FROM field.tg_link WHERE member_id = %s", (team["worker-a1"],)).fetchone()
    assert pins._matches(OTHER, link_row["pin_hash"]) and not pins._matches(PIN, link_row["pin_hash"])


def test_below_limit_posts_without_pin(conn, worker_chat):
    assert worker_chat.say("49999 petrol bolero") == "confirm"
    assert _post(worker_chat) == "posted"
    assert _txns(conn) == 1


def test_required_at_limit_then_posts(conn, worker_chat, fake_tg):
    _set_pin(worker_chat)
    assert worker_chat.say("50000 petrol bolero") == "confirm"
    assert _post(worker_chat) == "pin_asked"
    assert _txns(conn) == 0
    assert worker_chat.say(PIN) == "posted"
    assert (U.USER_A["id"], _last_message_id(worker_chat)) in fake_tg.deleted
    assert _txns(conn) == 1


def test_advance_at_limit_needs_pin(conn, pool, pinbot, team):
    link(pool, pinbot, team["principal"])
    chat = Chat(pool, pinbot)
    _set_pin(chat)
    assert chat.say("दिया 60000 प्रभारी क") == "name_matched"
    assert chat.tap(T.BTN_RECORD) == "pin_asked"
    assert _txns(conn) == 0
    assert chat.say(PIN) == "posted"
    assert _txns(conn) == 1


def test_no_pin_yet_sets_one_first(conn, worker_chat, fake_tg):
    worker_chat.say("75000 petrol bolero")
    assert _post(worker_chat) == "pin_needed_first"
    assert _txns(conn) == 0
    assert T.BTN_PIN_SET in worker_chat.buttons()
    _set_pin(worker_chat)
    assert fake_tg.sent[-1]["text"] == T.PIN_SAVED_RETRY          # the draft is still there
    assert _retap(worker_chat) == "pin_asked"
    assert worker_chat.say(PIN) == "posted"


def test_three_wrong_locks_then_admin_reset(conn, worker_chat, team, settings, pool, core, fake_tg):
    _set_pin(worker_chat)
    worker_chat.say("60000 petrol bolero")
    assert _post(worker_chat) == "pin_asked"
    assert worker_chat.say(WRONG) == "pin_wrong"
    assert worker_chat.say(WRONG) == "pin_wrong"
    assert worker_chat.say(WRONG) == "pin_locked"
    assert _txns(conn) == 0
    assert pins.is_locked(conn, team["worker-a1"])
    assert _audit(conn) == ["pin_set", "pin_locked"]

    # locked: no bot money action at all, even a small one
    worker_chat.say("500 chai nashta")
    assert fake_tg.sent[-1]["text"] == T.PIN_LOCKED_MONEY
    assert _txns(conn) == 0

    app = create_app(settings, pool=pool, core=core, telegram_client=fake_tg)
    with TestClient(app) as api:
        rep = {"Authorization": f"Bearer {core.issue_token('usr-worker-a1')}"}
        admin = {"Authorization": f"Bearer {core.issue_token('usr-principal')}"}
        url = f"/acs/32/field/members/{team['worker-a1']}/pin"
        assert api.post(url + "/reset", headers=rep).status_code == 403
        assert api.post(url + "/reset", headers=admin).status_code == 204
    assert not pins.is_locked(conn, team["worker-a1"]) and not pins.has_pin(conn, team["worker-a1"])
    audit = conn.execute("SELECT action, actor_user_id FROM field.admin_audit ORDER BY audit_id").fetchall()
    assert audit[-1] == {"action": "pin_reset", "actor_user_id": "usr-principal"}


def test_unlock_keeps_pin(conn, worker_chat, team, settings, pool, core, fake_tg):
    _set_pin(worker_chat)
    worker_chat.tap_data(CB_SET, 1)
    for _ in range(3):
        worker_chat.say(WRONG)
    assert pins.is_locked(conn, team["worker-a1"])
    app = create_app(settings, pool=pool, core=core, telegram_client=fake_tg)
    with TestClient(app) as api:
        api.headers["Authorization"] = f"Bearer {core.issue_token('usr-principal')}"
        assert api.post(f"/acs/32/field/members/{team['worker-a1']}/pin/unlock").status_code == 204
    assert not pins.is_locked(conn, team["worker-a1"]) and pins.has_pin(conn, team["worker-a1"])
    assert _audit(conn)[-1] == "pin_unlocked"


def test_prompt_expires_after_two_minutes(conn, worker_chat, fake_tg):
    _set_pin(worker_chat)
    t0 = utcnow()
    worker_chat.say("60000 petrol bolero", now=t0)
    assert _post(worker_chat, now=t0) == "pin_asked"
    # a PIN typed late is swallowed and deleted, never read as an amount
    assert worker_chat.say(PIN, now=t0 + timedelta(minutes=3)) == "pin_expired"
    assert (U.USER_A["id"], _last_message_id(worker_chat)) in fake_tg.deleted
    assert fake_tg.sent[-1]["text"] == T.PIN_EXPIRED_ENTRY            # tells them to send the amount again
    assert _txns(conn) == 0
    # the step is gone: tapping record asks again with a fresh two minutes
    t1 = t0 + timedelta(minutes=4)
    assert _retap(worker_chat, now=t1) == "pin_asked"
    assert worker_chat.say(PIN, now=t1 + timedelta(seconds=90)) == "posted"


def test_late_window_ends(worker_chat):
    _set_pin(worker_chat)
    t0 = utcnow()
    worker_chat.tap_data(CB_SET, 1, now=t0)                       # change: asks for the current PIN
    assert worker_chat.say("4500 petrol", now=t0 + timedelta(minutes=11)) != "pin_expired"


def test_pin_never_in_clear(conn, pool, pinbot, team, worker_chat, fake_tg, caplog):
    caplog.set_level(logging.DEBUG)
    _set_pin(worker_chat)
    worker_chat.say("60000 petrol bolero")
    _post(worker_chat)
    worker_chat.say(WRONG)
    assert worker_chat.say(PIN) == "posted"
    worker_chat.tap_data(CB_SET, 1)
    worker_chat.say(PIN)
    worker_chat.say(OTHER)
    worker_chat.say(OTHER)

    for secret in (PIN, OTHER, WRONG):
        assert not any(secret in r.getMessage() for r in caplog.records), "PIN in a log line"
        assert not any(secret in (m.get("text") or "") for m in fake_tg.sent + fake_tg.edits), "PIN echoed"
        tables = [r["t"] for r in conn.execute(
            "SELECT table_name AS t FROM information_schema.tables WHERE table_schema = 'field'")]
        for t in tables:
            hit = conn.execute(f"SELECT count(*) AS n FROM field.{t} x WHERE row_to_json(x)::text LIKE %s",
                               (f"%{secret}%",)).fetchone()["n"]
            assert hit == 0, f"PIN in clear in field.{t}"
    assert pins.has_pin(conn, team["worker-a1"])


def test_pin_limit_follows_ac_setting(conn, worker_chat):
    from pulse import settings_ac

    settings_ac.change(conn, "ac-32", {"pin_limit": "5000"}, actor_user_id="usr-principal")
    worker_chat.say("6000 petrol bolero")
    assert _post(worker_chat) == "pin_needed_first"
    assert Decimal("5000") == settings_ac.effective(conn, "ac-32").ledger.pin_limit


def test_late_pin_while_setting_says_start_setup_again(worker_chat, fake_tg):
    t0 = utcnow()
    worker_chat.tap_data(CB_SET, 1, now=t0)
    assert worker_chat.say(PIN, now=t0 + timedelta(minutes=3)) == "pin_expired"
    assert fake_tg.sent[-1]["text"] == T.PIN_EXPIRED_SETUP
