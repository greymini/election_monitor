"""One-time link tokens: hashed storage, 24h expiry, single use, replacement."""
from datetime import datetime, timedelta, timezone

from pulse.bot import texts_hi
from pulse.bot.dispatch import dispatch
from pulse.team import linking
from pulse.team.linking import LinkResult, hash_token
from tests import updates as U

AC = "ac-32"
TTL = timedelta(hours=24)


def _issue(pool, member_id, now=None):
    with pool.connection() as conn, conn.transaction():
        return linking.create_link_token(conn, AC, member_id, ttl=TTL, now=now)


def test_token_is_stored_hashed_with_24h_expiry(pool, conn, team):
    t0 = datetime(2026, 11, 19, 6, 0, tzinfo=timezone.utc)
    issued = _issue(pool, team["worker-a1"], now=t0)
    assert issued.expires_at == t0 + TTL
    assert 32 <= len(issued.token) <= 64
    rows = conn.execute("SELECT token_hash, expires_at FROM field.tg_link_token").fetchall()
    assert rows == [{"token_hash": hash_token(issued.token), "expires_at": t0 + TTL}]
    assert issued.token not in str(rows)


def test_expired_token_is_refused(pool, conn, bot, fake_tg, team):
    past = datetime.now(timezone.utc) - timedelta(hours=25)
    issued = _issue(pool, team["worker-a1"], now=past)  # expired an hour ago
    assert dispatch(pool, bot, U.start(1001, issued.token)) == "link_invalid"
    assert fake_tg.texts == [texts_hi.LINK_INVALID]
    assert conn.execute("SELECT count(*) AS n FROM field.tg_link").fetchone()["n"] == 0


def test_token_just_inside_expiry_works(pool, conn, team):
    t0 = datetime(2026, 11, 19, 6, 0, tzinfo=timezone.utc)
    issued = _issue(pool, team["worker-a1"], now=t0)
    with pool.connection() as c, c.transaction():
        result, _ = linking.redeem_link_token(c, issued.token, 1, 1, now=t0 + TTL - timedelta(seconds=1))
    assert result is LinkResult.LINKED


def test_token_cannot_be_reused(pool, conn, bot, fake_tg, team):
    issued = _issue(pool, team["worker-a1"])
    assert dispatch(pool, bot, U.start(1101, issued.token, U.USER_A)) == "linked"
    # same token again, from someone else
    assert dispatch(pool, bot, U.start(1102, issued.token, U.USER_B)) == "link_invalid"
    assert fake_tg.texts[-1] == texts_hi.LINK_INVALID
    links = conn.execute("SELECT telegram_user_id FROM field.tg_link WHERE revoked_at IS NULL").fetchall()
    assert links == [{"telegram_user_id": U.USER_A["id"]}]
    used = conn.execute("SELECT used_at FROM field.tg_link_token").fetchone()["used_at"]
    assert used is not None


def test_new_token_replaces_unused_old_one(pool, bot, fake_tg, team):
    old = _issue(pool, team["worker-a1"])
    new = _issue(pool, team["worker-a1"])
    assert dispatch(pool, bot, U.start(1201, old.token)) == "link_invalid"
    assert dispatch(pool, bot, U.start(1202, new.token)) == "linked"


def test_unknown_token_is_refused(pool, bot, fake_tg, team):
    assert dispatch(pool, bot, U.start(1301, "not-a-real-token")) == "link_invalid"


def test_no_token_for_inactive_member(pool, team):
    import pytest

    from pulse.team.errors import Invalid

    with pool.connection() as conn, conn.transaction():
        conn.execute("UPDATE field.member SET active = false, deactivated_at = now() WHERE member_id = %s", (team["worker-a1"],))
    with pytest.raises(Invalid):
        _issue(pool, team["worker-a1"])
