"""Long-polling entry point for local use. Hands every update to the same dispatcher as the webhook."""
from __future__ import annotations

import logging
import time
from typing import Callable

from psycopg_pool import ConnectionPool

from pulse.bot.client import TelegramApiError
from pulse.bot.dispatch import dispatch
from pulse.bot.ui import BotDeps

log = logging.getLogger(__name__)


class WebhookActiveError(RuntimeError):
    pass


def run_polling(
    pool: ConnectionPool,
    bot: BotDeps,
    *,
    timeout: int = 30,
    should_stop: Callable[[], bool] = lambda: False,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    # Telegram refuses getUpdates while a webhook is set. We do not delete it ourselves: a
    # deployed instance may be using the same bot.
    client = bot.client
    if client.get_webhook_info().get("url"):
        raise WebhookActiveError(
            "This bot has a webhook set, so long polling cannot run. Use a separate test bot, "
            "or remove the webhook deliberately (Bot API deleteWebhook)."
        )
    offset: int | None = None
    backoff = 1.0
    while not should_stop():
        try:
            updates = client.get_updates(offset, timeout)
        except TelegramApiError as e:
            log.warning("getUpdates failed (%s); retrying in %.0fs", e, backoff)
            sleep(backoff)
            backoff = min(backoff * 2, 60.0)
            continue
        backoff = 1.0
        for update in updates:
            outcome = dispatch(pool, bot, update)
            log.info("update %s → %s", update.get("update_id"), outcome)
            offset = update["update_id"] + 1
