"""Run the bot locally by long polling (no public URL needed).

    python -m scripts.run_polling
"""
from __future__ import annotations

import logging
import sys

from pulse.bot.client import HttpxTelegramClient
from pulse.bot.polling import WebhookActiveError, run_polling
from pulse.bot.ui import BotDeps
from pulse.config import ConfigError, load_settings
from pulse.api.main import make_core_adapter
from pulse.db import make_pool


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # request URLs contain the bot token
    try:
        settings = load_settings()
    except ConfigError as e:
        print(e, file=sys.stderr)
        return 2
    pool = make_pool(settings.database_url, max_size=4)
    client = HttpxTelegramClient(settings.telegram_bot_token)
    try:
        print(f"Polling as @{settings.telegram_bot_username}. Ctrl+C to stop.")
        run_polling(pool, BotDeps(client=client, core=make_core_adapter(settings), limits=settings.ledger,
                                       reports=settings.reports))
    except WebhookActiveError as e:
        print(e, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        pass
    finally:
        client.close()
        pool.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
