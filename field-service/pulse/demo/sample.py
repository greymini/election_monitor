"""Load db/seeds/demo_sample.json through the real services (the ledger service stays the only writer
of field.money_txn). Entries are back-dated by passing now= to each service call.

Order: money in (inflows, advances, returns), expenses, reports (media downloaded and transcribed by
the worker, one report at a time so each voice note gets its own fake transcript), issues, report
statuses, then the expenses linked to reports and issues, with their follow-up.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable
from uuid import UUID

from psycopg_pool import ConnectionPool

from pulse.asr import fake as fake_asr
from pulse.common.clock import utcnow
from pulse.config import LedgerLimits
from pulse.core_adapter import CoreAdapter
from pulse.db import transaction
from pulse.demo import placeholders
from pulse.demo.memory_client import MemoryTelegramClient
from pulse.ledger import buckets, followup, register
from pulse.ledger import service as ledger
from pulse.reports import access, issues, media, triage
from pulse.reports import service as reports
from pulse.seed import SEEDS_DIR

SAMPLE_PATH = SEEDS_DIR / "demo_sample.json"


@dataclass
class Loaded:
    inflows: int = 0
    advances: int = 0
    returns: int = 0
    expenses: int = 0
    reports: int = 0
    issues: int = 0
    followups_answered: int = 0
    windows: int = 0


def _at(now: datetime, spec: dict[str, Any] | list) -> datetime:
    if isinstance(spec, list):
        return now - timedelta(days=spec[4])
    return now - timedelta(days=spec.get("days_ago", 0), hours=spec.get("hours_ago", 0))


def _block_of(core: CoreAdapter, ac_id: str, area_id: str) -> str:
    return next(a.block_id for a in core.list_areas(ac_id) if a.area_id == area_id)


def load(pool: ConnectionPool, core: CoreAdapter, client: MemoryTelegramClient, members: dict[str, UUID], *,
         limits: LedgerLimits, drain: Callable[[datetime | None], None], ac_id: str, path: Path = SAMPLE_PATH,
         now: datetime | None = None) -> Loaded:
    """members: dev-team key -> member_id. drain(at) runs the worker queue at that time until it is empty."""
    data = json.loads(path.read_text(encoding="utf-8"))
    now = now or utcnow()
    out = Loaded()
    m = members
    hour = timedelta(hours=1)

    with transaction(pool) as conn:
        win = data.get("election_window")
        if win:
            register.create_window(conn, ac_id, starts_on=(now - timedelta(days=win["starts_days_ago"])).date(),
                                   ends_on=(now + timedelta(days=win["ends_in_days"])).date(),
                                   ceiling_amount=Decimal(win["ceiling_amount"]), cash_limit=Decimal(win["cash_limit"]),
                                   event_id=None, actor_user_id="usr-principal", actor_member_id=m["principal"])
            out.windows += 1
        for code, head in data.get("statutory_heads", {}).items():
            if not code.startswith("_"):
                b = buckets.bucket_by_code(conn, code)
                register.set_head(conn, ac_id, b["bucket_id"], head, actor_user_id="usr-principal",
                                  actor_member_id=m["principal"])
        for f in data["inflows"]:
            ledger.create_inflow(conn, core, m["principal"], to_member_id=m[f["to"]], amount=f["amount"],
                                 fund_source=f["fund_source"], remark=f.get("remark"), limits=limits,
                                 now=_at(now, f))
            out.inflows += 1
        for a in data["advances"]:
            t = _at(now, a)
            txn = ledger.create_advance(conn, core, m[a["from"]], to_member_id=m[a["to"]], amount=a["amount"],
                                        remark=a.get("remark"), limits=limits, now=t)
            if a["then"] == "ack":
                ledger.acknowledge(conn, txn["txn_id"], m[a["to"]], now=t + 2 * hour)
            elif a["then"] == "dispute":
                ledger.dispute(conn, txn["txn_id"], m[a["to"]], a["reason"], note=a.get("note"), now=t + 2 * hour)
            out.advances += 1
        for r in data["returns"]:
            t = _at(now, r)
            txn = ledger.create_return(conn, core, m[r["from"]], to_member_id=m[r["to"]], amount=r["amount"],
                                       remark=r.get("remark"), limits=limits, now=t)
            if r["then"] == "ack":
                ledger.acknowledge(conn, txn["txn_id"], m[r["to"]], now=t + 3 * hour)
            out.returns += 1
        for e in data["expenses"]:
            who, code, amount, area, _, remark, pay_mode = e
            _expense(conn, core, m[who], code, amount, area, remark, pay_mode, ac_id=ac_id, limits=limits,
                     now=_at(now, e))
            out.expenses += 1
        for e in data.get("other_labels", []):
            _expense(conn, core, m[e["member"]], "OTHER", e["amount"], e["area"], None, "cash", ac_id=ac_id,
                     limits=limits, now=now - timedelta(days=e["days_ago"]), custom_label=e["label"])
            out.expenses += 1

    report_ids: dict[str, UUID] = {}
    for r in data["reports"]:
        t = _at(now, r)
        report_ids[r["key"]] = _report(pool, core, client, m, r, ac_id=ac_id, now=t)
        if r.get("voice"):
            fake_asr.TEXT = r["voice"]
        drain(t + timedelta(minutes=12))       # download its media, transcribe its voice note
        out.reports += 1
    fake_asr.TEXT = data["asr_default_text"]

    with transaction(pool) as conn:
        boss = access.viewer_for(conn, m["principal"])
        issue_ids: dict[str, UUID] = {}
        for i in data["issues"]:
            row = issues.create(conn, core, boss, now=_at(now, i), title=i["title"],
                                from_report_id=report_ids[i["from_report"]])
            issue_ids[i["key"]] = row["issue_id"]
            out.issues += 1
        for r in data["reports"]:
            if r.get("issue") and r["key"] != next(i["from_report"] for i in data["issues"] if i["key"] == r["issue"]):
                issues.link(conn, boss, issue_ids[r["issue"]], report_ids[r["key"]], now=_at(now, r) + hour)
        for r in data["reports"]:
            _status(conn, boss, m, report_ids[r["key"]], r, start=_at(now, r), latest=now - timedelta(minutes=30))
        for e in data["linked_expenses"]:
            t = _at(now, e)
            txn = _expense(conn, core, m[e["member"]], e["bucket"], e["amount"], e["area"], e["remark"], "cash",
                           ac_id=ac_id, limits=limits, now=t, report_id=report_ids.get(e.get("report")),
                           issue_id=issue_ids.get(e.get("issue")))
            out.expenses += 1
            fu = e.get("followup")
            if fu:
                followup.mark_asked(conn, txn["txn_id"], now=now - timedelta(days=fu["asked_days_ago"]))
                if followup.answer(conn, txn["txn_id"], m[e["member"]], fu["answer"],
                                   now=now - timedelta(days=fu["answered_days_ago"])) == "answered":
                    out.followups_answered += 1
    drain(None)
    return out


def _expense(conn, core: CoreAdapter, member_id: UUID, code: str, amount: str, area_id: str, remark: str | None,
             pay_mode: str, *, ac_id: str, limits: LedgerLimits, now: datetime, report_id: UUID | None = None,
             issue_id: UUID | None = None, custom_label: str | None = None) -> dict[str, Any]:
    bucket = buckets.bucket_by_code(conn, code)
    if bucket is None:
        raise ValueError(f"demo sample: unknown bucket {code}")
    inp = ledger.ExpenseInput(amount=Decimal(amount), bucket_id=bucket["bucket_id"], block_id=_block_of(core, ac_id, area_id),
                              area_id=area_id, pay_mode=pay_mode, remark=remark, report_id=report_id,
                              issue_id=issue_id, custom_label=custom_label)
    return ledger.create_expense(conn, core, member_id, inp, limits=limits, now=now)


def _register(conn, client: MemoryTelegramClient, member: dict[str, Any], data: bytes, *, kind: str, tg_kind: str,
              mime: str, now: datetime, duration_s: int | None = None, size: tuple[int, int] | None = None):
    file_id, unique = client.add_file(data, mime)
    item = media.Item(tg_kind=tg_kind, kind=kind, file_id=file_id, file_unique_id=unique, file_size=len(data),
                      mime=mime, duration_s=duration_s, width=size[0] if size else None,
                      height=size[1] if size else None, media_group_id=None)
    return media.register(conn, member, item, now=now)


def _report(pool: ConnectionPool, core: CoreAdapter, client: MemoryTelegramClient, m: dict[str, UUID],
            r: dict[str, Any], *, ac_id: str, now: datetime) -> UUID:
    with transaction(pool) as conn:
        member = conn.execute("SELECT * FROM field.member WHERE member_id = %s", (m[r["reporter"]],)).fetchone()
        rid = reports.open_draft(conn, core, member, now=now)["report_id"]
        reports.set_area(conn, core, rid, _block_of(core, ac_id, r["area"]), r["area"], "chosen", actor=None, now=now)
        actor = member["member_id"]
        if r.get("note"):
            reports.add_note(conn, rid, r["note"], actor=actor, now=now)
        if r.get("loc"):
            reports.set_location(conn, rid, r["loc"][0], r["loc"][1], actor=actor, now=now)
        for rgb in r["photos"]:
            a = _register(conn, client, member, placeholders.png(tuple(rgb)), kind="photo", tg_kind="photo",
                          mime="image/png", now=now, size=(320, 240))
            reports.add_media(conn, rid, a["asset_id"], actor=actor, now=now)
        if r.get("voice"):
            a = _register(conn, client, member, placeholders.wav(), kind="voice", tg_kind="audio", mime="audio/wav",
                          now=now, duration_s=3)
            reports.add_media(conn, rid, a["asset_id"], actor=actor, now=now)
        filed_at = now + timedelta(minutes=2)
        if r["category"] is None:
            reports.autofile(conn, core, rid, now=now + timedelta(minutes=10))
        else:
            cat = conn.execute("SELECT category_id FROM field.report_category WHERE code = %s",
                               (r["category"],)).fetchone()
            reports.set_category(conn, rid, cat["category_id"], actor=actor, now=filed_at)
            if r["urgency"] == "urgent":
                reports.set_urgency(conn, rid, "urgent", source="user", actor=actor, now=filed_at)
            reports.file(conn, core, rid, how="urgent" if r["urgency"] == "urgent" else "user", actor=actor,
                         now=filed_at)
    return rid


def _status(conn, boss: access.Viewer, m: dict[str, UUID], rid: UUID, r: dict[str, Any], *, start: datetime,
            latest: datetime) -> None:
    status = r["status"]
    t = lambda hours: min(start + timedelta(hours=hours), latest)
    if status == "new":
        return
    if status in ("acknowledged", "closed_no_action"):
        triage.acknowledge(conn, boss, rid, now=t(1))
    if status in ("assigned", "in_progress", "resolved"):
        triage.assign(conn, boss, rid, m[r["assign"]], now=t(1))
    if status == "in_progress":
        worker = access.viewer_for(conn, m[r["assign"]])
        triage.start(conn, worker, rid, now=t(3))
    if status in ("resolved", "closed_no_action"):
        triage.close(conn, boss, rid, status, now=t(24 if status == "resolved" else 5), note=r.get("close_note"))
