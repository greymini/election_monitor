"""Webhook and long polling both feed the same dispatcher."""
import pytest
from fastapi.testclient import TestClient

from pulse.api.main import create_app
from pulse.bot import texts_hi
from pulse.bot.polling import WebhookActiveError, run_polling
from tests import updates as U
from pulse.bot.ui import BotDeps
from pulse.config import LedgerLimits
from tests.conftest import TEST_WEBHOOK_SECRET, FakeTelegramClient

HEADER = "X-Telegram-Bot-Api-Secret-Token"


@pytest.fixture
def client(settings, pool, core, fake_tg):
    app = create_app(settings, pool=pool, core=core, telegram_client=fake_tg)
    with TestClient(app) as c:
        yield c


def test_webhook_wrong_or_missing_secret_is_403(client, conn, fake_tg):
    assert client.post("/telegram/webhook", json=U.text(4001, "x")).status_code == 403
    r = client.post("/telegram/webhook", json=U.text(4002, "x"), headers={HEADER: TEST_WEBHOOK_SECRET + "x"})
    assert r.status_code == 403
    assert fake_tg.sent == []
    assert conn.execute("SELECT count(*) AS n FROM field.tg_update").fetchone()["n"] == 0


def test_webhook_dispatches_and_dedupes(client, conn, fake_tg):
    for _ in range(2):
        r = client.post("/telegram/webhook", json=U.text(4101, "x"), headers={HEADER: TEST_WEBHOOK_SECRET})
        assert r.status_code == 200
    assert fake_tg.texts == [texts_hi.UNREGISTERED]
    assert conn.execute("SELECT outcome FROM field.tg_update WHERE update_id = 4101").fetchone()["outcome"] == "unlinked"


def test_webhook_rejects_non_json(client):
    r = client.post("/telegram/webhook", content=b"not json", headers={HEADER: TEST_WEBHOOK_SECRET})
    assert r.status_code == 400


def test_polling_dispatches_and_advances_offset(pool, conn, core):
    fake = FakeTelegramClient(updates=[[U.text(4201, "a"), U.group_text(4202, "b")], [U.text(4201, "a")]])
    calls = iter([False, False, True])
    run_polling(pool, BotDeps(fake, core, LedgerLimits()), timeout=0, should_stop=lambda: next(calls))
    assert fake.get_updates_calls == [None, 4203]
    assert fake.texts == [texts_hi.UNREGISTERED]  # redelivered 4201 was a duplicate
    outcomes = {r["update_id"]: r["outcome"] for r in conn.execute("SELECT * FROM field.tg_update")}
    assert outcomes == {4201: "unlinked", 4202: "ignored_not_private"}


def test_polling_refuses_while_webhook_is_set(pool, core):
    fake = FakeTelegramClient(webhook_url="https://example.invalid/telegram/webhook")
    with pytest.raises(WebhookActiveError):
        run_polling(pool, BotDeps(fake, core, LedgerLimits()), should_stop=lambda: True)
