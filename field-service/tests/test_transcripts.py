"""Transcription (slice 3, LLD §6): the engine interface, the transcribe job after media_fetch,
labelled card updates, suggestions that never override a person, failures that keep the report, PII
masking, and the human correction preferred for display."""
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from pulse import asr
from pulse.api.main import create_app
from pulse.asr import fake
from pulse.bot import texts_hi as T
from pulse.common.clock import utcnow
from pulse.common.pii import MASK
from pulse.reports import media
from pulse.reports import service as reports
from pulse.worker.runner import run_queue
from tests.triage_world import alerts_for, category_id, file_report

BASE = "/acs/32/field/reports"


@pytest.fixture(autouse=True)
def fake_engine(monkeypatch):
    monkeypatch.setattr(fake, "TEXT", "डेमो: गाँव में चापाकल खराब है")
    monkeypatch.setattr(fake, "CONFIDENCE", Decimal("0.870"))
    monkeypatch.setattr(fake, "FAIL", False)
    fake.calls.clear()
    monkeypatch.setattr(asr, "convert", lambda src, workdir: (src, False))   # no ffmpeg in tests
    yield


@pytest.fixture
def asr_worker(worker):
    return replace(worker, asr_engine="fake")


def add_voice(conn, member_id, report_id, file_id="voice-1", fake_tg=None, data=b"OggS demo voice bytes"):
    member = conn.execute("SELECT * FROM field.member WHERE member_id = %s", (member_id,)).fetchone()
    item = media.Item(tg_kind="voice", kind="voice", file_id=file_id, file_unique_id=file_id, file_size=len(data),
                      mime="audio/ogg", duration_s=12, width=None, height=None, media_group_id=None)
    asset = media.register(conn, member, item, now=utcnow())
    reports.add_media(conn, report_id, asset["asset_id"], actor=member_id, now=utcnow(), after_filing=True)
    if fake_tg is not None:
        fake_tg.files[file_id] = data
    return asset["asset_id"]


def drain(deps, rounds=10):
    out = []
    for _ in range(rounds):
        batch = run_queue(deps, batch=100)
        if not batch:
            break
        out += batch
    return out


def _report(conn, rid):
    return conn.execute("SELECT * FROM field.report WHERE report_id = %s", (rid,)).fetchone()


def test_engine_modules():
    assert asr.get_engine("none") is None
    assert asr.get_engine("fake").name == "fake"
    with pytest.raises(ValueError):
        asr.get_engine("no_such_engine")
    with pytest.raises(ValueError):
        asr.get_engine("../etc")


def test_no_converter_passes_the_original(tmp_path, monkeypatch):
    import shutil

    from pulse.asr import base

    monkeypatch.setattr(shutil, "which", lambda name: None)
    src = tmp_path / "a.ogg"
    src.write_bytes(b"OggS")
    assert base.convert(src, tmp_path) == (src, False)


def test_transcript_arrives_after_filing_and_updates_cards(conn, core, world, asr_worker, fake_tg):
    r = file_report(conn, core, world.team["worker-a1"], urgency="urgent")
    world.drain()                                                      # alert cards sent without a transcript
    cards = alerts_for(conn, r["report_id"])
    assert cards and all(a["status"] == "sent" for a in cards)
    assert all(T.MACHINE_WRITTEN not in m["text"] for m in world.principal.mine)
    add_voice(conn, world.team["worker-a1"], r["report_id"], fake_tg=fake_tg)
    assert _report(conn, r["report_id"])["transcript_status"] == "none"   # nothing queued until stored
    drain(asr_worker)
    row = _report(conn, r["report_id"])
    assert row["transcript_status"] == "done" and row["transcript_engine"] == "fake"
    assert row["transcript_hi"] == fake.TEXT and row["transcript_conf"] == Decimal("0.870")
    assert len(fake.calls) == 1
    edited = [e for e in fake_tg.edits if T.MACHINE_WRITTEN in e["text"]]
    assert len(edited) == len(cards)                                   # every open card redrawn, labelled
    assert "(fake)" in edited[0]["text"] and fake.TEXT in edited[0]["text"]
    ev = conn.execute("SELECT data FROM field.report_event WHERE kind = 'transcribed'").fetchone()["data"]
    assert ev["engine"] == "fake" and ev["converted"] is False         # no converter: the original was passed


def test_card_shows_first_300_characters(conn, core, world, asr_worker, fake_tg, monkeypatch):
    monkeypatch.setattr(fake, "TEXT", "क" * 500)
    r = file_report(conn, core, world.team["worker-a1"], urgency="urgent")
    world.drain()
    add_voice(conn, world.team["worker-a1"], r["report_id"], fake_tg=fake_tg)
    drain(asr_worker)
    text = next(e["text"] for e in fake_tg.edits if T.MACHINE_WRITTEN in e["text"])
    assert "क" * 300 + "…" in text and "क" * 301 not in text


