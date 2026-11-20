"""Browser demo of both modules, without Telegram. Dev-only (docs/DEMO.md).

    python -m scripts.demo [--port 8787]

Builds the web app (web/; needs Node.js the first time) and serves it at /, with the chat simulator
for developers at /demo. Starts the demo's own embedded PostgreSQL in .demo-pgdata/ (separate from
.pgdata/), recreates the pulse_demo database, migrates, seeds the dev team and the sample data, and
serves the API with the worker running in the same process. Needs no .env: DEMO_MODE=1 makes
throwaway secrets in memory. Everything is rebuilt on each run; Ctrl+C stops the server and the
database.
"""
from __future__ import annotations

import argparse
import logging
import os
import shutil
import socket
import subprocess
import sys

import pgserver
import psycopg
import uvicorn

from pulse.config import DEMO_MEDIA_DIR, DEMO_PGDATA, REPO_ROOT, ConfigError, load_settings
from pulse.db import connect
from pulse.migrate import apply_migrations
from pulse.seed import apply_seeds

DB_NAME = "pulse_demo"
WEB = REPO_ROOT / "web"


def _free_port(preferred: int) -> int:
    for port in (preferred, 0):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                continue
            return s.getsockname()[1]
    raise RuntimeError("no free port")


def _build_web() -> bool:
    """Build the web app when needed: `npm ci` once, `npm run build` when a source is newer than the
    build. Returns False when there is no build (no Node.js): the API and /demo still run."""
    dist = WEB / "dist" / "index.html"
    npm = shutil.which("npm")
    if npm is None:
        print("Node.js / npm not found: the web app is not built. The API and /demo still work.")
        return dist.exists()
    if not (WEB / "node_modules").exists():
        print("Installing web dependencies (npm ci) ...")
        subprocess.run([npm, "ci", "--no-audit", "--no-fund"], cwd=WEB, check=True)
    sources = [p for p in (WEB / "src").rglob("*") if p.is_file()]
    sources += [WEB / n for n in ("index.html", "package.json", "vite.config.ts", "tailwind.config.js")]
    newest = max(p.stat().st_mtime for p in sources if p.exists())
    if not dist.exists() or dist.stat().st_mtime < newest:
        print("Building the web app (npm run build) ...")
        subprocess.run([npm, "run", "build"], cwd=WEB, check=True)
    return True


def _fresh_database() -> str:
    server = pgserver.get_server(DEMO_PGDATA, cleanup_mode="stop")
    with psycopg.connect(server.get_uri(), autocommit=True) as admin:
        admin.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s", (DB_NAME,))
        admin.execute(f'DROP DATABASE IF EXISTS "{DB_NAME}"')
        admin.execute(f'CREATE DATABASE "{DB_NAME}"')
    url = server.get_uri(DB_NAME)
    from pulse.core_adapter.stub import StubCoreAdapter
    from pulse.team.dev_seed import seed_team

    with connect(url) as conn:
        apply_migrations(conn)
        apply_seeds(conn)
        with conn.transaction():
            core = StubCoreAdapter()
            ids = seed_team(conn, core)
            _extra_members(conn, core, ids)
    return url


def _extra_members(conn, core, ids) -> None:
    """A coordinator and a treasurer for the web app's "view as" switch (demo data only)."""
    import json

    from pulse.demo.sample import SAMPLE_PATH
    from pulse.team.service import Scope, create_member

    for m in json.loads(SAMPLE_PATH.read_text(encoding="utf-8")).get("extra_members", []):
        create_member(conn, core, "ac-32", user_id=m["user_id"], display_name=m["display_name"],
                      parent_member_id=ids[m["parent"]], is_coordinator=m["is_coordinator"],
                      finance_scope=m["finance_scope"], can_give_advance=m["can_give_advance"],
                      scopes=[Scope(block_id=s.get("block_id"), area_id=s.get("area_id")) for s in m["scopes"]])


def _setup(base_url: str, built: bool):
    def setup(hub) -> None:
        from pulse.demo import sample

        hub.link_all()
        ac_id = hub.state.core.list_acs()[0].ac_id
        got = sample.load(hub.state.pool, hub.state.core, hub.client,
                          {k: m.member_id for k, m in hub.members.items()},
                          limits=hub.state.settings.ledger, drain=hub.drain, ac_id=ac_id)
        hub.tick()                    # reminders, flags and escalations the sample history is due
        hub.client.clear()            # panes start empty; the history is in the screens
        print(f"Sample data: {got.expenses} expenses, {got.advances} advances, {got.returns} returns, "
              f"{got.reports} reports, {got.issues} issues, {got.followups_answered} answered follow-up.")
        print()
        if built:
            print(f"  Election Monitor, field screens:  {base_url}")
        print(f"  Chat simulator (developers):      {base_url}demo")
        print()
    return setup


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="demo")
    p.add_argument("--port", type=int, default=8787)
    p.add_argument("--no-build", action="store_true", help="serve web/dist as it is")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    built = (WEB / "dist" / "index.html").exists() if args.no_build else _build_web()
    print("Starting the demo database (.demo-pgdata) ...")
    url = _fresh_database()
    if DEMO_MEDIA_DIR.exists():
        shutil.rmtree(DEMO_MEDIA_DIR)         # media from the previous run (the demo's own folder only)
    os.environ["DEMO_MODE"] = "1"
    os.environ["DATABASE_URL"] = url
    try:
        settings = load_settings()
    except ConfigError as e:
        print(e, file=sys.stderr)
        return 2

    from pulse.api.main import create_app

    port = _free_port(args.port)
    base = f"http://127.0.0.1:{port}/"
    try:
        app = create_app(settings, demo_setup=_setup(base, built))
    except ConfigError as e:
        print(e, file=sys.stderr)
        return 2
    print("Loading sample data ...")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
