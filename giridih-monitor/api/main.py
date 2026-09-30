"""FastAPI application (LLD 9).

    uvicorn api.main:app --host 0.0.0.0 --port 8000 --workers 2
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.routers import admin, auth, chat, data, news, scenario
from common.config import get_settings
from common.db import close_pools, query_one
from common.logging_setup import get_logger, setup_logging

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    settings = get_settings()
    log.info("starting API (auth_mode=%s, analysis model=%s)",
             settings.auth_mode, settings.model_analysis)
    if not settings.jwt_secret:
        log.error("JWT_SECRET is not set - every authenticated request will fail")
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
def health() -> dict:
    try:
        query_one("SELECT 1 AS ok")
        db_ok = True
    except Exception as exc:
        log.warning("health check: database unreachable (%s)", exc)
        db_ok = False
    return {"status": "ok" if db_ok else "degraded", "database": db_ok}


app.include_router(auth.router)
app.include_router(data.router)
app.include_router(news.router)
app.include_router(chat.router)
app.include_router(scenario.router)
app.include_router(admin.router)
