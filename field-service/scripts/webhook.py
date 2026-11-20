"""Register, remove or show the Telegram webhook.

    python -m scripts.webhook info
    python -m scripts.webhook set [--drop-pending]      uses WEBHOOK_URL and TELEGRAM_WEBHOOK_SECRET
    python -m scripts.webhook delete [--drop-pending]   back to polling

The secret is read from the environment and never printed. Set BOT_MODE to match afterwards and run
scripts.preflight.
"""
from __future__ import annotations

import argparse
import logging
import sys

from pulse.bot.client import HttpxTelegramClient, TelegramApiError
from pulse.config import ConfigError, load_settings


def main(argv: list[str] | None = None) -> int:
    logging.getLogger("httpx").setLevel(logging.WARNING)   # request URLs contain the bot token
    p = argparse.ArgumentParser(prog="scripts.webhook")
    p.add_argument("action", choices=("info", "set", "delete"))
    p.add_argument("--drop-pending", action="store_true", help="discard updates Telegram is holding")
    args = p.parse_args(argv)
    try:
        s = load_settings()
    except ConfigError as e:
        print(e, file=sys.stderr)
        return 2
    client = HttpxTelegramClient(s.telegram_bot_token)
    try:
        if args.action == "set":
            if not s.webhook_url:
                print("WEBHOOK_URL is not set", file=sys.stderr)
                return 2
            client.set_webhook(s.webhook_url, s.telegram_webhook_secret, drop_pending_updates=args.drop_pending)
            print("webhook set; now run with BOT_MODE=webhook")
        elif args.action == "delete":
            client.delete_webhook(drop_pending_updates=args.drop_pending)
            print("webhook removed; polling can run (BOT_MODE=polling)")
        info = client.get_webhook_info()
    except TelegramApiError as e:
        print(f"Telegram refused: {e}", file=sys.stderr)
        return 1
    finally:
        client.close()
    print(f"url: {info.get('url') or '(none)'}")
    print(f"matches WEBHOOK_URL: {bool(info.get('url')) and info.get('url') == s.webhook_url}")
    print(f"pending updates: {info.get('pending_update_count', 0)}")
    if info.get("last_error_message"):
        print(f"last delivery error: {info['last_error_message']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
