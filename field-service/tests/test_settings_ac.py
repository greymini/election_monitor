"""Per-AC settings (slice 2c): field.ac_setting overrides the environment defaults everywhere, read
through settings_ac.effective; the admin API views and changes them, and every change is audited."""
from datetime import timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from pulse import settings_ac
from pulse.api.main import create_app
from pulse.common.clock import utcnow
from pulse.config import LedgerLimits, ReportLimits, Retention
from pulse.ledger import reminders
from pulse.worker.runner import run_timed
from tests.helpers import link, raw_txn
from tests.test_flow_report import Reporter, _reports

BASE = "/acs/32/field/settings"


@pytest.fixture
def api(settings, pool, core, fake_tg):
    app = create_app(settings, pool=pool, core=core, telegram_client=fake_tg)
    with TestClient(app) as c:
        c.headers["Authorization"] = f"Bearer {core.issue_token('usr-principal')}"
        yield c


def test_defaults_when_nothing_overridden(conn):
    d = settings_ac.Defaults(ledger=LedgerLimits(large_entry=Decimal("30000")), reports=ReportLimits(),
                             retention=Retention(chat_cleanup_hours=12))
    eff = settings_ac.effective(conn, "ac-32", d)
    assert eff.ledger.large_entry == Decimal("30000") and eff.retention.chat_cleanup_hours == 12
    assert set(eff.sources.values()) == {"default"}
    assert eff.retention.media_retention_days is None                     # missing stays missing


def test_override_wins_and_only_for_that_ac(conn):
    settings_ac.change(conn, "ac-32", {"large_entry": "40000", "autofile_minutes": 5, "chat_cleanup_hours": 6},
                       actor_user_id="usr-principal")
    eff = settings_ac.effective(conn, "ac-32")
    assert eff.ledger.large_entry == Decimal("40000") and isinstance(eff.ledger.large_entry, Decimal)
    assert eff.reports.autofile_minutes == 5 and eff.retention.chat_cleanup_hours == 6
    assert eff.sources["large_entry"] == "override" and eff.sources["pin_limit"] == "default"
    other = settings_ac.effective(conn, "ac-99")
    assert other.ledger.large_entry == LedgerLimits().large_entry


def test_change_is_audited_and_reset_returns_default(conn):
    settings_ac.change(conn, "ac-32", {"pin_limit": "75000"}, actor_user_id="usr-principal")
    settings_ac.change(conn, "ac-32", {"pin_limit": None}, actor_user_id="usr-principal")
    assert settings_ac.effective(conn, "ac-32").ledger.pin_limit == LedgerLimits().pin_limit
    rows = conn.execute("SELECT action, actor_user_id, data FROM field.admin_audit ORDER BY audit_id").fetchall()
    assert [r["action"] for r in rows] == ["setting_changed", "setting_changed"]
    assert rows[0]["data"] == {"key": "pin_limit", "old": "50000", "new": "75000", "reset_to_default": False}
    assert rows[1]["data"]["reset_to_default"] is True and rows[1]["actor_user_id"] == "usr-principal"


@pytest.mark.parametrize("key,value", [("chat_cleanup_hours", 48), ("chat_cleanup_hours", 0), ("pin_limit", "-5"),
                                       ("autofile_minutes", "soon"), ("no_such_key", 1)])
def test_bad_values_refused(conn, key, value):
    with pytest.raises(settings_ac.SettingError):
        settings_ac.change(conn, "ac-32", {key: value}, actor_user_id="usr-principal")
    assert conn.execute("SELECT count(*) AS n FROM field.ac_setting").fetchone()["n"] == 0


def test_api_view_and_change(api, conn, team, core):
    body = api.get(BASE).json()
    assert set(body) == set(settings_ac.KEYS)
    assert body["pin_limit"] == {"value": "50000", "default": "50000", "source": "default",
                                 "description": settings_ac.KEYS["pin_limit"][4]}
    r = api.patch(BASE, json={"pin_limit": "60000", "media_retention_days": 90})
    assert r.status_code == 200
    assert r.json()["pin_limit"]["source"] == "override" and r.json()["media_retention_days"]["value"] == 90
    assert api.patch(BASE, json={"chat_cleanup_hours": 72}).status_code == 422
    assert api.patch(BASE, json={}).status_code == 422
    rep = {"Authorization": f"Bearer {core.issue_token('usr-worker-a1')}"}
    assert api.get(BASE, headers=rep).status_code == 403
    assert api.patch(BASE, json={"pin_limit": "1"}, headers=rep).status_code == 403
    assert conn.execute("SELECT count(*) AS n FROM field.admin_audit").fetchone()["n"] == 2


def test_autofile_follows_ac_setting(conn, pool, bot, team, worker):
    settings_ac.change(conn, "ac-32", {"autofile_minutes": 3}, actor_user_id="usr-principal")
    link(pool, bot, team["worker-a1"])
    rep = Reporter(pool, bot)
    t0 = utcnow()
    rep.photo("set1", now=t0)
    assert run_timed(worker, t0 + timedelta(minutes=4))["report_autofile"] == {"filed": 1}   # default is 10
    assert _reports(conn)[0]["status"] == "new"


def test_ack_reminder_follows_ac_setting(conn, core, limits, team):
    raw_txn(conn, kind="advance", amount="5000", from_member_id=team["principal"], to_member_id=team["incharge-a"],
            status="pending_ack", created_by=team["principal"], created_at=utcnow() - timedelta(hours=5))
    now = utcnow()
    assert reminders.advances_needing_reminder(conn, now, limits, "ac-32") == []          # default 24 h
    settings_ac.change(conn, "ac-32", {"ack_remind_hours": 4}, actor_user_id="usr-principal")
    eff = settings_ac.effective(conn, "ac-32", settings_ac.Defaults(ledger=limits))
    assert len(reminders.advances_needing_reminder(conn, now, eff.ledger, "ac-32")) == 1
    assert reminders.advances_needing_reminder(conn, now, eff.ledger, "ac-99") == []


def test_bot_large_entry_flag_follows_ac_setting(conn, pool, bot, team):
    from pulse.bot import texts_hi as T
    from tests.test_flow_expense import Chat

    settings_ac.change(conn, "ac-32", {"large_entry": "5000"}, actor_user_id="usr-principal")
    link(pool, bot, team["worker-a1"])
    chat = Chat(pool, bot)
    chat.say("6000 tent")
    assert chat.tap(T.BTN_CONFIRM) == "warn"                                               # default flags at 25,000
    assert T.WARNINGS["large"].format(limit="₹5,000") in chat.text
