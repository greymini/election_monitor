"""Shared fixtures: one embedded PostgreSQL per test session, tables truncated between tests,
a fake Telegram client, and test settings. No network."""
from __future__ import annotations

import pytest
import psycopg
import pgserver
from psycopg_pool import ConnectionPool

from pulse.bot.ui import BotDeps
from decimal import Decimal

from pulse.config import LedgerLimits, Settings
from pulse.core_adapter.stub import StubCoreAdapter
from pulse.db import CONNECT_KWARGS, connect
from pulse.migrate import apply_migrations
from pulse.seed import apply_seeds
from pulse.team.dev_seed import seed_team

pytest_plugins = ["tests.triage_world"]     # the `world` fixture: five linked phones for triage tests

TEST_JWT_SECRET = "test-only-jwt-secret-0123456789abcdef"
TEST_WEBHOOK_SECRET = "test-only-webhook-secret-0123456789"

# reference data (exp_bucket, exp_keyword) is seeded once per session and not truncated
FIELD_TABLES = ("job_run", "job", "report_event", "report_media", "report", "report_counter", "media_asset",
                "txn_audit", "money_txn", "txn_counter", "exp_custom_label", "ac_election_window",
                "tg_session", "tg_update", "tg_link", "tg_link_token", "member_scope", "member",
                "ac_setting", "admin_audit", "bot_message", "alert_message",
                "report_transcript", "issue_event", "issue", "issue_counter", "txn_followup", "idempotency_key")


