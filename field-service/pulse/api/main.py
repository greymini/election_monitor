"""FastAPI app factory.

    uvicorn pulse.api.main:create_app --factory

Dependencies (settings, pool, core adapter, Telegram client) can be injected for tests.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import FastAPI
from psycopg_pool import ConnectionPool

from pulse.api import errors, logfilter
from pulse.api.routers import (buckets_admin, health, issues, ledger, me, members, register, reports,
                               settings as settings_router, spend, team_activity, telegram)
from pulse.bot.client import HttpxTelegramClient, TelegramClient
from pulse.bot.ui import BotDeps
from pulse.common.storage import LocalDiskStorage, Storage
from pulse.config import Settings, load_settings
from pulse.core_adapter import CoreAdapter
from pulse.core_adapter.stub import StubCoreAdapter
from pulse.db import make_pool


@dataclass
class AppState:
    settings: Settings
    pool: ConnectionPool
    core: CoreAdapter
    telegram: TelegramClient
    bot: BotDeps
    storage: Storage


def make_core_adapter(settings: Settings) -> CoreAdapter:
    if settings.core_adapter == "stub":
        return StubCoreAdapter(settings.core_seed_path, jwt_secret=settings.jwt_secret)
    if settings.core_adapter == "core":
        from pulse.core_adapter.core import CoreDbAdapter
        return CoreDbAdapter(settings.core_database_url, settings.core_jwt_secret)
    raise ValueError(f"unknown core adapter {settings.core_adapter!r}")


def create_app(
    settings: Settings | None = None,
    *,
    pool: ConnectionPool | None = None,
    core: CoreAdapter | None = None,
    telegram_client: TelegramClient | None = None,
    storage: Storage | None = None,
    demo_setup: Callable[[Any], None] | None = None,
) -> FastAPI:
    settings = settings or load_settings()  # refuses placeholder secrets
    owns_pool = pool is None
    if settings.demo_mode:                   # dev-only browser demo (pulse/demo): never on another database
        from pulse import demo
        from pulse.demo.memory_client import MemoryTelegramClient

        demo.assert_embedded(settings.database_url, settings.demo_pgdata)
        telegram_client = telegram_client or MemoryTelegramClient()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        p = pool or make_pool(settings.database_url)
        tg = telegram_client or HttpxTelegramClient(settings.telegram_bot_token)
        c = core or make_core_adapter(settings)
        app.state.pulse = AppState(settings=settings, pool=p, core=c, telegram=tg,
                                   storage=storage or LocalDiskStorage(settings.media_dir),
                                   bot=BotDeps(client=tg, core=c, limits=settings.ledger,
                                               reports=settings.reports))
        if settings.demo_mode:
            demo.start(app, demo_setup)
        try:
            yield
        finally:
            if settings.demo_mode:
                demo.stop(app)
            if owns_pool:
                p.close()
            if telegram_client is None:
                tg.close()

    app = FastAPI(title="Project Pulse", lifespan=lifespan)
    errors.install(app)
    logfilter.install()                      # no query strings (media tokens) in access logs
    app.include_router(telegram.router)
    app.include_router(me.router)
    app.include_router(members.router)
    app.include_router(ledger.router)
    app.include_router(spend.router)
    app.include_router(register.router)
    app.include_router(buckets_admin.router)
    app.include_router(reports.router)
    app.include_router(issues.router)
    app.include_router(team_activity.router)
    app.include_router(settings_router.router)
    app.include_router(health.router)

    if settings.demo_mode:
        from pulse.demo.router import router as demo_router

        app.include_router(demo_router)

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True}

    if settings.demo_mode:                   # the built web app at /, last so the API keeps its routes
        from pulse.demo import webapp

        webapp.mount(app)
    return app
