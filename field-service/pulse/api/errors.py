"""Generic error bodies: no stack traces, and validation errors never echo what was sent (it may
contain a phone number or a name)."""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

log = logging.getLogger(__name__)


def install(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def _invalid(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [{"loc": [str(p) for p in e.get("loc", ())], "msg": e.get("msg", "invalid")} for e in exc.errors()]
        return JSONResponse({"detail": "invalid request", "errors": errors}, status_code=422)

    @app.exception_handler(Exception)
    async def _crash(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse({"detail": "internal error"}, status_code=500)