def test_engine_none_leaves_reports_untouched(conn, core, world, worker, fake_tg):
    r = file_report(conn, core, world.team["worker-a1"], urgency="urgent")
    world.drain()
    before = len(fake_tg.edits)
    add_voice(conn, world.team["worker-a1"], r["report_id"], fake_tg=fake_tg)
    drain(worker)                                                      # asr_engine none (the default)
    row = _report(conn, r["report_id"])
    assert row["transcript_status"] == "none" and row["transcript_hi"] is None
    assert conn.execute("SELECT count(*) AS n FROM field.job WHERE kind = 'transcribe'").fetchone()["n"] == 0
    assert conn.execute("SELECT count(*) AS n FROM field.report_transcript").fetchone()["n"] == 0
    assert len(fake_tg.edits) == before and fake.calls == []


def test_failed_transcription_keeps_the_report(conn, core, world, asr_worker, fake_tg, monkeypatch):
    monkeypatch.setattr(fake, "FAIL", True)
    r = file_report(conn, core, world.team["worker-a1"], urgency="urgent")
    world.drain()
    asset = add_voice(conn, world.team["worker-a1"], r["report_id"], fake_tg=fake_tg)
    t = utcnow()
    for i in range(4):                                                 # three attempts with backoff
        from datetime import timedelta
        run_queue(asr_worker, t + timedelta(hours=i), batch=100)
    row = _report(conn, r["report_id"])
    assert row["transcript_status"] == "failed" and row["transcript_hi"] is None
    assert row["status"] == "new" and row["urgency"] == "urgent"       # nothing else changed
    part = conn.execute("SELECT * FROM field.report_transcript WHERE asset_id = %s", (asset,)).fetchone()
    assert part["status"] == "failed" and "fake engine" in part["error"]
    assert conn.execute("SELECT status FROM field.media_asset WHERE asset_id = %s", (asset,)).fetchone()["status"] == "stored"
    assert len(fake.calls) == 3


def test_mask_applied_to_transcripts(conn, core, world, asr_worker, fake_tg, monkeypatch):
    monkeypatch.setattr(fake, "TEXT", "मेरा नंबर 9876543210 है, आधार 2345 6789 0123")
    r = file_report(conn, core, world.team["worker-a1"])
    add_voice(conn, world.team["worker-a1"], r["report_id"], fake_tg=fake_tg)
    drain(asr_worker)
    row = _report(conn, r["report_id"])
    assert "9876543210" not in row["transcript_hi"] and "2345 6789 0123" not in row["transcript_hi"]
    assert row["transcript_hi"].count(MASK) == 2
    hit = conn.execute("SELECT count(*) AS n FROM field.report_transcript x WHERE row_to_json(x)::text LIKE '%%9876543210%%'"
                       ).fetchone()["n"]
    assert hit == 0
    assert conn.execute("SELECT data FROM field.report_event WHERE kind = 'transcribed'").fetchone()["data"]["masked"]


def test_suggestion_for_unsorted_never_overrides(conn, core, world, asr_worker, fake_tg, monkeypatch):
    monkeypatch.setattr(fake, "TEXT", "स्कूल में शिक्षक नहीं आते, आग लग गई")
    unsorted = file_report(conn, core, world.team["worker-a1"], category=None, how="auto", note="demo")
    chosen = file_report(conn, core, world.team["worker-a1"], category="SERVICES", note="demo")
    for i, r in enumerate((unsorted, chosen)):
        add_voice(conn, world.team["worker-a1"], r["report_id"], file_id=f"v{i}", fake_tg=fake_tg)
    drain(asr_worker)
    u, c = _report(conn, unsorted["report_id"]), _report(conn, chosen["report_id"])
    assert u["category_id"] is None                                    # still unsorted: only a suggestion
    assert u["suggested_source"] == "transcript_rule"
    assert c["category_id"] == category_id(conn, "SERVICES") and c["suggested_category_id"] is None
    assert u["urgency"] == "normal"                                    # urgency is suggested, never set


def test_human_correction_preferred(conn, core, world, asr_worker, fake_tg, settings, pool, storage):
    r = file_report(conn, core, world.team["worker-a1"], urgency="urgent")
    world.drain()
    add_voice(conn, world.team["worker-a1"], r["report_id"], fake_tg=fake_tg)
    drain(asr_worker)
    app = create_app(settings, pool=pool, core=core, telegram_client=fake_tg, storage=storage)
    with TestClient(app) as api:
        principal = {"Authorization": f"Bearer {core.issue_token('usr-principal')}"}
        reporter = {"Authorization": f"Bearer {core.issue_token('usr-worker-a1')}"}
        url = f"{BASE}/{r['report_id']}/transcript"
        assert api.post(url, json={"text": "सुधार"}, headers=reporter).status_code == 403
        res = api.post(url, json={"text": "गाँव में चापाकल 3 दिन से खराब है, फोन 9876543210"}, headers=principal)
        assert res.status_code == 200
        assert MASK in res.json()["human_text"] and res.json()["machine_text"] == fake.TEXT
        detail = api.get(f"{BASE}/{r['report_id']}", headers=principal).json()["transcript"]
    assert detail["source"] == "human" and detail["text"].startswith("गाँव में चापाकल 3 दिन")
    assert detail["machine_text"] == fake.TEXT and detail["label"] is None
    drain(asr_worker)
    last = [e["text"] for e in fake_tg.edits][-1]
    assert "जाँचा गया" in last and T.MACHINE_WRITTEN not in last
    assert conn.execute("SELECT actor_member_id FROM field.report_event WHERE kind = 'transcript_corrected'"
                        ).fetchone()["actor_member_id"] == world.team["principal"]
