"""worker/: the schedule, the job registry, the runner and the CLI.

Nothing tested the worker. A job name in SCHEDULE that is not in REGISTRY, or a
wrapper importing a function that was renamed, only fails at 03:00 in the
container - these pin both without running a job.
"""

from __future__ import annotations

import ast
import contextlib
import importlib
import inspect

import pytest

from worker import jobs, scheduler


def test_every_scheduled_job_is_registered_and_every_job_is_scheduled():
    scheduled = {name for name, _ in scheduler.SCHEDULE}
    assert scheduled == set(jobs.REGISTRY)


def test_every_job_wrapper_imports_something_that_exists():
    for name, fn in jobs.REGISTRY.items():
        tree = ast.parse(inspect.getsource(fn))
        imports = [n for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
        assert imports, f"{name} imports nothing"
        for node in imports:
            module = importlib.import_module(node.module)
            for alias in node.names:
                assert hasattr(module, alias.name), f"{name}: {node.module}.{alias.name} is gone"


def test_the_scheduler_registers_each_job_once_with_catch_up_rules(monkeypatch):
    monkeypatch.setenv("TZ", "Asia/Kolkata")
    sched = scheduler.build_scheduler()
    try:
        ids = sorted(job.id for job in sched.get_jobs())
        assert ids == sorted(name for name, _ in scheduler.SCHEDULE)
        assert sched._job_defaults["coalesce"] is True
        assert sched._job_defaults["max_instances"] == 1
        assert sched._job_defaults["misfire_grace_time"] == scheduler.MISFIRE_GRACE_SECONDS
    finally:
        with contextlib.suppress(Exception):
            sched.shutdown(wait=False)


class _FakeJob:
    def __init__(self):
        self.fields, self.lines = {}, []

    def set(self, **kw):
        self.fields.update(kw)

    def log_line(self, line):
        self.lines.append(line)


@pytest.fixture
def no_db(monkeypatch):
    made = []

    @contextlib.contextmanager
    def fake_context(name, **_):
        job = _FakeJob()
        made.append((name, job))
        yield job

    monkeypatch.setattr(jobs, "job_context", fake_context)
    return made


def test_a_failing_job_reports_its_error_and_does_not_raise(no_db, monkeypatch):
    def boom():
        raise RuntimeError("portal unreachable")

    monkeypatch.setitem(jobs.REGISTRY, "news.crawl", boom)
    assert jobs.run_job("news.crawl") == {"error": "portal unreachable"}


def test_a_successful_job_records_its_scalar_results(no_db, monkeypatch):
    monkeypatch.setitem(jobs.REGISTRY, "ops.usage_report",
                        lambda: {"rows": 3, "detail": {"nested": True}, "ok": True})
    result = jobs.run_job("ops.usage_report")
    assert result["rows"] == 3
    name, job = no_db[0]
    assert name == "ops.usage_report"
    assert job.fields == {"rows": 3, "ok": True}       # nested values are not columns


def test_an_unknown_job_is_a_key_error():
    with pytest.raises(KeyError, match="unknown job"):
        jobs.run_job("no.such.job")


def test_the_cli_lists_jobs_and_rejects_unknown_ones(capsys):
    from worker import run

    assert run.main(["--list"]) == 0
    assert "news.crawl" in capsys.readouterr().out
    assert run.main(["no.such.job"]) == 2