class FakeTelegramClient:
    """Records every call; never touches the network."""

    def __init__(self, updates: list[list[dict]] | None = None, webhook_url: str = ""):
        self.sent: list[dict] = []
        self.answered: list[dict] = []
        self._updates = list(updates or [])
        self._webhook_url = webhook_url
        self.get_updates_calls: list[int | None] = []
        self.edits: list[dict] = []
        self.stripped: list[int] = []
        self.cards: list[dict] = []          # every message shown, sent or edited in place, in order
        self.fail_send = False
        self.fail_edit = False
        self.fail_chats: set[int] = set()    # sends to these chats raise, as an unreachable user would
        self.rate_limit_edits = False             # edits raise 429, as Telegram does for fast albums
        # fake file server: file_id -> bytes; downloads of these file_ids fail
        self.files: dict[str, bytes] = {}
        self.fail_downloads: set[str] = set()
        self.get_file_calls: list[str] = []
        self.rate_limit_sends = 0                 # the next N sends raise 429 with retry_after
        self.deleted: list[tuple[int, int]] = []
        self.refuse_delete = False
        self.retry_after = 7
        self.refuse_user_buttons = False          # Telegram refuses tg://user buttons (reporter's privacy)
        self._next_id = 1000
        # Telegram unreachable for the next `outage_calls` sending/editing/deleting calls: "429" (with
        # retry_after), "network" (no answer: error_code None, as the real client raises) or "5xx"
        self.outage: str | None = None
        self.outage_calls = 0
        self.outage_hits = 0

    def go_down(self, mode: str, calls: int = 10_000) -> None:
        self.outage, self.outage_calls = mode, calls

    def come_back(self) -> None:
        self.outage, self.outage_calls = None, 0

    def _outage(self, method: str) -> None:
        from pulse.bot.client import TelegramApiError

        if self.outage is None or self.outage_calls <= 0:
            return
        self.outage_calls -= 1
        self.outage_hits += 1
        if self.outage == "429":
            raise TelegramApiError(method, "Too Many Requests: retry after", 429, self.retry_after)
        if self.outage == "5xx":
            raise TelegramApiError(method, "Bad Gateway", 502)
        raise TelegramApiError(method, "ConnectError", None)

    def _gate(self, chat_id, reply_markup):
        from pulse.bot.client import TelegramApiError

        if self.rate_limit_sends > 0:
            self.rate_limit_sends -= 1
            raise TelegramApiError("sendMessage", "Too Many Requests: retry after", 429, self.retry_after)
        if self.refuse_user_buttons and any(b.get("url", "").startswith("tg://user")
                                            for row in (reply_markup or {}).get("inline_keyboard", []) for b in row):
            raise TelegramApiError("sendMessage", "Bad Request: BUTTON_USER_PRIVACY_RESTRICTED", 400)

    def send_photo(self, chat_id, photo, caption=None, reply_markup=None):
        self._outage("sendPhoto")
        self._gate(chat_id, reply_markup)
        if chat_id in self.fail_chats:
            from pulse.bot.client import TelegramApiError

            raise TelegramApiError("sendPhoto", "Forbidden: bot was blocked by the user", 403)
        self._next_id += 1
        msg = {"chat_id": chat_id, "text": caption, "photo": photo, "reply_markup": reply_markup,
               "message_id": self._next_id}
        self.sent.append(msg)
        self.cards.append(msg)
        return {"message_id": self._next_id}

    def edit_message_caption(self, chat_id, message_id, caption, reply_markup=None):
        self._outage("editMessageCaption")
        msg = {"chat_id": chat_id, "message_id": message_id, "text": caption, "reply_markup": reply_markup,
               "caption": True}
        self.edits.append(msg)
        self.cards.append(msg)
        return True

    def send_message(self, chat_id, text, reply_markup=None):
        self._outage("sendMessage")
        if self.fail_send:
            raise RuntimeError("simulated send failure")
        self._gate(chat_id, reply_markup)
        if chat_id in self.fail_chats:
            from pulse.bot.client import TelegramApiError

            raise TelegramApiError("sendMessage", "Forbidden: bot was blocked by the user", 403)
        self._next_id += 1
        msg = {"chat_id": chat_id, "text": text, "reply_markup": reply_markup, "message_id": self._next_id}
        self.sent.append(msg)
        self.cards.append(msg)
        return {"message_id": self._next_id}

    def edit_message_text(self, chat_id, message_id, text, reply_markup=None):
        self._outage("editMessageText")
        if self.rate_limit_edits:
            from pulse.bot.client import TelegramApiError

            raise TelegramApiError("editMessageText", "Too Many Requests: retry after 1", 429)
        if self.fail_edit:
            from pulse.bot.client import TelegramApiError

            raise TelegramApiError("editMessageText", "Bad Request: message to edit not found", 400)
        msg = {"chat_id": chat_id, "message_id": message_id, "text": text, "reply_markup": reply_markup}
        self.edits.append(msg)
        self.cards.append(msg)
        return True

    def edit_message_reply_markup(self, chat_id, message_id, reply_markup=None):
        self._outage("editMessageReplyMarkup")
        self.stripped.append(message_id)
        return True

    def to(self, chat_id: int) -> list[dict]:
        """Every message shown in one chat, in order."""
        return [m for m in self.cards if m["chat_id"] == chat_id]

    @property
    def last(self) -> dict:
        """The most recent message shown, whether sent or edited in place."""
        return self.cards[-1]
    def answer_callback_query(self, callback_query_id, text=None):
        self.answered.append({"callback_query_id": callback_query_id, "text": text})
        return True

    def delete_message(self, chat_id, message_id):
        self._outage("deleteMessage")
        from pulse.bot.client import TelegramApiError

        if self.refuse_delete:
            raise TelegramApiError("deleteMessage", "Bad Request: message can't be deleted", 400)
        self.deleted.append((chat_id, message_id))
        return True

    def get_file(self, file_id):
        self.get_file_calls.append(file_id)
        if file_id in self.fail_downloads:
            from pulse.bot.client import TelegramApiError

            raise TelegramApiError("getFile", "Bad Request: temporarily unavailable", 500)
        data = self.files[file_id]
        return {"file_id": file_id, "file_unique_id": "u-" + file_id, "file_size": len(data), "file_path": f"files/{file_id}"}

    def download_file(self, file_path, *, max_bytes):
        data = self.files[file_path.split("/", 1)[1]]
        if len(data) > max_bytes:
            from pulse.bot.client import TelegramApiError

            raise TelegramApiError("downloadFile", "file larger than the download limit", 413)
        return data

    def get_updates(self, offset, timeout):
        self.get_updates_calls.append(offset)
        return self._updates.pop(0) if self._updates else []

    def get_webhook_info(self):
        return {"url": self._webhook_url}

    @property
    def texts(self) -> list[str]:
        return [m["text"] for m in self.sent]


