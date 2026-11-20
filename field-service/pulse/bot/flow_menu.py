"""The menu: expense, give money (members who may give advances), report (not built yet), my account."""
from __future__ import annotations

from typing import Any

from pulse.bot import cards, flow_advance, flow_expense, flow_me, session as sessions, texts_hi
from pulse.bot.ui import CB_MENU_EXPENSE, CB_MENU_GIVE, CB_MENU_ME, CB_MENU_REPORT, Ctx, menu_markup, send

# re-exported for callers and tests written against slice 1a
CB_EXPENSE, CB_REPORT, CB_ME, CB_GIVE = CB_MENU_EXPENSE, CB_MENU_REPORT, CB_MENU_ME, CB_MENU_GIVE
_FLOW_OF = {CB_MENU_EXPENSE: flow_expense.FLOW, CB_MENU_GIVE: flow_advance.FLOW}


def show_menu(ctx: Ctx, chat_id: int, text: str = texts_hi.MENU_PROMPT, member: dict | None = None) -> None:
    send(ctx, chat_id, text, menu_markup(member))


def handle_callback(ctx: Ctx, member: dict[str, Any], chat_id: int, message_id: int, data: str) -> tuple[str, str | None]:
    if data in _FLOW_OF:
        sess = sessions.load(ctx.conn, chat_id)
        if cards.is_open(sess, ctx) and sess.flow != _FLOW_OF[data]:
            cards.abandon(ctx, chat_id, sess)          # a different flow is starting over an open draft
    if data == CB_MENU_EXPENSE:
        return flow_expense.start_from_menu(ctx, member, chat_id, message_id)
    if data == CB_MENU_GIVE:
        return flow_advance.start_from_menu(ctx, member, chat_id, message_id)
    if data == CB_MENU_ME:
        return flow_me.show(ctx, member, chat_id, message_id), None
    if data == CB_MENU_REPORT:
        send(ctx, chat_id, texts_hi.COMING_SOON)
        return "coming_soon", None
    show_menu(ctx, chat_id, member=member)
    return "menu", None
