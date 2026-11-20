"""The expense bot flow (LLD §5.1), replayed from recorded update shapes through the dispatcher."""
from __future__ import annotations

import itertools
import threading
from datetime import timedelta
from decimal import Decimal

import pytest

from pulse.bot import flow_expense, texts_hi as T
from pulse.bot.dispatch import dispatch
from pulse.bot.ui import CB_MENU_EXPENSE, CALLBACK_DATA_MAX_BYTES
from pulse.common.clock import utcnow
from pulse.ledger import service as ledger
from tests import updates as U
from tests.helpers import bucket_id, link

_ids = itertools.count(100_000)


class Chat:
    """Drives one Telegram user's private chat with the bot."""

    def __init__(self, pool, bot, user=U.USER_A, after=None):
        """after: called after every update, e.g. to run the worker's queue like a live deployment."""
        self.pool, self.bot, self.user, self.fake, self.after = pool, bot, user, bot.client, after

    def _done(self, outcome):
        if self.after is not None:
            self.after()
        return outcome

    def say(self, text, now=None):
        return self._done(dispatch(self.pool, self.bot, U.text(next(_ids), text, self.user), now=now))

    @property
    def mine(self) -> list[dict]:
        return self.fake.to(self.user["id"])

    @property
    def card(self) -> dict:
        """The most recent message in this chat that still has buttons."""
        return next(m for m in reversed(self.mine) if m["reply_markup"] and m["reply_markup"]["inline_keyboard"])

    def buttons(self, card=None) -> list[str]:
        card = card or self.card
        return [b["text"] for row in card["reply_markup"]["inline_keyboard"] for b in row]

    def data_for(self, label, card=None) -> str:
        card = card or self.card
        for row in card["reply_markup"]["inline_keyboard"]:
            for b in row:
                if b["text"] == label or b["text"].endswith(" " + label):
                    return b["callback_data"]
        raise AssertionError(f"no button {label!r} in {self.buttons(card)}")

    def tap(self, label, now=None, card=None):
        card = card or self.card
        return self.tap_data(self.data_for(label, card), card["message_id"], now=now)

    def tap_data(self, data, message_id, now=None, update_id=None):
        return self._done(dispatch(self.pool, self.bot,
                                   U.callback(update_id or next(_ids), data, self.user, message_id=message_id), now=now))

    @property
    def text(self) -> str:
        return self.mine[-1]["text"]

    @property
    def toast(self):
        return self.fake.answered[-1]["text"]


@pytest.fixture
def worker(pool, bot, team):
    """worker-a1: one scope (area-a1), finance scope own."""
    link(pool, bot, team["worker-a1"])
    return Chat(pool, bot)


def _txns(conn):
    return conn.execute("SELECT * FROM field.money_txn ORDER BY created_at").fetchall()


# ---- fast path --------------------------------------------------------------------------------

def test_fast_path_one_message_one_tap(conn, worker, team):
    assert worker.say("1500 chai nashta baithak") == "confirm"
    card = worker.card
    assert card["text"].splitlines()[0] == "₹1,500 · भोजन और नाश्ता (सुझाव) · डेमो पंचायत क1 · आज · नकद"
    assert T.LINE_REMARK.format(remark="chai nashta baithak") in card["text"]
    assert worker.buttons() == [T.BTN_CONFIRM, T.BTN_CHANGE_BUCKET, T.BTN_DETAILS, T.BTN_CANCEL]  # one scope: no area change

    assert worker.tap(T.BTN_CONFIRM) == "posted"
    [row] = _txns(conn)
    assert row["txn_no"] == "32-E-000001" and row["amount"] == Decimal("1500.00")
    assert row["bucket_id"] == bucket_id(conn, "FOOD")
    assert row["suggest_source"] == "rule" and row["suggest_conf"] == Decimal("0.667")
    assert (row["block_id"], row["area_id"]) == ("blk-a", "area-a1")
    assert row["remark"] == "chai nashta baithak" and row["created_via"] == "telegram"

    posted = worker.fake.last
    assert posted["message_id"] == card["message_id"]            # edited in place
    assert posted["text"].startswith(T.POSTED.format(txn_no="32-E-000001"))
    assert T.BALANCE_OWED.format(amount="₹1,500") in posted["text"]
    assert worker.buttons(posted) == [T.BTN_UNDO]
    assert worker.fake.answered[-1] == {"callback_query_id": worker.fake.answered[-1]["callback_query_id"], "text": None}


