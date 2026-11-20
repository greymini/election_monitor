"""The acknowledgement card for advances and returns (LLD §5.2), and the notices around it.

The card goes to whoever received the money: the receiver of an advance, the giver of a return.
Its buttons carry `k:<txn hex>:y|d|n` and work independently of any open draft. The entry row is
locked while a tap is handled, so a double tap or a redelivered update changes it exactly once.

Every message to someone other than the person acting goes through the outbox (pulse/notify/outbox.py)
and is sent by the worker. An unlinked receiver is known at once ('not_linked') and the giver's card
says so; a message that later cannot be delivered (blocked, failed) is reported to the giver by the
sender job. Nothing about delivery ever undoes the entry.
"""
from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from pulse.bot import texts_hi as T
from pulse.bot.format import inr
from pulse.bot.outgoing import Confirm
from pulse.bot.ui import Ctx, btn, edit_or_send, keyboard, record, send
from pulse.ledger import service as ledger
from pulse.ledger.errors import LedgerError
from pulse.metrics.ledger import member_balance_sql
from pulse.notify import outbox

log = logging.getLogger(__name__)

PREFIX = "k"
QUEUED, NOT_LINKED = "queued", "not_linked"
_OPS = {"y": None, "d": "amount_differs", "n": "not_received"}


def member_chat(ctx: Ctx, member_id: UUID) -> int | None:
    row = ctx.conn.execute(
        """SELECT l.chat_id FROM field.tg_link l JOIN field.member m ON m.member_id = l.member_id
           WHERE l.member_id = %s AND l.revoked_at IS NULL AND m.active""",
        (member_id,),
    ).fetchone()
    return row["chat_id"] if row else None


def notice(ctx: Ctx, member_id: UUID, template: str, txn: dict[str, Any], **params: Any) -> None:
    """Queue a notice about an entry; the sender records it as undelivered if the member has no link."""
    outbox.enqueue(ctx.conn, recipient=member_id, kind="notice", ac_id=txn["ac_id"], txn_id=txn["txn_id"],
                   body=outbox.body(template, **params))


def delivery_line(result: str, name: str) -> str:
    return {QUEUED: T.RECEIVER_QUEUED, NOT_LINKED: T.RECEIVER_NOT_LINKED}[result].format(name=name)


def _name(conn, member_id: UUID | None) -> str:
    if member_id is None:
        return ""
    row = conn.execute("SELECT display_name FROM field.member WHERE member_id = %s", (member_id,)).fetchone()
    return row["display_name"] if row else ""


def _balance_line(ctx: Ctx, member_id: UUID) -> str | None:
    row = ctx.conn.execute("SELECT is_principal FROM field.member WHERE member_id = %s", (member_id,)).fetchone()
    if row is None or row["is_principal"]:
        return None
    bal = ctx.conn.execute(member_balance_sql(), {"member_id": member_id}).fetchone()["balance"]
    return T.BALANCE_HELD.format(amount=inr(bal)) if bal >= 0 else T.BALANCE_OWED.format(amount=inr(-bal))


def request_card(ctx: Ctx, txn: dict[str, Any], *, reminder: bool = False) -> tuple[str, dict]:
    return request_card_for(ctx.conn, txn, reminder=reminder)


def request_card_for(conn, txn: dict[str, Any], *, reminder: bool = False) -> tuple[str, dict]:
    template = T.ACK_REQUEST_ADVANCE if txn["kind"] == "advance" else T.ACK_REQUEST_RETURN
    lines = [T.ACK_REMINDER] if reminder else []
    lines.append(template.format(giver=_name(conn, txn["from_member_id"]), amount=inr(txn["amount"]),
                                 txn_no=txn["txn_no"]))
    if txn["remark"]:
        lines.append(T.LINE_PURPOSE.format(purpose=txn["remark"]))
    key = UUID(str(txn["txn_id"])).hex
    markup = keyboard([[btn(T.BTN_ACK_YES, f"{PREFIX}:{key}:y")],
                       [btn(T.BTN_ACK_DIFF, f"{PREFIX}:{key}:d"), btn(T.BTN_ACK_NO, f"{PREFIX}:{key}:n")]])
    return "\n".join(lines), markup


