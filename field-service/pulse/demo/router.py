"""Routes under /demo. Mounted by pulse/api/main.py only when settings.demo_mode is set."""
from __future__ import annotations

import base64
import binascii
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from pulse.common import clock
from pulse.common.clock import utcnow
from pulse.demo.hub import SKIPS, DemoHub

STATIC = Path(__file__).resolve().parent / "static"
ASSETS = {"demo.js": "text/javascript", "demo.css": "text/css"}
MAX_UPLOAD = 20 * 1024 * 1024

router = APIRouter(prefix="/demo", include_in_schema=False)


def hub(request: Request) -> DemoHub:
    h = getattr(request.app.state, "demo", None)
    if h is None:
        raise HTTPException(503, "demo is starting")
    return h


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MessageIn(Strict):
    member: str
    text: str = Field(min_length=1, max_length=4096)


class CallbackIn(Strict):
    member: str
    message_id: int
    data: str = Field(min_length=1, max_length=64)


class UploadIn(Strict):
    member: str
    name: str = Field(max_length=200)
    mime: str = Field(max_length=100)
    data_b64: str
    caption: str | None = Field(default=None, max_length=1024)
    duration: int | None = Field(default=None, ge=0, le=36_000)


class SkipIn(Strict):
    minutes: int


def _chat(h: DemoHub, key: str, outcome: str | None = None) -> dict:
    try:
        m = h.member(key)
    except LookupError as e:
        raise HTTPException(404, str(e)) from None
    return {"outcome": outcome, "now": utcnow().isoformat(), **h.client.chat(m.tg_id)}


def _act(h: DemoHub, key: str, fn) -> dict:
    try:
        h.member(key)
        outcome = fn()
    except LookupError as e:
        raise HTTPException(404, str(e)) from None
    return _chat(h, key, outcome)


@router.get("")
def page() -> FileResponse:
    return FileResponse(STATIC / "index.html", media_type="text/html", headers={"Cache-Control": "no-store"})


@router.get("/assets/{name}")
def asset(name: str) -> FileResponse:
    if name not in ASSETS:
        raise HTTPException(404, "not found")
    return FileResponse(STATIC / name, media_type=ASSETS[name], headers={"Cache-Control": "no-store"})


@router.get("/api/state")
def state(h: DemoHub = Depends(hub)) -> dict:
    principal = next(m for m in h.members.values() if m.is_principal)
    ac = h.state.core.list_acs()[0]
    return {"now": utcnow().isoformat(), "offset_minutes": int(clock.offset().total_seconds() // 60),
            "ac_number": ac.ac_number, "token": h.state.core.issue_token(principal.user_id),
            "skips": [{"minutes": k, "label": v} for k, v in SKIPS.items()],
            "members": [{"key": m.key, "name": m.display_name, "principal": m.is_principal}
                        for m in h.members.values()]}


class TokenIn(Strict):
    user_id: str = Field(min_length=1, max_length=100)


def _persona(m: dict) -> str:
    if m["is_principal"]:
        return "principal"
    if m["is_coordinator"]:
        return "coordinator"
    if m["finance_scope"] == "all":
        return "treasurer"
    return "incharge" if m["can_give_advance"] else "worker"


@router.get("/api/dev-users")
def dev_users(h: DemoHub = Depends(hub)) -> dict:
    """Who the web app's dev sign-in and "view as" switch can be: one row per active member."""
    with h.state.pool.connection() as conn:
        rows = conn.execute(
            """SELECT user_id, display_name, is_principal, is_coordinator, finance_scope, can_give_advance
               FROM field.member WHERE active ORDER BY is_principal DESC, created_at""").fetchall()
    return {"users": [{"user_id": r["user_id"], "name": r["display_name"], "persona": _persona(r),
                       "finance_scope": r["finance_scope"]} for r in rows]}


@router.post("/api/token")
def token(body: TokenIn, h: DemoHub = Depends(hub)) -> dict:
    """A stub-core bearer token for a demo user (the core's sign-in replaces this at merge)."""
    try:
        return {"token": h.state.core.issue_token(body.user_id)}
    except KeyError:
        raise HTTPException(404, "unknown user") from None


@router.get("/api/chat/{key}")
def chat(key: str, h: DemoHub = Depends(hub)) -> dict:
    return _chat(h, key)


@router.post("/api/message")
def message(body: MessageIn, h: DemoHub = Depends(hub)) -> dict:
    return _act(h, body.member, lambda: h.send_text(body.member, body.text))


@router.post("/api/callback")
def callback(body: CallbackIn, h: DemoHub = Depends(hub)) -> dict:
    return _act(h, body.member, lambda: h.press(body.member, body.message_id, body.data))


@router.post("/api/upload")
def upload(body: UploadIn, h: DemoHub = Depends(hub)) -> dict:
    try:
        data = base64.b64decode(body.data_b64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(422, "data_b64 is not base64") from None
    if not data or len(data) > MAX_UPLOAD:
        raise HTTPException(413, "file is empty or larger than 20 MB")
    return _act(h, body.member, lambda: h.send_file(body.member, data, mime=body.mime, name=body.name,
                                                    caption=body.caption, duration=body.duration))


@router.get("/api/file/{file_id}")
def file(file_id: str, h: DemoHub = Depends(hub)) -> Response:
    data = h.client.file(file_id)
    if data is None:
        raise HTTPException(404, "not found")
    return Response(data, media_type=h.client.file_mime(file_id), headers={"Cache-Control": "private, no-store",
                                                    "X-Content-Type-Options": "nosniff"})


@router.post("/api/skip")
def skip(body: SkipIn, h: DemoHub = Depends(hub)) -> dict:
    try:
        return h.skip(body.minutes)
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
