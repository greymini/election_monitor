"""Browser demo of both modules without Telegram. Dev-only: nothing in this package ships.

Started by `python -m scripts.demo` (docs/DEMO.md). The app mounts /demo only when
settings.demo_mode is set, and refuses demo mode unless the database is the demo's own embedded
PostgreSQL. Delete this package, scripts/demo.py and the clock offset at merge (docs/MERGE.md).
"""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

import psycopg

from pulse.config import ConfigError

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", ""}


def _same_dir(a: str | Path, b: str | Path) -> bool:
    norm = lambda p: os.path.normcase(os.path.realpath(str(p)))
    return norm(a) == norm(b)


def assert_embedded(database_url: str, pgdata: Path) -> None:
    """Raise ConfigError unless database_url is a local server whose data directory is pgdata."""
    host = urlparse(database_url).hostname or ""
    if host not in LOCAL_HOSTS and not host.startswith("/"):
        raise ConfigError("Refusing DEMO_MODE: the database is not on this machine")
    try:
        with psycopg.connect(database_url, connect_timeout=5) as conn:
            data_dir = conn.execute("SHOW data_directory").fetchone()[0]
    except psycopg.Error as e:
        raise ConfigError(f"Refusing DEMO_MODE: cannot reach the database ({type(e).__name__})") from None
    if not _same_dir(data_dir, pgdata):
        raise ConfigError("Refusing DEMO_MODE: the database is not the demo's embedded PostgreSQL")


def start(app, setup=None) -> None:
    """Lifespan hook: allow the clock to move, build the hub, run setup(hub) (sample data), start the worker."""
    from pulse.common import clock
    from pulse.demo.hub import DemoHub

    clock.allow_offset()
    h = DemoHub(app.state.pulse)
    if setup is not None:
        setup(h)
    if h.background_worker:
        h.start_worker()
    app.state.demo = h


def stop(app) -> None:
    from pulse.common import clock

    h = getattr(app.state, "demo", None)
    if h is not None:
        h.stop_worker()
        app.state.demo = None
    clock.reset_offset()
