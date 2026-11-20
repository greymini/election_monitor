"""media_fetch through the queue; retries and final failure; dedupe by sha256; two workers at once."""
from __future__ import annotations

import itertools
import threading
from dataclasses import replace
from datetime import timedelta

from pulse.bot import texts_hi as T
from pulse.common.clock import utcnow
from pulse.common.storage import key_for, sha256_hex
from pulse.worker import queue
from pulse.worker.runner import run_queue, run_timed
from tests import updates as U
from tests.helpers import link
from tests.test_flow_report import Reporter
from tests.test_storage_imagemeta import GPS, make_jpeg

_ids = itertools.count(500_000)


def _rep(pool, bot, team):
    link(pool, bot, team["worker-a1"])
    return Reporter(pool, bot)


def _asset(conn, file_id):
    return conn.execute("SELECT * FROM field.media_asset WHERE tg_file_id = %s", (file_id,)).fetchone()


def test_fetch_strips_metadata_and_stores(conn, pool, bot, team, worker, fake_tg, storage):
    rep = _rep(pool, bot, team)
    fake_tg.files["p1"] = make_jpeg()
    rep.photo("p1")
    done = run_queue(worker)
    assert done[0][1] == "stored"
    a = _asset(conn, "p1")
    assert a["status"] == "stored" and a["storage_key"] == key_for(a["sha256"])
    stored = storage.get(a["storage_key"])
    assert GPS not in stored and a["bytes"] == len(stored) and a["sha256"] == sha256_hex(stored)
    report_id = conn.execute("SELECT report_id FROM field.report").fetchone()["report_id"]
    assert conn.execute("SELECT count(*) AS n FROM field.report_event WHERE report_id = %s AND kind = 'media_stored'",
                        (report_id,)).fetchone()["n"] == 1
    assert conn.execute("SELECT status FROM field.job").fetchone()["status"] == "done"
    assert conn.execute("SELECT status FROM field.job_run WHERE job_name = 'media_fetch'").fetchone()["status"] == "ok"


def test_pdf_stored_unchanged(conn, pool, bot, team, worker, fake_tg, storage):
    rep = _rep(pool, bot, team)
    fake_tg.files["doc"] = b"%PDF-1.4 demo document"
    rep.send(U.document_msg(next(_ids), "doc", "application/pdf"))
    run_queue(worker)
    assert storage.get(_asset(conn, "doc")["storage_key"]) == b"%PDF-1.4 demo document"


def test_failed_download_retries_then_keeps_the_report(conn, pool, bot, team, worker, fake_tg):
    rep = _rep(pool, bot, team)
    fake_tg.fail_downloads.add("bad")
    rep.photo("bad")
    rep.tap("स्वास्थ्य")
    rep.tap(T.BTN_URGENT_NO)
    t = utcnow()
    fetch_id = conn.execute("SELECT job_id FROM field.job WHERE kind = 'media_fetch'").fetchone()["job_id"]
    for attempt in range(5):
        result = dict(run_queue(worker, t))
        assert result[fetch_id] == ("retry" if attempt < 4 else "failed")
        job = conn.execute("SELECT * FROM field.job WHERE job_id = %s", (fetch_id,)).fetchone()
        t = job["run_after"] + timedelta(seconds=1)                     # wait out the backoff
    assert job["status"] == "failed" and job["attempts"] == 5
    run_queue(worker, t)                                                # the notice goes out through the queue
    a = _asset(conn, "bad")
    assert a["status"] == "failed" and a["sha256"] is None
    r = conn.execute("SELECT * FROM field.report").fetchone()
    assert r["status"] == "new" and r["report_no"] == "32-R-000001"     # the report is kept
    kinds = [e["kind"] for e in conn.execute("SELECT kind FROM field.report_event ORDER BY event_id")]
    assert "media_failed" in kinds
    assert fake_tg.sent[-1]["text"] == T.MEDIA_FAILED.format(report_no="32-R-000001")
    statuses = [r["status"] for r in conn.execute(
        "SELECT status FROM field.job_run WHERE job_name = 'media_fetch' ORDER BY run_id")]
    assert statuses == ["retry"] * 4 + ["error"]


def test_backoff_grows():
    assert [queue.backoff(n).total_seconds() for n in (1, 2, 3, 4)] == [30, 60, 120, 240]


