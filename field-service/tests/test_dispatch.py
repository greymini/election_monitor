"""Recorded updates replayed through the dispatcher with a fake Telegram client."""
from datetime import timedelta

from pulse.bot import flow_menu, texts_hi
from pulse.bot.dispatch import dispatch
from pulse.team import linking, service
from pulse.bot import texts_hi as T
from tests import updates as U

AC = "ac-32"


def _link(pool, bot, fake_tg, member_id, user=U.USER_A, update_id=5000):
    with pool.connection() as conn, conn.transaction():
        issued = linking.create_link_token(conn, AC, member_id, ttl=timedelta(hours=24))
    assert dispatch(pool, bot, U.start(update_id, issued.token, user)) == "linked"
    fake_tg.sent.clear()


def _buttons(msg):
    return [b["text"] for row in msg["reply_markup"]["inline_keyboard"] for b in row]


def test_successful_link(pool, conn, bot, fake_tg, team):
    with pool.connection() as c, c.transaction():
        issued = linking.create_link_token(c, AC, team["worker-a1"], ttl=timedelta(hours=24))
    assert dispatch(pool, bot, U.start(2001, issued.token)) == "linked"

    link = conn.execute("SELECT * FROM field.tg_link").fetchone()
    assert link["member_id"] == team["worker-a1"]
    assert link["telegram_user_id"] == U.USER_A["id"] and link["chat_id"] == U.USER_A["id"]
    assert link["revoked_at"] is None and link["pin_hash"] is None
    [msg] = fake_tg.sent
    assert msg["text"].startswith(texts_hi.WELCOME.format(name="डेमो कार्यकर्ता क1"))
    assert _buttons(msg) == [texts_hi.BTN_EXPENSE, texts_hi.BTN_REPORT, texts_hi.BTN_ME]
    assert service.get_member(conn, AC, team["worker-a1"])["telegram_linked_at"] is not None


def test_duplicate_update_id_is_ignored(pool, conn, bot, fake_tg, team):
    _link(pool, bot, fake_tg, team["worker-a1"])
    update = U.text(2101, "नमस्ते")
    assert dispatch(pool, bot, update) == "menu"
    assert dispatch(pool, bot, update) == "duplicate"
    assert dispatch(pool, bot, update) == "duplicate"
    assert len(fake_tg.sent) == 1
    row = conn.execute("SELECT outcome FROM field.tg_update WHERE update_id = 2101").fetchone()
    assert row["outcome"] == "menu"


def test_unlinked_user_gets_fixed_reply_and_nothing_stored(pool, conn, bot, fake_tg, team):
    assert dispatch(pool, bot, U.text(2201, "1500 chai nashta")) == "unlinked"
    assert dispatch(pool, bot, U.photo(2202)) == "unlinked"
    assert dispatch(pool, bot, U.start(2203, None)) == "unlinked"
    assert fake_tg.texts == [texts_hi.UNREGISTERED] * 3
    assert all(m["reply_markup"] is None for m in fake_tg.sent)
    # only the update ids (and outcome) are recorded; no session, no link
    assert conn.execute("SELECT count(*) AS n FROM field.tg_update").fetchone()["n"] == 3
    assert conn.execute("SELECT count(*) AS n FROM field.tg_session").fetchone()["n"] == 0
    assert conn.execute("SELECT count(*) AS n FROM field.tg_link").fetchone()["n"] == 0


def test_unlinked_user_tapping_old_button(pool, bot, fake_tg, team):
    assert dispatch(pool, bot, U.callback(2251, flow_menu.CB_EXPENSE)) == "unlinked"
    assert fake_tg.answered == [{"callback_query_id": "cbq-2251", "text": None}]
    assert fake_tg.texts == [texts_hi.UNREGISTERED]


def test_group_chat_is_ignored(pool, conn, bot, fake_tg, team):
    _link(pool, bot, fake_tg, team["worker-a1"])  # even a linked member is ignored in a group
    with pool.connection() as c, c.transaction():
        issued = linking.create_link_token(c, AC, team["worker-a2"], ttl=timedelta(hours=24))
    assert dispatch(pool, bot, U.group_text(2301, "नमस्ते")) == "ignored_not_private"
    assert dispatch(pool, bot, U.group_text(2302, f"/start {issued.token}", U.USER_B)) == "ignored_not_private"
    assert dispatch(pool, bot, U.callback(2303, flow_menu.CB_ME, chat=U.GROUP_CHAT)) == "ignored_not_private"
    assert dispatch(pool, bot, U.channel_post(2304)) == "ignored_not_private"
    assert fake_tg.sent == [] and fake_tg.answered == []
    # the token posted in the group was not consumed
    assert conn.execute("SELECT used_at FROM field.tg_link_token WHERE member_id = %s", (team["worker-a2"],)).fetchone()["used_at"] is None


