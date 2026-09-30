"""Chat endpoint (LLD 9). SSE so the panel streams rather than hangs.

The feature is parked behind CHAT_ENABLED and api/main.py does not mount this
router when it is off. Even so, nothing from `chatbot` is imported at module
scope: that package reaches chatbot/sql_guard.py's top-level `import sqlglot`,
which made sqlglot a hard import-time requirement of the entire API (audit A5) -
remove it from requirements-api.txt and the dashboard stopped booting. Importing
inside the handler means the API starts, and every data route works, with
sqlglot and anthropic absent.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from api.deps import CurrentUser
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
            from chatbot.agent import ask

            answer = await asyncio.to_thread(
                ask, body.message, history, user.user_id, user.role
            )
        except Exception:
            # E3: every other error path in this app returns a flat message and
            # logs the detail. This one used to stream str(exc)[:200] to the
            # browser, so a psycopg failure surfaced table names, column names
            # and fragments of SQL to whoever was in the panel.
            log.exception("chat failed")
            yield {"event": "error", "data": json.dumps({
                "message": "The assistant could not answer that. The dashboard is unaffected.",
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
    from chatbot.agent import ask

    history = [t.model_dump() for t in body.history[-MAX_HISTORY:]]
    answer = await asyncio.to_thread(ask, body.message, history, user.user_id, user.role)
    payload = asdict(answer)
    if not user.is_admin:
        payload.pop("cost_usd", None)
    return payload