def test_same_bytes_are_stored_once(conn, pool, bot, team, worker, fake_tg, storage, tmp_path):
    rep = _rep(pool, bot, team)
    same = make_jpeg()
    fake_tg.files["d1"] = same
    fake_tg.files["d2"] = same                                          # forwarded again: new ids, same bytes
    rep.photo("d1", unique="uq-1")
    rep.photo("d2", unique="uq-2")
    run_queue(worker)
    a1, a2 = _asset(conn, "d1"), _asset(conn, "d2")
    assert a1["sha256"] == a2["sha256"] and a1["storage_key"] == a2["storage_key"]
    files = [p for p in (tmp_path / "media").rglob("*") if p.is_file()]
    assert len(files) == 1
    events = conn.execute("SELECT data FROM field.report_event WHERE kind = 'media_stored' ORDER BY event_id").fetchall()
    assert [e["data"]["deduplicated"] for e in events] == [False, True]


def test_same_telegram_file_is_not_downloaded_twice(conn, pool, bot, team, worker, fake_tg):
    rep = _rep(pool, bot, team)
    fake_tg.files["s1"] = make_jpeg()
    rep.photo("s1", unique="uq-same")
    run_queue(worker)
    rep.photo("s1", unique="uq-same", now=utcnow() + timedelta(minutes=30))   # sent again in a later report
    run_queue(worker)
    assert fake_tg.get_file_calls == ["s1"]


def test_two_workers_never_process_the_same_job(conn, pool, bot, team, worker, fake_tg):
    rep = _rep(pool, bot, team)
    for i in range(10):
        fake_tg.files[f"c{i}"] = make_jpeg() + bytes([i])
        rep.photo(f"c{i}", group="conc")
    for i in range(6):
        fake_tg.files[f"v{i}"] = b"OggS demo voice " + bytes([i])
        rep.send(U.voice_msg(next(_ids), f"v{i}", duration=10))
    barrier = threading.Barrier(2)
    results: list[list] = []

    def go(name):
        barrier.wait()
        results.append(run_queue(replace(worker, worker_id=name), batch=50))

    threads = [threading.Thread(target=go, args=(n,)) for n in ("w-a", "w-b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    processed = [job_id for batch in results for job_id, _ in batch]
    assert len(processed) == len(set(processed)) == 16
    assert sorted(fake_tg.get_file_calls) == sorted({*fake_tg.get_file_calls})   # each file fetched once
    assert conn.execute("SELECT count(*) AS n FROM field.job WHERE status = 'done'").fetchone()["n"] == 16
    by_worker = {r["locked_by"] for r in conn.execute("SELECT locked_by FROM field.job")}
    assert by_worker <= {"w-a", "w-b"}


def test_timed_job_runs_once_across_workers(conn, worker):
    calls = []

    def job(deps, now):
        calls.append(deps.worker_id)
        return {"ok": 1}

    timed = (("demo_timed", 60, job),)
    barrier = threading.Barrier(2)
    now = utcnow()

    def go(name):
        barrier.wait()
        run_timed(replace(worker, worker_id=name), now, timed=timed)

    threads = [threading.Thread(target=go, args=(n,)) for n in ("w-a", "w-b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(calls) == 1
    assert conn.execute("SELECT count(*) AS n FROM field.job_run WHERE job_name = 'demo_timed'").fetchone()["n"] == 1
    run_timed(worker, now + timedelta(seconds=30), timed=timed)
    assert len(calls) == 1                                                 # not due yet
    run_timed(worker, now + timedelta(seconds=61), timed=timed)
    assert len(calls) == 2


def test_stale_running_job_is_reclaimed(conn, worker):
    with worker.pool.connection() as c, c.transaction():
        queue.enqueue(c, "noop", {})
    now = utcnow()
    with worker.pool.connection() as c:
        assert queue.claim(c, "dead-worker", now=now)["status"] == "running"   # and then the worker dies
    with worker.pool.connection() as c:
        assert queue.claim(c, "w2", now=now + timedelta(minutes=5)) is None
        again = queue.claim(c, "w2", now=now + timedelta(minutes=11))
    assert again["locked_by"] == "w2" and again["attempts"] == 2
