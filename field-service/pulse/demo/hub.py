"""The demo's engine room: builds Telegram-shaped update dicts for the browser panes, hands them to the
one dispatcher, runs the worker in-process and moves the clock.
"""
from __future__ import annotations

import itertools
import json
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any
from uuid import UUID

from pulse.bot.dispatch import dispatch
from pulse.common import clock
from pulse.common.clock import IST, utcnow
from pulse.demo.memory_client import MemoryTelegramClient
from pulse.team.dev_seed import DEV_TEAM_PATH
from pulse.team.linking import create_link_token
from pulse.worker import runner
from pulse.worker.jobs import FOLLOWUP_FROM, FOLLOWUP_UNTIL, WorkerDeps

log = logging.getLogger(__name__)

TG_ID_BASE = 7_100_000          # invented Telegram ids: one per dev-team member, in dev_team.json order
SKIPS = {10: "10 minutes", 60: "1 hour", 1440: "1 day", 20160: "14 days"}
FOLLOWUP_SKIP = 20160


@dataclass(frozen=True)
class DemoMember:
    key: str
    member_id: UUID
    user_id: str
    display_name: str
    tg_id: int
    is_principal: bool


class DemoHub:
    def __init__(self, state) -> None:
        self.state = state                                  # pulse.api.main.AppState
        if not isinstance(state.telegram, MemoryTelegramClient):
            raise RuntimeError("the demo needs the in-memory Telegram client")
        self.client: MemoryTelegramClient = state.telegram
        self.deps = WorkerDeps(pool=state.pool, client=state.telegram, core=state.core, storage=state.storage,
                               ledger_limits=state.settings.ledger, report_limits=state.settings.reports,
                               worker_id="demo", retention=state.settings.retention,
                               asr_engine=state.settings.asr_engine)
        with state.pool.connection() as conn:
            last = conn.execute("SELECT max(update_id) AS n FROM field.tg_update").fetchone()["n"] or 0
        self._update_ids = itertools.count(last + 1)
        self._callback_ids = itertools.count(1)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.background_worker = True       # tests turn it off and rely on drain() for determinism
        self.members: dict[str, DemoMember] = {}
        self.reload_members()

    # ---- members -----------------------------------------------------------------------------

    def reload_members(self) -> None:
        team = json.loads(DEV_TEAM_PATH.read_text(encoding="utf-8"))["members"]
        with self.state.pool.connection() as conn:
            rows = {r["user_id"]: r for r in conn.execute(
                "SELECT member_id, user_id, display_name, is_principal FROM field.member WHERE active")}
        self.members = {}
        for i, spec in enumerate(team):
            row = rows.get(spec["user_id"])
            if row is not None:
                self.members[spec["key"]] = DemoMember(spec["key"], row["member_id"], row["user_id"],
                                                       row["display_name"], TG_ID_BASE + i, row["is_principal"])

    def member(self, key: str) -> DemoMember:
        try:
            return self.members[key]
        except KeyError:
            raise LookupError(f"unknown demo member {key!r}") from None

    def link_all(self) -> None:
        """Link every member's invented Telegram account the real way: a link token and /start."""
        ac_id = self.state.core.list_acs()[0].ac_id
        for m in self.members.values():
            with self.state.pool.connection() as conn, conn.transaction():
                token = create_link_token(conn, ac_id, m.member_id, ttl=timedelta(hours=1)).token
            self.send_text(m.key, f"/start {token}", show=False)

    # ---- updates -----------------------------------------------------------------------------

    def _person(self, m: DemoMember) -> dict[str, Any]:
        return {"id": m.tg_id, "is_bot": False, "first_name": "Demo", "language_code": "hi"}

    def _message(self, m: DemoMember, **content: Any) -> dict[str, Any]:
        mid = self.client.next_message_id()
        return {"message_id": mid, "date": int(utcnow().timestamp()), "chat": {"id": m.tg_id, "type": "private"},
                "from": self._person(m), **content}

    def _dispatch(self, update_body: dict[str, Any]) -> str:
        outcome = dispatch(self.state.pool, self.state.bot, {"update_id": next(self._update_ids), **update_body})
        self.drain()
        return outcome

    def send_text(self, key: str, text: str, *, show: bool = True) -> str:
        m = self.member(key)
        msg = self._message(m, text=text)
        if show:
            self.client.record_user_message(m.tg_id, msg["message_id"], text=text)
        return self._dispatch({"message": msg})

    def send_file(self, key: str, data: bytes, *, mime: str, name: str, caption: str | None = None,
                  duration: int | None = None) -> str:
        """A file from the browser, shaped as Telegram would deliver it: JPEG as a photo, other images
        and PDFs as documents, OGG as a voice note, other audio as audio."""
        m = self.member(key)
        file_id, unique = self.client.add_file(data, mime)
        obj = {"file_id": file_id, "file_unique_id": unique, "file_size": len(data)}
        mime = (mime or "").lower()
        if mime == "image/jpeg":
            content: dict[str, Any] = {"photo": [obj]}
            shown = {"photo": file_id}
        elif mime.startswith("audio/"):
            tg_kind = "voice" if mime in ("audio/ogg", "audio/opus") else "audio"
            content = {tg_kind: {**obj, "duration": duration, "mime_type": mime}}
            shown = {"audio": file_id, "audio_mime": mime, "duration": duration}
        else:
            content = {"document": {**obj, "file_name": name, "mime_type": mime}}
            shown = {"photo": file_id} if mime.startswith("image/") else {"document": name}
        if caption:
            content["caption"] = caption
        msg = self._message(m, **content)
        self.client.record_user_message(m.tg_id, msg["message_id"], text=caption, **shown)
        return self._dispatch({"message": msg})

    def press(self, key: str, message_id: int, data: str) -> str:
        m = self.member(key)
        shown = self.client.message(m.tg_id, message_id) or {}
        cq_id = f"demo-cq-{next(self._callback_ids)}"
        self.client.expect_callback(cq_id, m.tg_id)
        message = {"message_id": message_id, "date": int(utcnow().timestamp()),
                   "chat": {"id": m.tg_id, "type": "private"}, "text": shown.get("text") or ""}
        return self._dispatch({"callback_query": {"id": cq_id, "from": self._person(m), "message": message,
                                                  "chat_instance": "demo", "data": data}})

    # ---- worker and clock --------------------------------------------------------------------

    def drain(self, now: datetime | None = None, rounds: int = 20) -> None:
        """Run the queue until nothing is ready, so replies and card edits show at once."""
        for _ in range(rounds):
            if not runner.run_queue(self.deps, now, batch=50):
                break

    def tick(self) -> None:
        runner.run_timed(self.deps)
        self.drain()

    def skip(self, minutes: int) -> dict[str, Any]:
        if minutes not in SKIPS:
            raise ValueError("skip one of " + ", ".join(SKIPS.values()))
        clock.advance(timedelta(minutes=minutes))
        snapped = False
        if minutes == FOLLOWUP_SKIP:            # follow-ups are only asked 10:00-20:00 IST
            local = utcnow().astimezone(IST)
            if not FOLLOWUP_FROM <= local.time() < FOLLOWUP_UNTIL:
                day = local.date() if local.time() < FOLLOWUP_FROM else local.date() + timedelta(days=1)
                target = datetime.combine(day, time(10, 5), tzinfo=IST)
                clock.advance(target - local)
                snapped = True
        for _ in range(3):                      # timed jobs, then whatever they queued, then follow-ons
            self.tick()
        return {"now": utcnow().isoformat(), "snapped_to_followup_window": snapped}

    def start_worker(self) -> None:
        def loop() -> None:
            runner.run_forever(self.deps, should_stop=self._stop.is_set, idle_sleep=1.0)
        self._thread = threading.Thread(target=loop, name="demo-worker", daemon=True)
        self._thread.start()

    def stop_worker(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10)
