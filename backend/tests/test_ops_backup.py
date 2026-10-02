"""worker.ops.backup keeps the database password off the command line (audit G2).

pg_dump was called with the full DATABASE_URL as an argument, so the password
was visible to every user on the host in `ps` output for the whole dump.
"""

from __future__ import annotations

import subprocess

import pytest

from worker import ops


def test_the_password_goes_in_the_environment_not_argv(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", "postgresql://giridih:s3cret-pw@db:5432/giridih")
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path))
    from common.config import get_settings
    get_settings.cache_clear()

    seen = {}

    def fake_run(args, **kwargs):
        seen["args"], seen["env"] = args, kwargs.get("env") or {}
        return subprocess.CompletedProcess(args, 0, stdout=b"-- dump", stderr=b"")

    monkeypatch.setattr(ops.shutil, "which", lambda _: "/usr/bin/pg_dump")
    monkeypatch.setattr(ops.subprocess, "run", fake_run)
    result = ops.backup()
    get_settings.cache_clear()

    assert "s3cret-pw" not in " ".join(map(str, seen["args"]))
    assert seen["env"].get("PGPASSWORD") == "s3cret-pw"
    assert result["file"].endswith(".sql.gz")


@pytest.mark.parametrize("script", ["create_admin.py"])
def test_admin_script_runs_as_a_file(script):
    """`python scripts/create_admin.py --help` failed on `import common`."""
    import sys

    from tests.paths import BACKEND

    out = subprocess.run([sys.executable, str(BACKEND / "scripts" / script), "--help"],
                         capture_output=True, text=True, cwd=BACKEND.parent)
    assert out.returncode == 0, out.stderr[-500:]
