"""Buttons on report alert cards and on the assignee's card (LLD §5.3, §5.4).

    alert cards     t:<report hex>:a            acknowledge (first one wins)
                    t:<report hex>:s / sp:<n>   choose an assignee (page n) · p:<i> assign · bk back
                    t:<report hex>:c:<id>       set the category (unsorted cards)
                    t:<report hex>:m:<i>        merge into suggested original i
                    t:<report hex>:i            link to an issue: il:<issue number> existing · in new
    assignee card   w:<report hex>:s            start work
                    w:<report hex>:d | n        done | cannot be done → short note → optional photo
    close steps     z:<sid>:sk                  skip the note or the photo

The tapping person's own card is redrawn at once (a direct reply); everyone else's cards are
redrawn through the queue.
"""
from __future__ import annotations

import logging
import secrets
from typing import Any
from uuid import UUID

from pulse.bot import session as sessions
from pulse.bot import texts_hi as T
from pulse.bot.session import Session
from pulse.bot.ui import Ctx, btn, keyboard, send, two_columns, edit
from pulse.notify import alerts, outbox
from pulse.reports import access, duplicates, issues, lifecycle, media, triage
from pulse.reports import service as reports

log = logging.getLogger(__name__)

FLOW = "close"
PAGE = 8


def _redraw(ctx: Ctx, chat_id: int, message_id: int, text: str, markup: dict | None, is_media: bool,
            alert_id: int | None = None) -> None:
    """Redraw an alert card in place after the commit. If Telegram refuses or is unreachable, the outbox
    redraws it later from the database (tg_edit), so the card ends up showing what was saved."""
    def later(conn):
        if alert_id is not None:
            from pulse.notify import outbox

            outbox.enqueue_edit(conn, alert_id)
    edit(ctx, chat_id, message_id, text, markup, caption=is_media, on_fail=later)


def _redraw_card(ctx: Ctx, chat_id: int, message_id: int, alert: dict[str, Any] | None) -> None:
    if alert is None:
        return
    text, markup, _ = alerts.render_card(ctx.conn, ctx.core, alert)
    _redraw(ctx, chat_id, message_id, text, markup, alert["is_media"], alert.get("alert_id"))


def _parse(data: str) -> tuple[UUID, str, str] | None:
    parts = data.split(":", 3)
    if len(parts) < 3:
        return None
    try:
        rid = UUID(hex=parts[1])
    except ValueError:
        return None
    return rid, parts[2], parts[3] if len(parts) > 3 else ""


def _errors(fn):
    """Refusals become a toast. Each action runs in a savepoint, so a refusal raised after part of the
    action was written (for example a new issue opened, then the link refused) leaves nothing behind."""
    def wrapped(ctx: Ctx, *a, **kw) -> tuple[str, str | None]:
        try:
            with ctx.conn.transaction():
                return fn(ctx, *a, **kw)
        except (triage.Denied, issues.Denied):
            return "not_allowed", T.NOT_ALLOWED
        except issues.IssueError:
            return "issue_refused", T.ISSUE_NOT_POSSIBLE
        except lifecycle.TransitionError:
            return "transition_refused", T.STATUS_NOT_ALLOWED
        except reports.ReportError:
            return "card_stale", T.CARD_STALE
    return wrapped


