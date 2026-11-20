"""Drawing outbox messages at send time (slice 2c: the outbox stores no message bodies).

alert_message.body is {"render": <template>, ...ids and codes}. Each template here reads what it
needs from the database when the message is sent, so no text, name, amount or note is ever kept
in the outbox or in a job payload. A template may return None: the message is then skipped
(e.g. a nudge whose balance is no longer positive).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable
from uuid import UUID

import psycopg

from pulse.bot import texts_hi as T
from pulse.bot.format import inr
from pulse.bot.ui import CB_MENU_EXPENSE, btn, keyboard
from pulse.core_adapter import CoreAdapter

Rendered = tuple[str, dict | None, str | None] | None      # (text, keyboard, photo file_id) or None = skip
ALLOWED_PARAMS = {"render", "minutes", "no_contact", "no_photo", "reminder", "reason", "what", "into_report_id",
                  "recipient_member_id", "header", "confirm", "flow", "sid"}


def _name(conn: psycopg.Connection, member_id: Any) -> str:
    if member_id is None:
        return ""
    row = conn.execute("SELECT display_name FROM field.member WHERE member_id = %s", (member_id,)).fetchone()
    return row["display_name"] if row else ""


def _txn(conn: psycopg.Connection, a: dict[str, Any]) -> dict[str, Any]:
    return conn.execute("SELECT * FROM field.money_txn WHERE txn_id = %s", (a["txn_id"],)).fetchone()


def _report(conn: psycopg.Connection, a: dict[str, Any]) -> dict[str, Any]:
    return conn.execute("SELECT * FROM field.report WHERE report_id = %s", (a["report_id"],)).fetchone()


def ack_request(conn, core, a, now) -> Rendered:
    from pulse.bot.flow_ack import request_card_for

    txn = _txn(conn, a)
    if txn["status"] != "pending_ack":
        return None                                       # answered or removed before it went out
    text, markup = request_card_for(conn, txn, reminder=bool(a["body"].get("reminder")))
    return text, markup, None


def ack_notice(conn, core, a, now) -> Rendered:
    txn = _txn(conn, a)
    return T.ACK_NOTICE_TO_GIVER.format(receiver=_name(conn, txn["to_member_id"]), amount=inr(txn["amount"]),
                                        txn_no=txn["txn_no"]), None, None


def dispute_notice(conn, core, a, now) -> Rendered:
    txn = _txn(conn, a)
    return T.DISPUTE_NOTICE.format(receiver=_name(conn, txn["to_member_id"]), txn_no=txn["txn_no"],
                                   amount=inr(txn["amount"]), giver=_name(conn, txn["from_member_id"]),
                                   reason=T.DISPUTE_REASONS[a["body"]["reason"]]), None, None


def removed_notice(conn, core, a, now) -> Rendered:
    txn = _txn(conn, a)
    return T.REMOVED_NOTICE.format(giver=_name(conn, txn["from_member_id"]), txn_no=txn["txn_no"],
                                   amount=inr(txn["amount"])), None, None


def undelivered(conn, core, a, now) -> Rendered:
    txn = _txn(conn, a)
    return T.RECEIVER_UNDELIVERED.format(name=_name(conn, a["body"]["recipient_member_id"]),
                                         txn_no=txn["txn_no"]), None, None


def media_failed(conn, core, a, now) -> Rendered:
    r = _report(conn, a)
    return (T.MEDIA_FAILED.format(report_no=r["report_no"]) if r["report_no"] else T.MEDIA_FAILED_DRAFT), None, None


def report_filed_notice(conn, core, a, now) -> Rendered:
    from pulse.bot.flow_report import render_filed_for

    header = {"autofiled": T.REPORT_AUTOFILED}.get(a["body"].get("header"))
    text, markup = render_filed_for(conn, core, a["report_id"], header=header)
    return text, markup, None


def feedback(conn, core, a, now) -> Rendered:
    r = _report(conn, a)
    what, no = a["body"]["what"], r["report_no"]
    if what == "acknowledged":
        lines = [T.FEEDBACK_SEEN.format(report_no=no)]
    elif what == "resolved":
        lines = [T.FEEDBACK_RESOLVED.format(report_no=no)]
    elif what == "closed_no_action":
        lines = [T.FEEDBACK_CLOSED.format(report_no=no)]
    elif what == "duplicate":
        into = conn.execute("SELECT report_no FROM field.report WHERE report_id = %s",
                            (a["body"]["into_report_id"],)).fetchone()
        lines = [T.FEEDBACK_DUPLICATE.format(report_no=no, into=into["report_no"] if into else "")]
    else:
        return None
    if what in ("resolved", "closed_no_action") and r["resolution_note"]:
        lines.append(T.FEEDBACK_NOTE.format(note=r["resolution_note"]))
    return "\n".join(lines), None, None


def digest(conn, core, a, now) -> Rendered:
    from pulse.notify import digest as digest_module

    member = conn.execute("SELECT * FROM field.member WHERE member_id = %s", (a["recipient_member_id"],)).fetchone()
    text = digest_module.build(conn, core, member, a["local_date"], now)
    return (text, None, None) if text else None


def digest_weekly(conn, core, a, now) -> Rendered:
    from pulse.notify import digest_weekly as weekly

    member = conn.execute("SELECT * FROM field.member WHERE member_id = %s", (a["recipient_member_id"],)).fetchone()
    return weekly.build(conn, core, member, a["local_date"]), None, None


def nudge(conn, core, a, now) -> Rendered:
    from pulse.metrics.ledger import member_balance_sql

    balance = conn.execute(member_balance_sql(), {"member_id": a["recipient_member_id"]}).fetchone()["balance"]
    if balance <= 0:
        return None
    return T.NUDGE.format(amount=inr(balance)), keyboard([[btn(T.BTN_EXPENSE, CB_MENU_EXPENSE)]]), None


def followup(conn, core, a, now) -> Rendered:
    """The impact follow-up question; skipped once answered, cancelled or voided."""
    from pulse.bot import flow_followup

    f = conn.execute(
        """SELECT f.answer, f.cancelled_at, t.txn_no, t.amount, t.txn_date, t.status, b.name_hi
           FROM field.txn_followup f JOIN field.money_txn t ON t.txn_id = f.txn_id
           JOIN field.exp_bucket b ON b.bucket_id = t.bucket_id WHERE f.txn_id = %s""", (a["txn_id"],)).fetchone()
    if f is None or f["answer"] is not None or f["cancelled_at"] is not None or f["status"] == "void":
        return None
    text = T.FOLLOWUP_ASK.format(date=f["txn_date"].strftime("%d-%m-%Y"), bucket=f["name_hi"],
                                 amount=inr(f["amount"]), txn_no=f["txn_no"])
    if a["body"].get("reminder"):
        text = T.FOLLOWUP_REMINDER_PREFIX + text
    return text, flow_followup.buttons(a["txn_id"]), None


def _session_ctx(conn, core, member: dict[str, Any], now):
    from pulse import settings_ac
    from pulse.bot.ui import Ctx

    eff = settings_ac.effective(conn, member["ac_id"])
    return Ctx(conn=conn, client=None, core=core, limits=eff.ledger, now=now, reports=eff.reports)


def _session_card(conn, core, a, now) -> Rendered:
    """The chat's open draft card, as it is now, if it is still the draft the failed reply was about."""
    from pulse.bot import flow_advance, flow_expense, flow_report, flow_return
    from pulse.notify.outbox import linked_chat

    chat_id = linked_chat(conn, a["recipient_member_id"])
    s = conn.execute("SELECT flow, state, payload, expires_at FROM field.tg_session WHERE chat_id = %s",
                     (chat_id,)).fetchone() if chat_id is not None else None
    b = a["body"]
    if s is None or s["flow"] != b.get("flow") or (s["payload"] or {}).get("sid") != b.get("sid"):
        return None                                   # moved on: a later reply covers it
    if s["expires_at"] is not None and s["expires_at"] <= now:
        return None                                   # expired: the member is told on their next message
    member = conn.execute("SELECT * FROM field.member WHERE member_id = %s", (a["recipient_member_id"],)).fetchone()
    ctx, d = _session_ctx(conn, core, member, now), dict(s["payload"])
    renderers = {flow_expense.FLOW: lambda: flow_expense._render(ctx, member, d, s["state"]),
                 flow_advance.FLOW: lambda: flow_advance._render(ctx, member, d, s["state"]),
                 flow_return.FLOW: lambda: flow_return._render(ctx, member, d, s["state"]),
                 flow_report.FLOW: lambda: (flow_report.render_filed(ctx, UUID(d["report_id"]))
                                            if s["state"] == flow_report.S_FILED
                                            else flow_report.render_draft(ctx, d, s["state"]))}
    if s["flow"] not in renderers:
        return None
    text, markup = renderers[s["flow"]]()
    return T.REPLY_RECEIVED + "\n\n" + text, markup, None