def test_changing_the_suggested_bucket_makes_it_the_users_choice(conn, worker):
    worker.say("1500 chai nashta")
    assert worker.tap(T.BTN_CHANGE_BUCKET) == "pick_bucket"
    assert worker.tap("यात्रा और वाहन") == "confirm"
    assert T.SUGGESTED not in worker.card["text"]
    worker.tap(T.BTN_CONFIRM)
    [row] = _txns(conn)
    assert row["bucket_id"] == bucket_id(conn, "TRAVEL")
    assert row["suggest_source"] == "user" and row["suggest_conf"] is None


# ---- double tap, redelivery, concurrency ------------------------------------------------------

def test_double_tap_creates_one_entry(conn, worker):
    worker.say("1500 chai")
    card = worker.card
    data = worker.data_for(T.BTN_CONFIRM, card)
    assert worker.tap_data(data, card["message_id"]) == "posted"
    assert worker.tap_data(data, card["message_id"]) == "already_posted"
    assert worker.toast == T.ALREADY_POSTED.format(txn_no="32-E-000001")
    assert len(_txns(conn)) == 1


def test_redelivered_confirm_is_ignored(conn, worker):
    worker.say("1500 chai")
    card = worker.card
    data = worker.data_for(T.BTN_CONFIRM, card)
    assert worker.tap_data(data, card["message_id"], update_id=777_001) == "posted"
    assert worker.tap_data(data, card["message_id"], update_id=777_001) == "duplicate"
    assert len(_txns(conn)) == 1


