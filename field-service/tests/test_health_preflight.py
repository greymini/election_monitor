"""GET /health and scripts/preflight.py (slice 2c)."""
from __future__ import annotations

import os
import shutil
from dataclasses import replace
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from pulse.api.main import create_app
from pulse.common.clock import utcnow
from pulse.config import REPO_ROOT
from pulse.db import connect
from pulse.migrate import MIGRATIONS_DIR
from scripts import preflight
from tests.conftest import TEST_JWT_SECRET, TEST_WEBHOOK_SECRET, FakeTelegramClient

HOOK = "https://demo.example.org/telegram/webhook"


def _env(database_url, media_dir, **extra):
    return {"DATABASE_URL": database_url, "TELEGRAM_BOT_TOKEN": "1234567:TEST-ONLY-bot-token-not-real-abcdefgh",
            "TELEGRAM_BOT_USERNAME": "pulse_test_bot", "TELEGRAM_WEBHOOK_SECRET": TEST_WEBHOOK_SECRET,
            "JWT_SECRET": TEST_JWT_SECRET, "MEDIA_DIR": str(media_dir), **extra}


def _worker_ran(conn, ago: timedelta):
    t = utcnow() - ago
    conn.execute("""INSERT INTO field.job_run (job_name, worker_id, started_at, finished_at, status)
                    VALUES ('report_autofile', 'test', %s, %s, 'ok')""", (t, t))


# ---- /health ----------------------------------------------------------------------------------

def test_health_reports_worker_and_queue(settings, pool, core, fake_tg, conn):
    with TestClient(create_app(settings, pool=pool, core=core, telegram_client=fake_tg)) as api:
        body = api.get("/health").json()
        assert body["status"] == "degraded" and body["database"] == "ok"
        assert body["worker_last_run_seconds"] is None                   # never ran: missing, not 0
        assert body["queue"] == {"ready": 0, "waiting": 0, "running": 0, "failed_24h": 0}
        _worker_ran(conn, timedelta(seconds=20))
        res = api.get("/health")
    assert res.status_code == 200 and res.json()["status"] == "ok"
    assert 15 <= res.json()["worker_last_run_seconds"] <= 60
    for secret in (TEST_JWT_SECRET, TEST_WEBHOOK_SECRET, settings.database_url):
        assert secret not in res.text


def test_health_503_when_database_unreachable(settings, pool, core, fake_tg):
    class DeadPool:
        def connection(self, timeout=None):
            raise OSError("connection refused")

    app = create_app(settings, pool=pool, core=core, telegram_client=fake_tg)
    with TestClient(app) as api:
        app.state.pulse.pool = DeadPool()
        res = api.get("/health")
        app.state.pulse.pool = pool
    assert res.status_code == 503
    assert res.json() == {"status": "down", "database": "unreachable"}


# ---- preflight --------------------------------------------------------------------------------

def test_secrets(database_url, tmp_path):
    ok, s = preflight.check_secrets(_env(database_url, tmp_path))
    assert ok.status == "ok" and s is not None
    bad, _ = preflight.check_secrets(_env(database_url, tmp_path, JWT_SECRET="change-me"))
    assert bad.status == "fail" and "JWT_SECRET" in bad.detail and "change-me" not in bad.detail
    same, _ = preflight.check_secrets(_env(database_url, tmp_path, JWT_SECRET=TEST_WEBHOOK_SECRET))
    assert same.status == "fail" and "differ" in same.detail
    weak, _ = preflight.check_secrets(_env(database_url, tmp_path, JWT_SECRET="ab" * 20))
    assert weak.status == "fail" and "JWT_SECRET" in weak.detail and "ab" * 20 not in weak.detail


def test_migrations_current_pending_and_edited(conn, tmp_path):
    assert preflight.check_migrations(conn).status == "ok"
    d = tmp_path / "mig"
    shutil.copytree(MIGRATIONS_DIR, d)
    n = len(list(d.glob("*.sql"))) + 1
    (d / f"field_{n:04d}_later.sql").write_text("SELECT 1;\n", encoding="utf-8")
    pending = preflight.check_migrations(conn, d)
    assert pending.status == "fail" and f"field_{n:04d}_later.sql" in pending.detail
    (d / f"field_{n:04d}_later.sql").unlink()
    first = sorted(d.glob("*.sql"))[0]
    first.write_text(first.read_text(encoding="utf-8") + "\n-- edited\n", encoding="utf-8")
    edited = preflight.check_migrations(conn, d)
    assert edited.status == "fail" and first.name in edited.detail


def test_media_dir(tmp_path):
    assert preflight.check_media_dir(REPO_ROOT / "media").status == "fail"              # inside the repo
    assert preflight.check_media_dir(tmp_path / "missing").status == "fail"
    assert preflight.check_media_dir(tmp_path, posix=False).status == "warn"           # Windows: check ACLs by hand
    if os.name != "nt":
        tmp_path.chmod(0o700)
        assert preflight.check_media_dir(tmp_path).status == "ok"
        tmp_path.chmod(0o755)
    assert preflight.check_media_dir(tmp_path, posix=True).status == "fail"            # readable by others


def test_worker_seen(conn):
    now = utcnow()
    assert preflight.check_worker(conn, now).status == "fail"
    _worker_ran(conn, timedelta(minutes=8))
    assert preflight.check_worker(conn, now).status == "fail"
    _worker_ran(conn, timedelta(seconds=30))
    assert preflight.check_worker(conn, now).status == "ok"


@pytest.mark.parametrize("mode,configured,telegram,status", [
    ("polling", None, "", "ok"),
    ("polling", None, HOOK, "fail"),                     # a webhook blocks getUpdates
    ("webhook", HOOK, "", "fail"),
    ("webhook", HOOK, "https://other.example.org/hook", "fail"),
    ("webhook", HOOK, HOOK, "ok"),
])
def test_webhook_matches_mode(settings, mode, configured, telegram, status):
    s = replace(settings, bot_mode=mode, webhook_url=configured)
    assert preflight.check_webhook(FakeTelegramClient(webhook_url=telegram), s).status == status


def test_run_all_and_exit_code(database_url, tmp_path, conn, capsys, monkeypatch):
    env = _env(database_url, tmp_path)
    checks = preflight.run(env, connect=connect, client=FakeTelegramClient())
    by = {c.name: c.status for c in checks}
    assert by == {"secrets": "ok", "media": "warn" if os.name == "nt" else by["media"], "asr": "ok",
                  "migrations": "ok", "worker": "fail", "webhook": "ok"}
    monkeypatch.setattr(preflight, "run", lambda: checks)
    assert preflight.main() == 1                                        # the worker check failed
    out = capsys.readouterr().out
    assert TEST_JWT_SECRET not in out and TEST_WEBHOOK_SECRET not in out


def test_asr_engine_check():
    assert preflight.check_asr("none").status == "ok"
    assert preflight.check_asr("fake").status == "warn"
    assert preflight.check_asr("no_such_engine").status == "fail"


def test_unreachable_database_fails(database_url, tmp_path):
    def refuse(url):
        raise OSError("connection refused")

    checks = preflight.run(_env(database_url, tmp_path), connect=refuse, client=FakeTelegramClient())
    assert {c.name: c.status for c in checks}["database"] == "fail"
