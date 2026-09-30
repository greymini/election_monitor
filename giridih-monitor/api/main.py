"""FastAPI application (LLD 9).

    uvicorn api.main:app --host 0.0.0.0 --port 8000 --workers 2
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.routers import acs, admin, auth, data, legacy, news, scenario
from common.config import get_settings
from common.db import close_pools, query, query_one
from common.logging_setup import get_logger, setup_logging
from common.secrets import check_jwt_secret

log = get_logger(__name__)

# Stamped at import. The frontend reads it from /config so a stale cached bundle
# is identifiable: if the version chip does not move after a deploy, the browser
# is still running the old build.
BUILD_TIME = datetime.now(UTC).isoformat(timespec="seconds")


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    settings = get_settings()

    # E1: refuse to start rather than log and carry on. The previous code logged
    # "every authenticated request will fail", which was wrong in the dangerous
    # direction - HS256 verification with an empty key succeeds, so an unset
    # secret was an unauthenticated admin login, not a broken one. Raising here
    # means uvicorn exits; the module still imports, so the app stays
    # introspectable.
    check_jwt_secret(settings.jwt_secret)

    log.info("starting API (auth_mode=%s, chat=%s, analysis model=%s)",
             settings.auth_mode, settings.chat_enabled, settings.model_analysis)
    yield
    close_pools()


app = FastAPI(
    title="Giridih AC-32 Election Monitor",
    description=(
        "Booth-level election analytics for the Giridih assembly constituency, Jharkhand. "
        "Holds no individual voter records: electoral rolls are parsed into per-booth counts "
        "and the names discarded. Community figures are estimates and carry a confidence score. "
        "Review every figure before relying on it."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins or ["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception) -> JSONResponse:
    """Never leak an internal error to the client; log it in full instead."""
    log.exception("unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal error. This has been logged."},
    )


@app.get("/health", tags=["ops"])
def health(response: Response) -> dict:
    """Liveness plus a database probe.

    Returns 503 when the database is unreachable, not 200. It used to return 200
    either way with a "degraded" string in the body, which meant the compose
    healthcheck - and any load balancer or uptime monitor doing the ordinary
    thing of looking at the status code - reported the service healthy while
    every data route was timing out. An API that cannot reach the database
    cannot serve a single page, so it is not healthy.
    """
    try:
        query_one("SELECT 1 AS ok")
        db_ok = True
        detail = None
    except Exception as exc:
        log.warning("health check: database unreachable (%s)", exc)
        db_ok = False
        # The class name, not the message: a psycopg error can carry the host,
        # the database name and the user, and /health is unauthenticated.
        detail = type(exc).__name__

    if not db_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {
        "status": "ok" if db_ok else "unavailable",
        "database": db_ok,
        "error": detail,
    }


@app.get("/config", tags=["ops"])
def config() -> dict:
    """Boot-time configuration the frontend needs before it has a token.

    Read once at startup so the UI does not have to guess at server state:
    without this the chat panel has to be hidden by rebuilding the frontend
    rather than by setting an environment variable.

    Deliberately unauthenticated and deliberately small. It exposes feature
    flags and build metadata only - no connection strings, no key material, no
    model names.
    """
    settings = get_settings()
    # The constituency list is here as well as at /acs because the frontend
    # needs it before it has a token: the AC switcher sits in the header, which
    # renders on the login screen. Identity and the verified flag only, never a
    # figure.
    try:
        acs_list = query(
            "SELECT ac_number, name_en, name_hi, reservation, verified "
            "FROM ac WHERE is_active ORDER BY ac_number"
        )
    except Exception as exc:
        # /config has to answer even with the database down, or the frontend
        # cannot boot far enough to show why it is broken.
        log.warning("/config could not list constituencies (%s)", type(exc).__name__)
        acs_list = []
    return {
        "chat_enabled": settings.chat_enabled,
        "acs": acs_list,
        "default_ac": acs_list[0]["ac_number"] if acs_list else None,
        "version": app.version,
        "build_time": BUILD_TIME,
    }


app.include_router(auth.router)
app.include_router(acs.router)
app.include_router(data.router)
app.include_router(news.router)
app.include_router(scenario.router)
app.include_router(admin.router)

# Registered last so the scoped routes above always win a path match. These are
# 308 redirects from the pre-multi-AC paths and come out one release later.
app.include_router(legacy.router)

# A5: the chat feature is parked. It used to be impossible to turn off without
# editing source, and mounting it pulled `chatbot` -> `chatbot.sql_guard` ->
# `import sqlglot` into the API's import graph at startup. Both the import and
# the routes are now conditional, so with CHAT_ENABLED=false the routes are not
# merely hidden, they do not exist, and `sqlglot` and `anthropic` need not be
# installed at all.
if settings.chat_enabled:
    from api.routers import chat

    app.include_router(chat.router)
    log.info("chat routes mounted (CHAT_ENABLED=true)")
else:
    log.info("chat is disabled (CHAT_ENABLED=false); /chat routes are not mounted")