def test_linked_user_gets_menu_and_report_says_coming_soon(pool, bot, fake_tg, team):
    _link(pool, bot, fake_tg, team["worker-a1"])
    assert dispatch(pool, bot, U.text(2401, "कुछ भी")) == "menu"
    assert _buttons(fake_tg.sent[-1]) == [texts_hi.BTN_EXPENSE, texts_hi.BTN_REPORT, texts_hi.BTN_ME]
    assert dispatch(pool, bot, U.callback(2410, flow_menu.CB_REPORT)) == "coming_soon"
    assert fake_tg.texts[-1] == texts_hi.COMING_SOON == "जल्द आ रहा है"
    assert [a["callback_query_id"] for a in fake_tg.answered] == ["cbq-2410"]


def test_unlink_takes_effect_on_next_update(pool, bot, fake_tg, team):
    _link(pool, bot, fake_tg, team["worker-a1"])
    assert dispatch(pool, bot, U.text(2501, "हाँ")) == "menu"
    with pool.connection() as conn, conn.transaction():
        assert linking.unlink(conn, AC, team["worker-a1"]) is True
    assert dispatch(pool, bot, U.text(2502, "हाँ")) == "unlinked"


def test_deactivation_revokes_link(pool, core, bot, fake_tg, team):
    _link(pool, bot, fake_tg, team["worker-a1"])
    with pool.connection() as conn, conn.transaction():
        service.update_member(conn, core, AC, team["worker-a1"], {"active": False})
    assert dispatch(pool, bot, U.text(2601, "हाँ")) == "unlinked"


def test_telegram_account_linked_to_another_member_is_refused(pool, conn, bot, fake_tg, team):
    _link(pool, bot, fake_tg, team["worker-a1"], U.USER_A)
    with pool.connection() as c, c.transaction():
        issued = linking.create_link_token(c, AC, team["worker-a2"], ttl=timedelta(hours=24))
    assert dispatch(pool, bot, U.start(2701, issued.token, U.USER_A)) == "link_refused_account_in_use"
    assert fake_tg.texts == [texts_hi.ACCOUNT_IN_USE]
    # token not consumed, so the right person can still use it
    assert dispatch(pool, bot, U.start(2702, issued.token, U.USER_B)) == "linked"


def test_relink_member_to_new_phone_revokes_old_link(pool, conn, bot, fake_tg, team):
    _link(pool, bot, fake_tg, team["worker-a1"], U.USER_A, update_id=2801)
    _link(pool, bot, fake_tg, team["worker-a1"], U.USER_B, update_id=2802)
    assert dispatch(pool, bot, U.text(2803, "x", U.USER_A)) == "unlinked"
    assert dispatch(pool, bot, U.text(2804, "x", U.USER_B)) == "menu"
    assert conn.execute("SELECT count(*) AS n FROM field.tg_link").fetchone()["n"] == 2  # history kept


def test_a_failed_reply_never_undoes_the_update(pool, conn, bot, fake_tg, team):
    """Replies are sent after the commit: a reply Telegram refuses leaves the update handled and recorded
    with its real outcome (not 'error'), and a redelivery of the same update is a duplicate."""
    _link(pool, bot, fake_tg, team["worker-a1"], U.USER_A, update_id=2900)
    fake_tg.fail_send = True
    assert dispatch(pool, bot, U.text(2901, "x")) == "menu"
    fake_tg.fail_send = False
    assert dispatch(pool, bot, U.text(2901, "x")) == "duplicate"
    assert conn.execute("SELECT outcome FROM field.tg_update WHERE update_id = 2901").fetchone()["outcome"] == "menu"


def test_a_failure_before_the_commit_is_recorded_once_and_not_retried(pool, conn, bot, fake_tg, team, monkeypatch):
    from pulse.bot import flow_menu

    _link(pool, bot, fake_tg, team["worker-a1"], U.USER_A, update_id=2950)
    monkeypatch.setattr(flow_menu, "show_menu", lambda *a, **kw: 1 / 0)
    assert dispatch(pool, bot, U.text(2951, "x")) == "error"
    assert fake_tg.sent[-1]["text"] == T.FAILED_NOT_SAVED
    assert dispatch(pool, bot, U.text(2951, "x")) == "duplicate"
    assert conn.execute("SELECT outcome FROM field.tg_update WHERE update_id = 2951").fetchone()["outcome"] == "error"


def test_malformed_update_is_rejected(pool, bot, fake_tg):
    assert dispatch(pool, bot, {"message": {}}) == "invalid_update"
    assert dispatch(pool, bot, {"update_id": 3001}) == "ignored_unsupported"
