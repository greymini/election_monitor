""""मेरा हिसाब": balance from the single-source formula, in transit, last five entries."""

from pulse.bot import texts_hi as T
from pulse.bot.ui import CB_MENU_ME
from tests.helpers import bucket_id, link, raw_txn
from tests.test_flow_expense import Chat


def _post(chat, text):
    chat.say(text)
    assert chat.tap(T.BTN_CONFIRM) == "posted"


def _open_me(chat):
    chat.say("hello")
    menu = chat.fake.sent[-1]
    assert chat.tap_data(CB_MENU_ME, menu["message_id"]) == "me"
    return chat.fake.last


def test_worker_sees_balance_and_last_five(pool, bot, team):
    link(pool, bot, team["worker-a1"])
    chat = Chat(pool, bot)
    for i in range(6):
        _post(chat, f"{100 * (i + 1)} chai")
    chat.say("999 petrol")
    chat.tap(T.BTN_CONFIRM)
    chat.tap(T.BTN_UNDO)                                   # void: excluded everywhere
    shown = _open_me(chat)
    lines = shown["text"].splitlines()
    assert lines[0] == T.ME_TITLE
    assert lines[1] == T.BALANCE_OWED.format(amount="₹2,100")     # 100+...+600 spent from own pocket so far
    assert T.ME_RECENT in lines
    entries = lines[lines.index(T.ME_RECENT) + 1:]
    assert len(entries) == 5
    assert entries[0].startswith("32-E-000006 · ") and entries[0].endswith("₹600 · भोजन और नाश्ता")
    assert not any("32-E-000007" in e for e in entries)
    rows = [[b["text"] for b in row] for row in shown["reply_markup"]["inline_keyboard"]]
    assert rows == [[T.BTN_RETURN, T.BTN_LAST_MONTH, T.BTN_PIN_SET], [T.BTN_EXPENSE, T.BTN_REPORT, T.BTN_ME]]


def test_no_entries_yet(pool, bot, team):
    link(pool, bot, team["worker-b1"])
    shown = _open_me(Chat(pool, bot))
    assert T.BALANCE_HELD.format(amount="₹0") in shown["text"]
    assert T.ME_NONE in shown["text"]


def test_principal_sees_no_balance(conn, pool, bot, team):
    link(pool, bot, team["principal"])
    raw_txn(conn, kind="advance", amount="5000", from_member_id=team["principal"], to_member_id=team["incharge-a"],
            status="pending_ack", created_by=team["principal"])
    shown = _open_me(Chat(pool, bot))
    assert "शेष" not in shown["text"] and "मिलना" not in shown["text"]
    assert T.ME_IN_TRANSIT.format(amount="₹5,000") in shown["text"]
    assert T.XFER_LABELS[("advance", "out")].format(name="डेमो प्रभारी क") + T.STATUS_SUFFIX["pending_ack"] in shown["text"]
    assert [b["text"] for b in shown["reply_markup"]["inline_keyboard"][0]] == [T.BTN_LAST_MONTH, T.BTN_PIN_SET]   # no return for the principal


def test_received_advance_shows_as_held(conn, pool, bot, team):
    link(pool, bot, team["incharge-a"])
    raw_txn(conn, kind="advance", amount="10000", from_member_id=team["principal"], to_member_id=team["incharge-a"],
            created_by=team["principal"])
    raw_txn(conn, kind="expense", amount="2500", from_member_id=team["incharge-a"],
            bucket_id=bucket_id(conn, "TRAVEL.FUEL"), created_by=team["incharge-a"])
    shown = _open_me(Chat(pool, bot))
    assert T.BALANCE_HELD.format(amount="₹7,500") in shown["text"]
    assert "यात्रा और वाहन › ईंधन" in shown["text"]


def test_finance_scope_none_sees_no_ledger(conn, pool, bot, team):
    conn.execute("UPDATE field.member SET finance_scope = 'none' WHERE member_id = %s", (team["worker-b1"],))
    link(pool, bot, team["worker-b1"])
    chat = Chat(pool, bot)
    chat.say("hello")
    assert chat.tap_data(CB_MENU_ME, chat.fake.sent[-1]["message_id"]) == "no_permission"
    assert chat.fake.last["text"] == T.NO_FINANCE_PERMISSION