@pytest.fixture(scope="session")
def database_url(tmp_path_factory) -> str:
    server = pgserver.get_server(tmp_path_factory.mktemp("pgdata"), cleanup_mode="delete")
    with psycopg.connect(server.get_uri(), autocommit=True) as admin:
        admin.execute('CREATE DATABASE "pulse_test"')
    url = server.get_uri("pulse_test")
    with connect(url) as conn:
        apply_migrations(conn)
        apply_seeds(conn)
    yield url
    server.cleanup()


@pytest.fixture(scope="session")
def pool(database_url) -> ConnectionPool:
    p = ConnectionPool(database_url, min_size=1, max_size=8, kwargs=CONNECT_KWARGS, open=False)
    p.open(wait=True)
    yield p
    p.close()


@pytest.fixture(autouse=True)
def _clean_tables(request):
    yield
    if "pool" in request.fixturenames:
        p = request.getfixturevalue("pool")
        with p.connection() as conn:
            conn.execute("TRUNCATE " + ", ".join(f"field.{t}" for t in FIELD_TABLES) + " CASCADE")


@pytest.fixture
def conn(pool):
    with pool.connection() as c:
        yield c


@pytest.fixture
def core() -> StubCoreAdapter:
    return StubCoreAdapter(jwt_secret=TEST_JWT_SECRET)


@pytest.fixture
def team(pool, core) -> dict:
    """The dev tree: principal → incharge-a → worker-a1, worker-a2; principal → incharge-b → worker-b1."""
    with pool.connection() as c, c.transaction():
        return seed_team(c, core)


@pytest.fixture
def fake_tg() -> FakeTelegramClient:
    return FakeTelegramClient()


@pytest.fixture
def limits() -> LedgerLimits:
    # PIN gate out of reach so earlier money tests run without it; test_pin.py uses the real limit
    return LedgerLimits(pin_limit=Decimal("1000000000"))


@pytest.fixture
def report_limits():
    from pulse.config import ReportLimits

    return ReportLimits()


@pytest.fixture
def storage(tmp_path):
    from pulse.common.storage import LocalDiskStorage

    return LocalDiskStorage(tmp_path / "media")


@pytest.fixture
def worker(pool, fake_tg, core, storage, limits, report_limits):
    from pulse.worker.jobs import WorkerDeps

    return WorkerDeps(pool=pool, client=fake_tg, core=core, storage=storage, ledger_limits=limits,
                      report_limits=report_limits, worker_id="test-worker")


@pytest.fixture
def bot(fake_tg, core, limits, report_limits) -> BotDeps:
    return BotDeps(client=fake_tg, core=core, limits=limits, reports=report_limits)


@pytest.fixture
def settings(database_url) -> Settings:
    return Settings(
        database_url=database_url,
        telegram_bot_token="1234567:TEST-ONLY-bot-token-not-real-abcdefgh",
        telegram_bot_username="pulse_test_bot",
        telegram_webhook_secret=TEST_WEBHOOK_SECRET,
        jwt_secret=TEST_JWT_SECRET,
    )


@pytest.fixture
def drain(worker):
    """Run the worker's queue until nothing is ready: delivers everything the outbox queued."""
    from pulse.worker.runner import run_queue

    def go(now=None, rounds: int = 10):
        done = []
        for _ in range(rounds):
            batch = run_queue(worker, now, batch=100)
            if not batch:
                break
            done += batch
        return done

    return go
