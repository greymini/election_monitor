"""POST /telegram/webhook: verify the secret header, then hand the update to the shared dispatcher."""
from __future__ import annotations

import hmac

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from pulse.bot.dispatch import dispatch

router = APIRouter()

SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"


@router.post("/telegram/webhook")
async def webhook(request: Request) -> dict:
    state = request.app.state.pulse
    given = request.headers.get(SECRET_HEADER, "")
    if not hmac.compare_digest(given.encode("utf-8"), state.settings.telegram_webhook_secret.encode("utf-8")):
        raise HTTPException(403, "forbidden")
    try:
        update = await request.json()
    except ValueError:
        raise HTTPException(400, "body must be JSON") from None
    if not isinstance(update, dict):
        raise HTTPException(400, "body must be a JSON object")
    # Always 200 once accepted: Telegram retries anything else, and duplicates are ignored anyway.
    await run_in_threadpool(dispatch, state.pool, state.bot, update)
    return {"ok": True}