def test_simultaneous_taps_create_one_entry(conn, worker):
    worker.say("1500 chai")
    card = worker.card
    data = worker.data_for(T.BTN_CONFIRM, card)
    outcomes = []
    barrier = threading.Barrier(4)

    def tap():
        barrier.wait()
        outcomes.append(worker.tap_data(data, card["message_id"]))

    threads = [threading.Thread(target=tap) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(outcomes) == ["already_posted"] * 3 + ["posted"]
    assert len(_txns(conn)) == 1


# ---- amount only, more buckets, other ---------------------------------------------------------

def test_amount_only_offers_six_buckets_in_preset_order(worker):
    assert worker.say("1500") == "pick_bucket"
    assert worker.text.startswith(T.PICK_BUCKET.format(amount="₹1,500"))
    assert worker.buttons() == ["सभा और कार्यक्रम", "यात्रा और वाहन", "भोजन और नाश्ता", "कार्यकर्ता खर्च",
                                "प्रचार और मीडिया", "कार्यालय खर्च", T.BTN_MORE_BUCKETS, T.BTN_OTHER, T.BTN_CANCEL]


def test_most_used_buckets_come_first(worker):
    for _ in range(2):
        worker.say("300")
        worker.tap(T.BTN_MORE_BUCKETS)
        worker.tap("संगठन खर्च")
        worker.tap(T.BTN_CONFIRM)
    worker.say("400")
    assert worker.buttons()[0] == "संगठन खर्च"
    assert len([b for b in worker.buttons() if b not in (T.BTN_MORE_BUCKETS, T.BTN_OTHER, T.BTN_CANCEL)]) == 6


def test_more_buckets_shows_all_eleven(worker):
    worker.say("1500")
    assert worker.tap(T.BTN_MORE_BUCKETS) == "pick_bucket_all"
    names = [b for b in worker.buttons() if b not in (T.BTN_OTHER, T.BTN_CANCEL)]
    assert len(names) == 11 and "अन्य" not in names


def test_other_asks_for_a_label_and_counts_it(conn, worker):
    worker.say("700")
    assert worker.tap(T.BTN_OTHER) == "await_label"
    assert worker.text == T.ASK_LABEL.format(amount="₹700")
    assert worker.say("x" * 41) == "label_too_long"
    assert worker.text.startswith(T.LABEL_TOO_LONG)
    assert worker.say("call 9876543210") == "pii_rejected"
    assert worker.say("ghadi marammat") == "confirm"
    assert T.OTHER_LABEL.format(label="ghadi marammat") in worker.card["text"]
    worker.tap(T.BTN_CONFIRM)
    [row] = _txns(conn)
    assert row["bucket_id"] == bucket_id(conn, "OTHER") and row["custom_label"] == "ghadi marammat"
    assert conn.execute("SELECT count FROM field.exp_custom_label").fetchone()["count"] == 1


# ---- details ----------------------------------------------------------------------------------

def test_details_path(conn, worker):
    worker.say("1500 chai")
    assert worker.tap(T.BTN_DETAILS) == flow_expense.S_D_SUBHEAD
    assert worker.tap("चाय-नाश्ता") == flow_expense.S_D_DATE
    assert worker.say("01/01/2099") == "date_future"
    assert worker.say("kal") == "date_invalid"
    assert worker.tap(T.YESTERDAY) == flow_expense.S_D_PAY_MODE
    assert worker.tap("UPI") == flow_expense.S_D_FUND
    assert worker.buttons()[:2] == [T.FUND_SOURCES["advance"], T.FUND_SOURCES["own_pocket_reimbursable"]]
    assert worker.tap(T.FUND_SOURCES["own_pocket_reimbursable"]) == flow_expense.S_D_RECEIPT  # FOOD: no reach step
    assert worker.tap(T.BTN_SKIP) == flow_expense.S_D_REMARK
    assert worker.say("ABC1234567 wale ke liye") == "pii_rejected"
    assert worker.say("baithak ke baad") == "confirm"
    text = worker.card["text"]
    assert "भोजन और नाश्ता › चाय-नाश्ता" in text and T.YESTERDAY in text and "UPI" in text
    worker.tap(T.BTN_CONFIRM)
    [row] = _txns(conn)
    assert row["bucket_id"] == bucket_id(conn, "FOOD.TEA") and row["suggest_source"] == "user"
    assert row["pay_mode"] == "upi" and row["fund_source"] == "own_pocket_reimbursable"
    assert row["remark"] == "baithak ke baad"
    assert (utcnow().date() - row["txn_date"]).days in (0, 1, 2)


def test_details_reach_only_where_ask_reach(conn, worker):
    worker.say("500 ilaj dawai")                              # ASSIST: sensitive, ask_reach
    worker.tap(T.BTN_DETAILS)
    for _ in range(4):                                        # sub-head, date, pay mode, paid from
        worker.tap(T.BTN_SKIP)
    assert "पहुँचा" in worker.text
    assert worker.tap(T.REACH_UNITS["family"]) == flow_expense.S_D_REACH
    assert worker.say("0") == "reach_invalid"
    assert worker.say("३") == flow_expense.S_D_RECEIPT        # Devanagari digit
    assert worker.tap(T.BTN_SKIP) == flow_expense.S_D_REMARK
    assert worker.tap(T.BTN_SKIP) == "confirm"
    assert T.LINE_REACH.format(count=3, unit=T.REACH_UNITS["family"]) in worker.card["text"]
    assert worker.tap(T.BTN_CONFIRM) == "posted"               # sensitive but no election window: no warning
    [row] = _txns(conn)
    assert (row["reach_count"], row["reach_unit"]) == (3, "family")


def test_typed_date_without_year(conn, worker):
    worker.say("1500 chai")
    worker.tap(T.BTN_DETAILS)
    worker.tap(T.BTN_SKIP)
    today = utcnow().date()
    past = today - timedelta(days=3)
    assert worker.say(past.strftime("%d/%m")) == flow_expense.S_D_PAY_MODE


# ---- area -------------------------------------------------------------------------------------

def test_area_asked_then_remembered(conn, pool, bot, team):
    link(pool, bot, team["worker-a2"], U.USER_B)          # scopes area-a2 and area-a3
    chat = Chat(pool, bot, U.USER_B)
    assert chat.say("500 chai") == "pick_area"
    assert chat.buttons() == ["डेमो पंचायत क2", "डेमो पंचायत क3", T.BTN_CANCEL]
    assert chat.tap("डेमो पंचायत क3") == "confirm"
    assert T.BTN_CHANGE_AREA in chat.buttons()
    chat.tap(T.BTN_CONFIRM)
    assert chat.say("600 chai") == "confirm"               # last used
    assert "डेमो पंचायत क3" in chat.card["text"]
    chat.tap(T.BTN_CHANGE_AREA)
    chat.tap("डेमो पंचायत क2")
    chat.tap(T.BTN_CONFIRM)
    assert [r["area_id"] for r in _txns(conn)] == ["area-a3", "area-a2"]


def test_block_only_scope_sets_block(conn, pool, bot, team):
    link(pool, bot, team["incharge-a"], U.USER_B)          # scope: whole of blk-a
    chat = Chat(pool, bot, U.USER_B)
    assert chat.say("800 petrol") == "confirm"
    assert "डेमो प्रखंड क" in chat.card["text"]
    chat.tap(T.BTN_CONFIRM)
    [row] = _txns(conn)
    assert (row["block_id"], row["area_id"]) == ("blk-a", None)


def test_principal_posts_without_balance_line(conn, pool, bot, team):
    link(pool, bot, team["principal"], U.USER_B)
    chat = Chat(pool, bot, U.USER_B)
    assert chat.say("2000 tent") == "pick_area"            # three block scopes
    chat.tap("डेमो प्रखंड ख")
    chat.tap(T.BTN_CONFIRM)
    assert "शेष" not in chat.text and "मिलना" not in chat.text
    [row] = _txns(conn)
    assert row["fund_source"] == "own"


# ---- warnings and big amounts -----------------------------------------------------------------

def test_large_amount_warns_with_one_extra_tap(conn, worker):
    worker.say("30000 tent")
    assert worker.tap(T.BTN_CONFIRM) == "warn"
    assert worker.text.startswith(T.WARN_HEADER)
    assert T.WARNINGS["large"].format(limit="₹25,000") in worker.text
    assert _txns(conn) == []
    assert worker.tap(T.BTN_BACK) == "confirm"
    worker.tap(T.BTN_CONFIRM)
    assert worker.tap(T.BTN_POST_ANYWAY) == "posted"
    [row] = _txns(conn)
    assert row["flagged"] == ["large"]


def test_window_warnings(conn, worker):
    conn.execute("""INSERT INTO field.ac_election_window (ac_id, starts_on, ends_on, cash_limit)
                    VALUES ('ac-32', current_date - 30, current_date + 30, 10000)""")
    worker.say("12000 madad")
    worker.tap(T.BTN_CONFIRM)
    assert T.WARNINGS["cash_over_limit"].format(limit="₹10,000") in worker.text
    assert T.WARNINGS["sensitive_in_window"] in worker.text
    worker.tap(T.BTN_POST_ANYWAY)
    [row] = _txns(conn)
    assert row["flagged"] == ["cash_over_limit", "sensitive_in_window"] and row["in_election_window"]


def test_amount_above_ten_lakh_is_confirmed_first(conn, worker):
    assert worker.say("15 lakh tent") == "confirm_amount"
    assert worker.text == T.CONFIRM_BIG_AMOUNT.format(amount="₹15,00,000")
    assert worker.tap(T.BTN_YES_CORRECT) == "confirm"
    assert worker.card["text"].startswith("₹15,00,000 · सभा और कार्यक्रम")


# ---- cancel, expiry, stale cards --------------------------------------------------------------

@pytest.mark.parametrize("setup", [
    lambda c: c.say("1500 chai"),                                    # confirm card
    lambda c: c.say("1500"),                                         # bucket picker
    lambda c: (c.say("1500"), c.tap(T.BTN_OTHER)),                   # label prompt
    lambda c: (c.say("1500 chai"), c.tap(T.BTN_DETAILS)),            # details
    lambda c: c.say("15 lakh"),                                      # big amount confirm
    lambda c: (c.say("30000 tent"), c.tap(T.BTN_CONFIRM)),           # warning
])
def test_cancel_works_in_every_state(conn, worker, setup):
    setup(worker)
    assert worker.tap(T.BTN_CANCEL) == "cancelled"
    assert worker.text == T.CANCELLED
    assert _txns(conn) == []
    assert conn.execute("SELECT count(*) AS n FROM field.tg_session").fetchone()["n"] == 0


def test_typed_cancel(conn, worker):
    worker.say("1500")
    worker.tap(T.BTN_OTHER)
    assert worker.say("रद्द") == "cancelled"
    assert _txns(conn) == []


def test_expired_card_tap(conn, worker):
    t0 = utcnow()
    worker.say("1500 chai", now=t0)
    assert worker.tap(T.BTN_CONFIRM, now=t0 + timedelta(minutes=16)) == "card_expired"
    assert worker.toast == T.CARD_EXPIRED
    assert _txns(conn) == []


def test_timeout_discards_draft_and_tells_user_on_next_message(conn, worker):
    t0 = utcnow()
    worker.say("1500 chai", now=t0)
    old_card = worker.card["message_id"]
    assert worker.say("namaste", now=t0 + timedelta(minutes=15)) == "menu"
    texts = [m["text"] for m in worker.fake.sent[-2:]]
    assert texts[0] == T.DRAFT_EXPIRED.format(amount="₹1,500")
    assert old_card in worker.fake.stripped
    assert _txns(conn) == []
    assert conn.execute("SELECT count(*) AS n FROM field.tg_session").fetchone()["n"] == 0


def test_session_still_alive_at_fourteen_minutes(worker):
    t0 = utcnow()
    worker.say("1500 chai", now=t0)
    assert worker.tap(T.BTN_CONFIRM, now=t0 + timedelta(minutes=14)) == "posted"


def test_old_card_version_is_stale(conn, worker):
    worker.say("1500 chai")
    old = worker.card
    old_ok = worker.data_for(T.BTN_CONFIRM, old)
    worker.tap(T.BTN_CHANGE_BUCKET)                                  # card moves to v2
    assert worker.tap_data(old_ok, old["message_id"]) == "card_stale"
    assert worker.toast == T.CARD_STALE
    assert _txns(conn) == []


def test_card_from_a_replaced_draft_is_stale(conn, worker):
    worker.say("1500 chai")
    first = worker.card
    assert worker.say("200 samosa") == "confirm"
    assert worker.text.startswith(T.DRAFT_REPLACED.format(amount="₹1,500"))
    assert first["message_id"] in worker.fake.stripped
    assert worker.tap(T.BTN_CONFIRM, card=first) == "card_stale"
    worker.tap(T.BTN_CONFIRM)
    [row] = _txns(conn)
    assert row["amount"] == Decimal("200.00")


def test_typing_while_buttons_are_expected_reshows_the_card(worker):
    worker.say("1500")
    assert worker.say("kya karun") == "use_buttons"
    assert worker.text.startswith(T.USE_BUTTONS)
    assert T.BTN_MORE_BUCKETS in worker.buttons()


# ---- menu entry, errors, permissions ----------------------------------------------------------

def test_menu_expense_button_prompts_for_amount(worker):
    worker.say("hello")                                              # shows the menu
    menu = worker.fake.sent[-1]
    assert worker.tap_data(CB_MENU_EXPENSE, menu["message_id"]) == "await_amount"
    assert worker.fake.last == {**worker.fake.last, "message_id": menu["message_id"], "text": T.ASK_AMOUNT}
    assert worker.say("namaste") == "amount_not_understood"
    assert worker.text.startswith(T.AMOUNT_NOT_UNDERSTOOD)
    assert worker.say("0") == "amount_error"
    assert worker.say("1500 chai") == "confirm"


def test_amount_errors_get_hindi_replies(worker):
    assert worker.say("0 chai") == "amount_error"
    assert worker.text == T.AMOUNT_ERRORS["zero"]
    assert worker.say("-500 chai") == "amount_error"
    assert worker.text == T.AMOUNT_ERRORS["negative"]


def test_pii_in_description_is_rejected(conn, worker):
    assert worker.say("500 madad 9876543210 wale ko") == "pii_rejected"
    assert worker.text == T.PII_REJECTED.format(what=T.PII_KINDS["mobile"])
    assert conn.execute("SELECT count(*) AS n FROM field.tg_session").fetchone()["n"] == 0


def test_finance_scope_none_cannot_enter_expenses(conn, pool, bot, team):
    conn.execute("UPDATE field.member SET finance_scope = 'none' WHERE member_id = %s", (team["worker-b1"],))
    link(pool, bot, team["worker-b1"], U.USER_B)
    chat = Chat(pool, bot, U.USER_B)
    assert chat.say("1500 chai") == "no_permission"
    assert chat.text == T.NO_FINANCE_PERMISSION


def test_failure_tells_user_it_was_not_saved(conn, worker, monkeypatch):
    worker.say("1500 chai")
    card = worker.card

    def boom(*a, **kw):
        raise RuntimeError("database exploded")

    monkeypatch.setattr(ledger, "create_expense", boom)
    assert worker.tap(T.BTN_CONFIRM, card=card) == "error"
    assert worker.fake.sent[-1]["text"] == T.FAILED_NOT_SAVED
    assert worker.toast == T.FAILED_NOT_SAVED
    assert _txns(conn) == []
    monkeypatch.undo()
    assert worker.tap(T.BTN_CONFIRM, card=card) == "posted"          # state rolled back: retry works


def test_failure_on_a_text_message_is_reported(worker, monkeypatch):
    monkeypatch.setattr(flow_expense, "start_draft", lambda *a, **kw: 1 / 0)
    assert worker.say("1500 chai") == "error"
    assert worker.fake.sent[-1]["text"] == T.FAILED_NOT_SAVED


def test_card_that_cannot_be_edited_is_sent_again(conn, worker):
    worker.say("1500 chai")
    worker.fake.fail_edit = True
    assert worker.tap(T.BTN_CONFIRM) == "posted"
    assert worker.fake.sent[-1]["text"].startswith(T.POSTED.format(txn_no="32-E-000001"))


# ---- undo -------------------------------------------------------------------------------------

def test_undo_within_ten_minutes(conn, worker):
    worker.say("1500 chai")
    worker.tap(T.BTN_CONFIRM)
    posted = worker.card
    assert worker.tap(T.BTN_UNDO) == "undone"
    assert worker.toast == T.UNDONE_TOAST
    assert worker.fake.last["text"] == T.UNDONE.format(txn_no="32-E-000001")
    [row] = _txns(conn)
    assert row["status"] == "void" and row["void_reason"] == "undo"
    assert worker.tap(T.BTN_UNDO, card=posted) == "already_undone"


def test_undo_after_ten_minutes_is_refused(conn, worker):
    t0 = utcnow()
    worker.say("1500 chai", now=t0)
    worker.tap(T.BTN_CONFIRM, now=t0)
    assert worker.tap(T.BTN_UNDO, now=t0 + timedelta(minutes=11)) == "undo_expired"
    assert worker.toast == T.UNDO_EXPIRED.format(minutes=10)
    assert _txns(conn)[0]["status"] == "posted"


def test_someone_elses_undo_button_does_nothing(conn, pool, bot, worker, team):
    worker.say("1500 chai")
    worker.tap(T.BTN_CONFIRM)
    data = worker.data_for(T.BTN_UNDO)
    link(pool, bot, team["worker-a2"], U.USER_B)
    other = Chat(pool, bot, U.USER_B)
    assert other.tap_data(data, 1) == "card_stale"
    assert _txns(conn)[0]["status"] == "posted"


# ---- protocol hygiene -------------------------------------------------------------------------

def test_every_callback_answered_once_and_data_within_limit(worker, pool, bot, team):
    worker.say("1500")
    worker.tap(T.BTN_MORE_BUCKETS)
    worker.tap("भोजन और नाश्ता")
    worker.tap(T.BTN_DETAILS)
    for _ in range(6):                                       # sub-head, date, pay mode, paid from, receipt, remark
        worker.tap(T.BTN_SKIP)
    worker.tap(T.BTN_CONFIRM)
    worker.tap(T.BTN_UNDO)
    worker.tap(T.BTN_UNDO, card=worker.fake.cards[-2] if worker.fake.cards[-2]["reply_markup"] else worker.card)
    callback_ids = [a["callback_query_id"] for a in worker.fake.answered]
    assert len(callback_ids) == len(set(callback_ids)) == 12
    for m in worker.fake.cards:
        for row in (m["reply_markup"] or {}).get("inline_keyboard", []):
            for b in row:
                assert len(b["callback_data"].encode("utf-8")) <= CALLBACK_DATA_MAX_BYTES


def test_parse_date():
    from datetime import date

    today = date(2026, 11, 19)
    assert flow_expense.parse_date("15/11", today) == date(2026, 11, 15)
    assert flow_expense.parse_date("28/12", today) == date(2025, 12, 28)     # no year, would be future: last year
    assert flow_expense.parse_date("१५-११-२६", today) == date(2026, 11, 15)
    assert flow_expense.parse_date("15.11.2026", today) == date(2026, 11, 15)
    assert flow_expense.parse_date("01/01/2099", today) == date(2099, 1, 1)   # explicit year kept; caller refuses future
    assert flow_expense.parse_date("31/02", today) is None
    assert flow_expense.parse_date("kal", today) is None


def test_pin_lock_after_the_draft_opened_stops_the_post(conn, worker, team):
    """A lock stops every bot money action, below the PIN limit too, even with the draft already open."""
    worker.say("1500 chai")
    card = worker.card
    conn.execute("UPDATE field.member SET pin_locked_at = now() WHERE member_id = %s", (team["worker-a1"],))
    assert worker.tap(T.BTN_CONFIRM, card=card) == "pin_locked"
    assert worker.text == T.PIN_LOCKED_MONEY
    assert _txns(conn) == []
