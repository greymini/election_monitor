"""scripts/dev_stack.py helpers that need no database."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "dev_stack", Path(__file__).resolve().parents[1] / "scripts" / "dev_stack.py")
dev_stack = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(dev_stack)


@pytest.mark.parametrize("base, expected", [
    # Windows: pgserver listens on TCP.
    ("postgresql://postgres:@localhost:5432/postgres",
     "postgresql://postgres:@localhost:5432/giridih_dev"),
    # macOS / Linux: a Unix socket, its directory in the query string. The old
    # rsplit("/") replaced the last segment of the *socket path* instead.
    ("postgresql://postgres:@/postgres?host=/Users/x/repo/.devstack/pgdata",
     "postgresql://postgres:@/giridih_dev?host=/Users/x/repo/.devstack/pgdata"),
])
def test_database_url_replaces_only_the_database_name(base, expected):
    assert dev_stack.database_url(base, "giridih_dev") == expected