def request_ack(ctx: Ctx, txn: dict[str, Any], *, reminder: bool = False) -> str:
    """Queue the card for whoever must confirm. Returns QUEUED, or NOT_LINKED when there is nobody to send to."""
    if member_chat(ctx, txn["to_member_id"]) is None:
        return NOT_LINKED
    outbox.enqueue(ctx.conn, recipient=txn["to_member_id"], kind="ack_request", ac_id=txn["ac_id"],
                   txn_id=txn["txn_id"], body=outbox.body("ack_request", reminder=reminder))
    return QUEUED


def tell_ack(ctx: Ctx, txn: dict[str, Any], actor_member_id: UUID) -> None:
    if txn["from_member_id"] != actor_member_id:
        notice(ctx, txn["from_member_id"], "ack_notice", txn)


def tell_dispute(ctx: Ctx, txn: dict[str, Any], reason: str, actor_member_id: UUID) -> None:
    """The giver and every member with finance scope all, except whoever raised it."""
    treasurers = [r["member_id"] for r in ctx.conn.execute(
        "SELECT member_id FROM field.member WHERE ac_id = %s AND active AND finance_scope = 'all'", (txn["ac_id"],))]
    for member_id in dict.fromkeys([txn["from_member_id"], *treasurers]):
        if member_id != actor_member_id:
            notice(ctx, member_id, "dispute_notice", txn, reason=reason)


def tell_removed(ctx: Ctx, txn: dict[str, Any]) -> None:
    notice(ctx, txn["to_member_id"], "removed_notice", txn)


def on_callback(ctx: Ctx, member: dict[str, Any], chat_id: int, message_id: int, data: str) -> tuple[str, str | None]:
    parts = data.split(":")
    if len(parts) != 3 or parts[2] not in _OPS:
        return "card_stale", T.CARD_STALE
    try:
        txn_id = UUID(hex=parts[1])
    except ValueError:
        return "card_stale", T.CARD_STALE
    txn = ctx.conn.execute("SELECT * FROM field.money_txn WHERE txn_id = %s FOR UPDATE", (txn_id,)).fetchone()
    if txn is None or txn["to_member_id"] != member["member_id"]:
        return "card_stale", T.CARD_STALE
    if txn["status"] != "pending_ack":
        toast = {"posted": T.ALREADY_ACKED, "disputed": T.ALREADY_DISPUTED, "void": T.ENTRY_REMOVED}[txn["status"]]
        return f"ack_already_{txn['status']}", toast

    reason = _OPS[parts[2]]
    try:
        if reason is None:
            row = ledger.acknowledge(ctx.conn, txn_id, member["member_id"], now=ctx.now)
        else:
            row = ledger.dispute(ctx.conn, txn_id, member["member_id"], reason, now=ctx.now)
    except LedgerError:
        log.warning("acknowledgement refused", exc_info=True)
        return "not_saved", T.NOT_SAVED

    if reason is None:
        lines = [T.ACKED_CARD.format(amount=inr(row["amount"]), txn_no=row["txn_no"])]
        balance = _balance_line(ctx, member["member_id"])
        if balance:
            lines.append(balance)
        edit_or_send(ctx, chat_id, message_id, "\n".join(lines), None, confirms=Confirm("txn", txn_id=row["txn_id"]))
        tell_ack(ctx, row, member["member_id"])
        return "acknowledged", None
    edit_or_send(ctx, chat_id, message_id,
                 T.DISPUTED_CARD.format(reason=T.DISPUTE_REASONS[reason], txn_no=row["txn_no"]), None,
                 confirms=Confirm("txn", txn_id=row["txn_id"]))
    tell_dispute(ctx, row, reason, member["member_id"])
    return "disputed", None


def open_card(ctx: Ctx, member: dict[str, Any], chat_id: int, txn_hex: str) -> tuple[str, str | None]:
    """Re-send a pending acknowledgement card (from "my account")."""
    try:
        txn_id = UUID(hex=txn_hex)
    except ValueError:
        return "card_stale", T.CARD_STALE
    txn = ctx.conn.execute("SELECT * FROM field.money_txn WHERE txn_id = %s", (txn_id,)).fetchone()
    if txn is None or txn["to_member_id"] != member["member_id"]:
        return "card_stale", T.CARD_STALE
    if txn["status"] != "pending_ack":
        return f"ack_already_{txn['status']}", {"posted": T.ALREADY_ACKED, "disputed": T.ALREADY_DISPUTED,
                                                 "void": T.ENTRY_REMOVED}[txn["status"]]
    text, markup = request_card(ctx, txn)
    record(ctx, chat_id, send(ctx, chat_id, text, markup), "money", txn_id=txn["txn_id"])
    return "ack_card", None