def reply(conn, core, a, now) -> Rendered:
    """A reply that could not be sent when the member acted, drawn now from what was saved. It always says
    the input was already saved, so nobody sends it again (pulse/bot/outgoing.py)."""
    c = a["body"].get("confirm")
    if c == "txn":
        txn = _txn(conn, a)
        if txn is None:
            return None
        if txn["status"] == "void":
            return T.REPLY_TXN_VOID.format(txn_no=txn["txn_no"]), None, None
        return (T.REPLY_SAVED + "\n" + T.ALREADY_POSTED.format(txn_no=txn["txn_no"]) + "\n"
                + T.REPLY_TXN.format(txn_no=txn["txn_no"], amount=inr(txn["amount"]))), None, None
    if c == "report":
        r = _report(conn, a)
        if r is None:
            return None
        if r["status"] == "draft":
            return T.REPLY_SAVED + "\n" + T.REPLY_REPORT_DRAFT, None, None
        from pulse.bot.flow_report import render_filed_for

        text, markup = render_filed_for(conn, core, a["report_id"], header=T.REPLY_SAVED + "\n" + T.REPORT_FILED)
        return text, markup, None
    if c == "followup":
        txn = _txn(conn, a)
        return (T.REPLY_SAVED + "\n" + T.REPLY_FOLLOWUP.format(txn_no=txn["txn_no"])), None, None
    if c == "pin":
        return T.REPLY_SAVED + "\n" + T.REPLY_PIN, None, None
    if c == "pin_not_deleted":
        return T.PIN_NOT_DELETED, None, None
    if c == "session":
        return _session_card(conn, core, a, now)
    return None


def report_card(conn, core, a, now) -> Rendered:
    from pulse.notify import alerts

    return alerts.render_card(conn, core, a)


TEMPLATES: dict[str, Callable[[psycopg.Connection, CoreAdapter, dict[str, Any], datetime], Rendered]] = {
    "report_card": report_card, "ack_request": ack_request, "ack_notice": ack_notice,
    "dispute_notice": dispute_notice, "removed_notice": removed_notice, "undelivered": undelivered,
    "media_failed": media_failed, "report_filed_notice": report_filed_notice, "feedback": feedback,
    "digest": digest, "digest_weekly": digest_weekly, "nudge": nudge, "followup": followup, "reply": reply,
}


def render(conn: psycopg.Connection, core: CoreAdapter, alert: dict[str, Any], now: datetime) -> Rendered:
    template = TEMPLATES.get((alert["body"] or {}).get("render"))
    return template(conn, core, alert, now) if template else None     # 'redacted' and unknown: skip


def check_params(params: dict[str, Any]) -> None:
    unknown = set(params) - ALLOWED_PARAMS
    if unknown:
        raise ValueError(f"outbox params may hold ids and codes only, not {sorted(unknown)}")

