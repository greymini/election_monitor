"""The dev-only browser demo (pulse/demo, scripts/demo.py): guards, the in-memory client, the routes
and the sample data. Network-free: Telegram is the in-memory client."""
from __future__ import annotations

import base64
import dataclasses
import struct
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

from pulse.api.main import create_app
from pulse.asr import fake as fake_asr
from pulse.bot.client import TelegramApiError
from pulse.common import clock
from pulse.config import ConfigError, load_settings
from pulse.demo import assert_embedded
from pulse.demo.memory_client import MemoryTelegramClient


@pytest.fixture(autouse=True)
def _real_time(request):
    text = fake_asr.TEXT
    yield
    clock.reset_offset()
    fake_asr.TEXT = text
    if "pool" in request.fixturenames:          # the sample maps statutory heads: buckets are not truncated
        with request.getfixturevalue("pool").connection() as c:
            c.execute("UPDATE field.exp_bucket SET statutory_head = NULL WHERE statutory_head IS NOT NULL")


@pytest.fixture(scope="module")
def data_dir(database_url) -> Path:
    with psycopg.connect(database_url) as c:
        return Path(c.execute("SHOW data_directory").fetchone()[0])


def make_jpeg() -> bytes:
    def seg(marker: int, body: bytes) -> bytes:
        return bytes([0xFF, marker]) + struct.pack(">H", len(body) + 2) + body
    return (b"\xff\xd8" + seg(0xE0, b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00")
            + seg(0xDB, b"\x00" + bytes(range(64))) + seg(0xDA, b"\x01\x01\x00\x00\x3f\x00") + b"\x12\x34\xff\xd9")


def demo_client(settings, pool, core, storage, data_dir, setup=None):
    s = dataclasses.replace(settings, demo_mode=True, demo_pgdata=data_dir, asr_engine="fake")

    def prepare(hub):
        hub.background_worker = False             # drain() only: deterministic
        hub.link_all()
        if setup:
            setup(hub)
    app = create_app(s, pool=pool, core=core, storage=storage, demo_setup=prepare)
    return TestClient(app), app


# ---- guards -----------------------------------------------------------------------------------

def test_demo_settings_need_no_env_and_make_throwaway_secrets(database_url):
    a = load_settings({"DEMO_MODE": "1", "DATABASE_URL": database_url})
    b = load_settings({"DEMO_MODE": "1", "DATABASE_URL": database_url})
    assert a.demo_mode and a.asr_engine == "fake"
    assert a.jwt_secret != b.jwt_secret and a.telegram_bot_token != b.telegram_bot_token


def test_demo_mode_refuses_webhook():
    with pytest.raises(ConfigError, match="DEMO_MODE"):
        from pulse.config import Settings, check_settings
        s = load_settings({"DEMO_MODE": "1", "DATABASE_URL": "postgresql://localhost/x"})
        check_settings(dataclasses.replace(s, bot_mode="webhook", webhook_url="https://example.invalid/hook"))


def test_demo_refuses_a_remote_database_without_connecting(tmp_path):
    with pytest.raises(ConfigError, match="not on this machine"):
        assert_embedded("postgresql://u:p@db.example.invalid:5432/pulse", tmp_path)


def test_demo_refuses_a_local_database_that_is_not_its_own(database_url, tmp_path):
    with pytest.raises(ConfigError, match="not the demo's embedded"):
        assert_embedded(database_url, tmp_path / "some-other-pgdata")


def test_demo_accepts_its_own_embedded_database(database_url, data_dir):
    assert_embedded(database_url, data_dir)


def test_app_refuses_demo_mode_on_another_database(settings, tmp_path):
    with pytest.raises(ConfigError):
        create_app(dataclasses.replace(settings, demo_mode=True, demo_pgdata=tmp_path))


def test_demo_routes_do_not_exist_outside_demo_mode(settings, pool, core, fake_tg):
    with TestClient(create_app(settings, pool=pool, core=core, telegram_client=fake_tg)) as c:
        assert c.get("/demo").status_code == 404
        assert c.get("/demo/api/state").status_code == 404
        assert c.post("/demo/api/skip", json={"minutes": 10}).status_code == 404


def test_clock_cannot_move_outside_demo_mode():
    with pytest.raises(RuntimeError):
        clock.advance(timedelta(minutes=10))
    clock.allow_offset()
    before = clock.utcnow()
    clock.advance(timedelta(days=1))
    assert clock.utcnow() - before >= timedelta(days=1)
    with pytest.raises(ValueError):
        clock.advance(timedelta(minutes=-1))
    clock.reset_offset()
    assert clock.offset() == timedelta(0)
    with pytest.raises(RuntimeError):
        clock.advance(timedelta(minutes=1))


# ---- in-memory client -------------------------------------------------------------------------

def test_memory_client_keeps_each_message_current():
    tg = MemoryTelegramClient()
    sent = tg.send_message(5, "one", {"inline_keyboard": [[{"text": "ok", "callback_data": "x"}]]})
    mid = sent["message_id"]
    tg.edit_message_text(5, mid, "two", None)
    other = tg.send_message(5, "three")["message_id"]
    tg.delete_message(5, other)
    tg.expect_callback("cq1", 5)
    tg.answer_callback_query("cq1", "done")
    chat = tg.chat(5)
    assert [(m["text"], m["edits"], m["deleted"]) for m in chat["messages"]] == [("two", 1, False), ("three", 0, True)]
    assert chat["messages"][0]["reply_markup"] is None
    assert chat["toasts"][-1]["text"] == "done"
    assert tg.chat(6)["messages"] == []
    with pytest.raises(TelegramApiError):
        tg.edit_message_text(5, other, "edit after delete")


def test_memory_client_serves_files_like_telegram():
    tg = MemoryTelegramClient()
    file_id, unique = tg.add_file(b"abc", "image/jpeg")
    assert tg.add_file(b"abc")[1] == unique                     # same bytes, same unique id
    info = tg.get_file(file_id)
    assert tg.download_file(info["file_path"], max_bytes=10) == b"abc"
    with pytest.raises(TelegramApiError):
        tg.download_file(info["file_path"], max_bytes=2)
    with pytest.raises(TelegramApiError):
        tg.get_file("nope")


# ---- routes -----------------------------------------------------------------------------------

def _bot_messages(chat):
    return [m for m in chat["messages"] if m["from"] == "bot" and not m["deleted"]]


def _button(msg, data_suffix):
    return next(b["callback_data"] for row in msg["reply_markup"]["inline_keyboard"] for b in row
                if (b.get("callback_data") or "").endswith(data_suffix))


def test_expense_typed_in_the_page_goes_through_the_dispatcher(settings, pool, core, storage, data_dir, team):
    c, app = demo_client(settings, pool, core, storage, data_dir)
    with c:
        assert c.get("/demo").status_code == 200
        st = c.get("/demo/api/state").json()
        assert {m["key"] for m in st["members"]} >= {"principal", "worker-a1"}
        got = c.post("/demo/api/message", json={"member": "worker-a1", "text": "250 chai"}).json()
        assert got["outcome"] == "confirm"
        assert got["messages"][-2] == {**got["messages"][-2], "from": "user", "text": "250 chai"}
        card = _bot_messages(got)[-1]
        got = c.post("/demo/api/callback", json={"member": "worker-a1", "message_id": card["message_id"],
                                                 "data": _button(card, ":ok")}).json()
        assert got["outcome"] == "posted"
        assert _bot_messages(got)[-1]["edits"] == 1               # the same card, edited in place
        txns = c.get("/acs/32/field/txns", headers={"Authorization": f"Bearer {st['token']}"}).json()
        assert [(t["kind"], t["amount"]) for t in txns["items"]] == [("expense", "250.00")]


def test_photo_from_the_page_auto_files_after_skipping_ten_minutes(settings, pool, core, storage, data_dir, team):
    c, app = demo_client(settings, pool, core, storage, data_dir)
    with c:
        body = {"member": "worker-a1", "name": "x.jpg", "mime": "image/jpeg",
                "data_b64": base64.b64encode(make_jpeg()).decode(), "caption": None}
        got = c.post("/demo/api/upload", json=body).json()
        assert got["outcome"].startswith("report")
        assert any(m.get("photo") for m in got["messages"] if m["from"] == "user")
        with pool.connection() as conn:
            assert conn.execute("SELECT status FROM field.report").fetchone()["status"] == "draft"
        assert c.post("/demo/api/skip", json={"minutes": 7}).status_code == 422
        moved = c.post("/demo/api/skip", json={"minutes": 10}).json()
        assert moved["snapped_to_followup_window"] is False
        with pool.connection() as conn:
            r = conn.execute("SELECT status, filed_at FROM field.report").fetchone()
            asset = conn.execute("SELECT status FROM field.media_asset").fetchone()
        assert r["status"] == "new" and asset["status"] == "stored"
        token = c.get("/demo/api/state").json()["token"]
        h = {"Authorization": f"Bearer {token}"}
        rid = c.get("/acs/32/field/reports", headers=h).json()["items"][0]["report_id"]
        media = c.get(f"/acs/32/field/reports/{rid}", headers=h).json()["media"]
        assert c.get(media[0]["url"]).status_code == 200        # signed link works on the moved clock


def test_fourteen_day_skip_lands_in_the_follow_up_window(settings, pool, core, storage, data_dir, team):
    from pulse.common.clock import IST
    from pulse.worker.jobs import FOLLOWUP_FROM, FOLLOWUP_UNTIL

    c, app = demo_client(settings, pool, core, storage, data_dir)
    with c:
        moved = c.post("/demo/api/skip", json={"minutes": 20160}).json()
        local = clock.utcnow().astimezone(IST).time()
        assert FOLLOWUP_FROM <= local < FOLLOWUP_UNTIL
        assert clock.offset() >= timedelta(days=14)
        assert isinstance(moved["snapped_to_followup_window"], bool)


# ---- sample data ------------------------------------------------------------------------------

def test_sample_data_loads_through_the_services(settings, pool, core, storage, data_dir, team):
    from pulse.demo import sample

    loaded = {}

    def setup(hub):
        loaded["n"] = sample.load(hub.state.pool, hub.state.core, hub.client,
                                  {k: m.member_id for k, m in hub.members.items()},
                                  limits=hub.state.settings.ledger, drain=hub.drain, ac_id="ac-32")
    c, app = demo_client(settings, pool, core, storage, data_dir, setup)
    with c:
        n = loaded["n"]
        assert (n.expenses, n.reports, n.issues, n.followups_answered, n.windows) == (43, 10, 2, 1, 1)
        with pool.connection() as conn:
            kinds = {(r["kind"], r["status"]): r["n"] for r in conn.execute(
                "SELECT kind, status, count(*) AS n FROM field.money_txn GROUP BY kind, status")}
            statuses = {r["status"] for r in conn.execute("SELECT DISTINCT status FROM field.report")}
            transcribed = conn.execute(
                "SELECT count(*) AS n FROM field.report WHERE transcript_status = 'done'").fetchone()["n"]
            stored = conn.execute("SELECT count(*) FILTER (WHERE status = 'stored') AS ok, count(*) AS n "
                                  "FROM field.media_asset").fetchone()
            linked = conn.execute("SELECT count(*) AS n FROM field.money_txn WHERE report_id IS NOT NULL").fetchone()["n"]
            spent = conn.execute("SELECT sum(amount) AS s FROM field.money_txn WHERE kind = 'expense'").fetchone()["s"]
        assert {("advance", "posted"), ("advance", "pending_ack"), ("advance", "disputed"),
                ("return", "posted"), ("return", "pending_ack")} <= set(kinds)
        assert statuses >= {"new", "acknowledged", "assigned", "in_progress", "resolved", "closed_no_action"}
        assert transcribed == 5 and stored["ok"] == stored["n"] and linked == 2

        # money given is not money spent: the spend total is the expenses only
        token = c.get("/demo/api/state").json()["token"]
        totals = c.get("/acs/32/field/spend/summary", headers={"Authorization": f"Bearer {token}"}).json()["totals"]
        assert Decimal(totals["spend_total"]) == spent
        assert totals["entries"] == 43


def test_dev_sign_in_lists_personas_and_issues_tokens(settings, pool, core, storage, data_dir, team):
    c, app = demo_client(settings, pool, core, storage, data_dir)
    with c:
        users = c.get("/demo/api/dev-users").json()["users"]
        personas = {u["user_id"]: u["persona"] for u in users}
        assert personas["usr-principal"] == "principal" and personas["usr-incharge-a"] == "incharge"
        assert personas["usr-worker-a1"] == "worker"
        token = c.post("/demo/api/token", json={"user_id": "usr-worker-a1"}).json()["token"]
        me = c.get("/acs/32/field/me", headers={"Authorization": f"Bearer {token}"}).json()
        assert me["member"]["finance_scope"] == "own"
        assert c.post("/demo/api/token", json={"user_id": "usr-nobody"}).status_code == 404


def test_web_app_is_served_at_root_without_hiding_the_api(tmp_path):
    from fastapi import FastAPI

    from pulse.demo import webapp

    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>Election Monitor</html>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    app = FastAPI()

    @app.get("/acs/{n}/field/me")
    def me(n: int):
        return {"n": n}

    assert webapp.mount(app, dist) is True
    c = TestClient(app)
    assert "Election Monitor" in c.get("/").text
    assert "Election Monitor" in c.get("/field/ledger").text          # client-side route
    assert c.get("/assets/app.js").text == "console.log(1)"
    assert c.get("/acs/32/field/me").json() == {"n": 32}
    assert c.get("/acs/32/field/nope").status_code == 404             # API paths never fall back
    assert c.get("/demo/api/state").status_code == 404
    (tmp_path / "secret.txt").write_text("do not serve", encoding="utf-8")
    for path in ("/..%2fsecret.txt", "/assets/..%2f..%2fsecret.txt", "/%2e%2e/secret.txt"):
        assert "do not serve" not in c.get(path).text                  # never outside dist


def test_web_app_not_mounted_without_a_build(tmp_path):
    from fastapi import FastAPI

    from pulse.demo import webapp

    assert webapp.mount(FastAPI(), tmp_path / "missing") is False
