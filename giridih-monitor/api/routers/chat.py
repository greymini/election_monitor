"""Chat endpoint (LLD 9). SSE so the panel streams rather than hangs."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from api.deps import CurrentUser
from chatbot.agent import ask
from common.logging_setup import get_logger

log = get_logger(__name__)
router = APIRouter(tags=["chat"])

MAX_HISTORY = 12


class Turn(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(max_length=8000)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    history: list[Turn] = Field(default_factory=list)


@router.post("/chat")
async def chat(body: ChatRequest, user: CurrentUser) -> EventSourceResponse:
    """Streams: status -> tool events -> answer -> done.

    The agent loop is synchronous and does its own network I/O, so it runs in a
    worker thread; the SSE generator only forwards events.
    """
    history = [t.model_dump() for t in body.history[-MAX_HISTORY:]]

    async def events():
        yield {"event": "status", "data": json.dumps({"state": "thinking"})}
        try:
            answer = await asyncio.to_thread(
                ask, body.message, history, user.user_id, user.role
            )
        except Exception as exc:
            log.exception("chat failed")
            yield {"event": "error", "data": json.dumps({
                "message": "The assistant could not answer that. The dashboard is unaffected.",
                "detail": str(exc)[:200],
            })}
            return

        for call in answer.tool_calls:
            yield {"event": "tool", "data": json.dumps({
                "name": call.name,
                "arguments": call.arguments,
                "is_error": call.is_error,
                "seconds": call.seconds,
                # The result body can be large; the panel shows a preview only.
                "preview": call.result[:600],
            }, ensure_ascii=False, default=str)}

        for chart in answer.charts:
            yield {"event": "chart", "data": json.dumps(chart, ensure_ascii=False, default=str)}

        yield {"event": "answer", "data": json.dumps({
            "text": answer.text,
            "intent": answer.intent,
            "lang": answer.lang,
            "model": answer.model,
            "rounds": answer.rounds,
            "truncated": answer.truncated,
            "degraded": answer.degraded,
            "notice": answer.notice,
            # The cost chip is shown to admins only (LLD 8.4).
            "cost_usd": round(answer.cost_usd, 5) if user.is_admin else None,
        }, ensure_ascii=False)}
        yield {"event": "done", "data": "{}"}

    return EventSourceResponse(events())


@router.post("/chat/sync")
async def chat_sync(body: ChatRequest, user: CurrentUser) -> dict:
    """Non-streaming variant, for scripts and tests."""
    history = [t.model_dump() for t in body.history[-MAX_HISTORY:]]
    answer = await asyncio.to_thread(ask, body.message, history, user.user_id, user.role)
    payload = asdict(answer)
    if not user.is_admin:
        payload.pop("cost_usd", None)
    return payload