@_errors
def on_alert_callback(ctx: Ctx, member: dict[str, Any], chat_id: int, message_id: int, data: str) -> tuple[str, str | None]:
    parsed = _parse(data)
    if parsed is None:
        return "card_stale", T.CARD_STALE
    rid, op, arg = parsed
    v = access.viewer_for(ctx.conn, member["member_id"])
    alert = outbox.card_for_message(ctx.conn, chat_id, message_id)
    own = alert["alert_id"] if alert else None
    report = reports.get(ctx.conn, rid)
    if report is None or not access.can_see(ctx.conn, v, rid):
        return "card_stale", T.CARD_STALE

    if op == "a":
        done = triage.acknowledge(ctx.conn, v, rid, now=ctx.now, except_alert=own)
        if not done.changed:
            r = done.report
            if r["ack_at"] is not None:
                who = alerts._name(ctx.conn, r["ack_by"])
                _redraw_card(ctx, chat_id, message_id, alert)
                return "report_already_seen", T.ALREADY_SEEN.format(name=who, time=alerts.when(r["ack_at"]))
            return "transition_refused", T.STATUS_NOT_ALLOWED
        _redraw_card(ctx, chat_id, message_id, alert)
        return "report_acknowledged", None

    if op in ("s", "sp"):
        if not access.may_triage(ctx.conn, v, report):
            raise triage.Denied("assign")
        page = int(arg) if op == "sp" and arg.isdigit() else 0
        people = triage.assignable(ctx.conn, v, report)
        chunk = people[page * PAGE:(page + 1) * PAGE]
        key = rid.hex
        grid = two_columns([btn(p["display_name"], f"t:{key}:p:{page * PAGE + i}") for i, p in enumerate(chunk)])
        extra = ([btn(T.BTN_NEXT_PAGE, f"t:{key}:sp:{page + 1}")] if (page + 1) * PAGE < len(people) else []) + \
                [btn(T.BTN_BACK, f"t:{key}:bk")]
        _redraw(ctx, chat_id, message_id, T.ASK_ASSIGNEE, keyboard(grid + [extra]), bool(alert and alert["is_media"]))
        return "report_pick_assignee", None

    if op == "p" and arg.isdigit():
        people = triage.assignable(ctx.conn, v, report)
        if int(arg) >= len(people):
            return "card_stale", T.CARD_STALE
        triage.assign(ctx.conn, v, rid, people[int(arg)]["member_id"], now=ctx.now, except_alert=own)
        _redraw_card(ctx, chat_id, message_id, alert)
        return "report_assigned", None

    if op == "c" and arg.isdigit():
        if report["category_id"] is not None:                # sorted meanwhile: an old card never overwrites it
            _redraw_card(ctx, chat_id, message_id, alert)
            return "report_category_set_already", T.CATEGORY_ALREADY_SET
        triage.set_category(ctx.conn, v, rid, int(arg), now=ctx.now, except_alert=own)
        _redraw_card(ctx, chat_id, message_id, alert)
        return "report_category", None

    if op == "m" and arg.isdigit():
        dups = duplicates.suggestions(ctx.conn, rid)
        if int(arg) >= len(dups):
            return "card_stale", T.CARD_STALE
        triage.merge(ctx.conn, v, rid, dups[int(arg)]["report_id"], now=ctx.now, except_alert=own)
        _redraw_card(ctx, chat_id, message_id, alert)
        return "report_merged", None

    if op == "i":
        if not issues.may_manage(v):
            raise issues.Denied("link issue")
        if report["area_id"] is None:
            return "issue_refused", T.ISSUE_NOT_POSSIBLE
        key = rid.hex
        found = issues.open_in_area(ctx.conn, v, report["area_id"])
        rows = [[btn(f"{i['issue_no']} · {i['title']}"[:60], f"t:{key}:il:{issues.number(i)}")] for i in found]
        rows += [[btn(T.BTN_ISSUE_NEW, f"t:{key}:in")], [btn(T.BTN_BACK, f"t:{key}:bk")]]
        _redraw(ctx, chat_id, message_id, T.ASK_ISSUE if found else T.ASK_ISSUE_NONE_OPEN, keyboard(rows),
                bool(alert and alert["is_media"]))
        return "report_pick_issue", None

    if op == "il" and arg.isdigit():
        issue = issues.by_number(ctx.conn, v, int(arg))
        if issue is None:
            return "card_stale", T.CARD_STALE
        issues.link(ctx.conn, v, issue["issue_id"], rid, now=ctx.now, except_alert=own)
        _redraw_card(ctx, chat_id, message_id, alert)
        return "report_issue_linked", T.ISSUE_LINKED.format(issue_no=issue["issue_no"])

    if op == "in":
        issue = issues.create(ctx.conn, ctx.core, v, now=ctx.now, from_report_id=rid)
        _redraw_card(ctx, chat_id, message_id, alert)
        return "report_issue_created", T.ISSUE_CREATED.format(issue_no=issue["issue_no"])

    if op == "bk":
        _redraw_card(ctx, chat_id, message_id, alert)
        return "report_card", None
    return "card_stale", T.CARD_STALE


