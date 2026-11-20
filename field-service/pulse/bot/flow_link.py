"""`/start <token>`: link a Telegram account to a member (LLD §2.2)."""
from __future__ import annotations

from pulse.bot import texts_hi
from pulse.bot.flow_menu import show_menu
from pulse.bot.ui import Ctx, send
from pulse.team.linking import LinkResult, active_member_for_telegram_user, redeem_link_token


def parse_start(text: str) -> str | None:
    """Return the start parameter ('' when absent) if text is a /start command, else None."""
    parts = text.strip().split(maxsplit=1)
    if not parts:
        return None
    command = parts[0].split("@", 1)[0]
    if command != "/start":
        return None
    return parts[1].strip() if len(parts) > 1 else ""


def handle_start(ctx: Ctx, telegram_user_id: int, chat_id: int, token: str) -> str:
    if not token:
        member = active_member_for_telegram_user(ctx.conn, telegram_user_id)
        if member:
            show_menu(ctx, chat_id, member=member)
            return "menu"
        send(ctx, chat_id, texts_hi.UNREGISTERED)
        return "unlinked"

    result, member = redeem_link_token(ctx.conn, token, telegram_user_id, chat_id, now=ctx.now)
    if result is LinkResult.LINKED:
        show_menu(ctx, chat_id, texts_hi.WELCOME.format(name=member["display_name"]) + "\n\n" + texts_hi.MENU_PROMPT,
                  member=active_member_for_telegram_user(ctx.conn, telegram_user_id))
        return "linked"
    if result is LinkResult.ACCOUNT_IN_USE:
        send(ctx, chat_id, texts_hi.ACCOUNT_IN_USE)
        return "link_refused_account_in_use"
    send(ctx, chat_id, texts_hi.LINK_INVALID)
    return "link_invalid"
