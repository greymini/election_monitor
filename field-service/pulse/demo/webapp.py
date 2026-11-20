"""Serve the built web app (web/dist) at / in demo mode, with a single-page-app fallback.

Dev-only, like the rest of pulse/demo: in the merged product the core app serves the pages. Mounted
last, so the API (/acs/...), the chat simulator (/demo), /telegram and /healthz keep their routes.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from pulse.config import REPO_ROOT

DIST = REPO_ROOT / "web" / "dist"
RESERVED = ("acs/", "demo", "telegram/", "healthz", "health", "docs", "openapi.json")


def mount(app: FastAPI, dist: Path = DIST) -> bool:
    """Returns False (and mounts nothing) when the app has not been built."""
    index = dist / "index.html"
    if not index.exists():
        return False
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="web-assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        if path.startswith(RESERVED):
            raise HTTPException(404, "not found")
        file = (dist / path).resolve()
        if path and file.is_file() and dist.resolve() in file.parents:
            return FileResponse(file)
        return FileResponse(index, headers={"Cache-Control": "no-store"})

    return True