@_errors
def on_work_callback(ctx: Ctx, member: dict[str, Any], chat_id: int, message_id: int, data: str,
                     sess: Session | None) -> tuple[str, str | None]:
    parsed = _parse(data)
    if parsed is None:
        return "card_stale", T.CARD_STALE
    rid, op, _ = parsed
    v = access.viewer_for(ctx.conn, member["member_id"])
    report = reports.get(ctx.conn, rid)
    if report is None or not access.may_work(v, report):
        return "not_allowed", T.NOT_ALLOWED
    alert = outbox.card_for_message(ctx.conn, chat_id, message_id)
    if op == "s":
        triage.start(ctx.conn, v, rid, now=ctx.now, except_alert=alert["alert_id"] if alert else None)
        _redraw_card(ctx, chat_id, message_id, alert)
        return "report_started", None
    if op in ("d", "n"):
        target = "resolved" if op == "d" else "closed_no_action"
        if not lifecycle.allowed(report["status"], target):
            return "transition_refused", T.STATUS_NOT_ALLOWED
        from pulse.bot import cards
        if cards.is_open(sess, ctx) and sess.flow != FLOW:
            cards.abandon(ctx, chat_id, sess)
        d = {"sid": secrets.token_hex(3), "report_id": str(rid), "to": target, "note": None,
             "card": message_id, "alert_id": alert["alert_id"] if alert else None}
        sessions.save(ctx.conn, chat_id, FLOW, "note", d, ttl=cards.ttl(ctx), now=ctx.now)
        send(ctx, chat_id, T.ASK_CLOSE_NOTE, keyboard([[btn(T.BTN_SKIP, f"z:{d['sid']}:sk")]]))
        return "close_ask_note", None
    return "card_stale", T.CARD_STALE


def _finish(ctx: Ctx, member: dict[str, Any], chat_id: int, d: dict[str, Any], photo: UUID | None) -> str:
    v = access.viewer_for(ctx.conn, member["member_id"])
    sessions.clear(ctx.conn, chat_id)
    try:
        done = triage.close(ctx.conn, v, UUID(d["report_id"]), d["to"], now=ctx.now, note=d["note"],
                            photo_asset_id=photo, except_alert=d.get("alert_id"))
    except (triage.Denied, lifecycle.TransitionError):
        send(ctx, chat_id, T.STATUS_NOT_ALLOWED)
        return "transition_refused"
    if d.get("alert_id"):
        alert = ctx.conn.execute("SELECT * FROM field.alert_message WHERE alert_id = %s", (d["alert_id"],)).fetchone()
        _redraw_card(ctx, chat_id, d["card"], alert)
    send(ctx, chat_id, T.CLOSED_DONE.format(report_no=done.report["report_no"], status=T.REPORT_STATUS[d["to"]]))
    return "report_closed"


def on_text(ctx: Ctx, member: dict[str, Any], chat_id: int, text: str, sess: Session) -> str | None:
    d = sess.payload
    if sess.state != "note":
        return None
    d["note"] = text.strip()[:1000] or None
    sessions.save(ctx.conn, chat_id, FLOW, "photo", d, ttl=sessions_ttl(ctx), now=ctx.now)
    send(ctx, chat_id, T.ASK_CLOSE_PHOTO, keyboard([[btn(T.BTN_SKIP, f"z:{d['sid']}:sk")]]))
    return "close_ask_photo"


def on_media(ctx: Ctx, member: dict[str, Any], chat_id: int, message: dict[str, Any], sess: Session) -> str | None:
    if sess.state != "photo":
        return None
    found = media.describe(message, ctx.reports.max_download_bytes)
    if not isinstance(found, media.Item) or found.kind != "photo":
        return None
    asset = media.register(ctx.conn, member, found, now=ctx.now)
    return _finish(ctx, member, chat_id, sess.payload, asset["asset_id"])


def on_skip(ctx: Ctx, member: dict[str, Any], chat_id: int, message_id: int, data: str,
            sess: Session | None) -> tuple[str, str | None]:
    parts = data.split(":")
    if sess is None or sess.flow != FLOW or len(parts) != 3 or sess.payload.get("sid") != parts[1]:
        return "card_stale", T.CARD_STALE
    if sess.expired(ctx.now):                                # the close steps ran out: nothing is closed by an old skip
        sessions.clear(ctx.conn, chat_id)
        return "card_expired", T.CLOSE_EXPIRED
    d = sess.payload
    if sess.state == "note":
        sessions.save(ctx.conn, chat_id, FLOW, "photo", d, ttl=sessions_ttl(ctx), now=ctx.now)
        send(ctx, chat_id, T.ASK_CLOSE_PHOTO, keyboard([[btn(T.BTN_SKIP, f"z:{d['sid']}:sk")]]))
        return "close_ask_photo", None
    return _finish(ctx, member, chat_id, d, None), None


def sessions_ttl(ctx: Ctx):
    from pulse.bot import cards
    return cards.ttl(ctx)
