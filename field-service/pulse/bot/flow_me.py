""""My account" (mera hisab, LLD §5.5). Always read live from the database.

Shows: balance (not for the principal), money in transit, money in dispute, acknowledgements
waiting on this member, each direct report's balance for finance scope team, the last five
entries; buttons for returning money, last month, and opening a pending acknowledgement.
Callbacks: `m:me`, `m:ret`, `m:prev`, `m:ack:<txn hex>`.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from pulse.bot import flow_ack, flow_pin, flow_return
from pulse.bot import texts_hi as T
from pulse.bot.format import inr, short_date
from pulse.bot.session import Session
from pulse.bot.ui import Ctx, btn, edit_or_send, keyboard, menu_markup, record, send
from pulse.common.clock import IST, today_ist
from pulse.ledger.buckets import OTHER_CODE
from pulse.metrics import spend as sm
from pulse.reports import service as reports
from pulse.metrics.ledger import balance_components_select, disputed_sql, in_transit_sql, member_balance_sql

RECENT_LIMIT = 5
WAITING_BUTTONS = 3


def _held(bal: Decimal) -> str:
    return T.BALANCE_HELD.format(amount=inr(bal)) if bal >= 0 else T.BALANCE_OWED.format(amount=inr(-bal))


def _entry_label(r: dict[str, Any], me: UUID) -> str:
    if r["kind"] == "expense":
        if r["top_code"] == OTHER_CODE:
            return T.OTHER_LABEL.format(label=r["custom_label"] or "")
        if r["parent_id"] is not None:
            return r["top_name_hi"] + T.SUBHEAD_SEP + r["name_hi"]
        return r["name_hi"]
    if r["kind"] in ("advance", "return"):
        incoming = r["to_member_id"] == me
        label = T.XFER_LABELS[(r["kind"], "in" if incoming else "out")].format(
            name=r["from_name"] if incoming else r["to_name"])
        return label + T.STATUS_SUFFIX.get(r["status"], "")
    return T.KIND_LABELS[r["kind"]]


def _menu_rows(member: dict[str, Any]) -> list[list[dict]]:
    return menu_markup(member)["inline_keyboard"]


def _report_lines(ctx: Ctx, member: dict[str, Any]) -> list[str]:
    rows = reports.recent_for_member(ctx.conn, member["member_id"], RECENT_LIMIT)
    if not rows:
        return []
    return ["", T.ME_REPORTS] + [
        T.ME_REPORT_ENTRY.format(report_no=r["report_no"] or T.ME_REPORT_NO_NUMBER,
                                 date=short_date(r["created_at"].astimezone(IST).date()),
                                 category=r["category_name_hi"] or T.CATEGORY_PENDING_SHORT,
                                 status=T.REPORT_STATUS[r["status"]])
        for r in rows]


def render(ctx: Ctx, member: dict[str, Any]) -> tuple[str, dict]:
    if member["finance_scope"] == "none":        # no money lines, but reports are not money
        return "\n".join([T.NO_FINANCE_PERMISSION] + _report_lines(ctx, member)), menu_markup(member)
    me = member["member_id"]
    p = {"member_id": me}
    lines = [T.ME_TITLE]
    if not member["is_principal"]:          # the principal is the source of funds; no balance shown
        lines.append(_held(ctx.conn.execute(member_balance_sql(), p).fetchone()["balance"]))
    transit = ctx.conn.execute(in_transit_sql(), p).fetchone()["in_transit"]
    if transit > 0:
        lines.append(T.ME_IN_TRANSIT.format(amount=inr(transit)))
    disputed = ctx.conn.execute(disputed_sql(), p).fetchone()["disputed"]
    if disputed > 0:
        lines.append(T.ME_DISPUTED.format(amount=inr(disputed)))

    waiting = ctx.conn.execute(
        """SELECT t.txn_id, t.txn_no, t.amount, g.display_name AS giver
           FROM field.money_txn t JOIN field.member g ON g.member_id = t.from_member_id
           WHERE t.to_member_id = %s AND t.status = 'pending_ack' ORDER BY t.created_at""",
        (me,),
    ).fetchall()
    if waiting:
        lines += ["", T.ME_WAITING] + [T.ME_WAITING_ENTRY.format(txn_no=w["txn_no"], amount=inr(w["amount"]),
                                                                 name=w["giver"]) for w in waiting]

    if member["finance_scope"] == "team":
        team = ctx.conn.execute(
            "SELECT member_id, display_name FROM field.member WHERE parent_member_id = %s AND active ORDER BY display_name",
            (me,),
        ).fetchall()
        if team:
            lines += ["", T.ME_TEAM]
            for t in team:
                bal = ctx.conn.execute(member_balance_sql(), {"member_id": t["member_id"]}).fetchone()["balance"]
                shown = T.TEAM_HELD.format(amount=inr(bal)) if bal >= 0 else T.TEAM_OWED.format(amount=inr(-bal))
                lines.append(T.ME_TEAM_ENTRY.format(name=t["display_name"], balance=shown))

    rows = ctx.conn.execute(
        """SELECT t.txn_no, t.kind, t.status, t.amount, t.txn_date, t.custom_label, t.from_member_id, t.to_member_id,
                  fm.display_name AS from_name, tm.display_name AS to_name,
                  b.name_hi, b.parent_id, p.code AS top_code, p.name_hi AS top_name_hi
           FROM field.money_txn t
           LEFT JOIN field.member fm ON fm.member_id = t.from_member_id
           LEFT JOIN field.member tm ON tm.member_id = t.to_member_id
           LEFT JOIN field.exp_bucket b ON b.bucket_id = t.bucket_id
           LEFT JOIN field.exp_bucket p ON p.bucket_id = COALESCE(b.parent_id, b.bucket_id)
           WHERE (t.from_member_id = %(member_id)s OR t.to_member_id = %(member_id)s) AND t.status <> 'void'
           ORDER BY t.created_at DESC, t.txn_no DESC
           LIMIT %(n)s""",
        {**p, "n": RECENT_LIMIT},
    ).fetchall()
    lines.append("")
    if rows:
        lines.append(T.ME_RECENT)
        lines += [T.ME_ENTRY.format(txn_no=r["txn_no"], date=short_date(r["txn_date"]), amount=inr(r["amount"]),
                                    label=_entry_label(r, me)) for r in rows]
    else:
        lines.append(T.ME_NONE)
    lines += _report_lines(ctx, member)

    buttons = [[btn(T.BTN_ACK_OPEN.format(txn_no=w["txn_no"]), f"m:ack:{UUID(str(w['txn_id'])).hex}")]
               for w in waiting[:WAITING_BUTTONS]]
    actions = ([btn(T.BTN_RETURN, "m:ret")] if not member["is_principal"] else []) + [btn(T.BTN_LAST_MONTH, "m:prev")]
    actions.append(flow_pin.pin_button(ctx.conn, member))
    return "\n".join(lines), keyboard(buttons + [actions] + _menu_rows(member))


def _previous_month(today: date) -> tuple[date, date]:
    last = today.replace(day=1) - timedelta(days=1)
    return last.replace(day=1), last


def render_last_month(ctx: Ctx, member: dict[str, Any]) -> tuple[str, dict]:
    if member["finance_scope"] == "none":
        return T.NO_FINANCE_PERMISSION, menu_markup(member)
    first, last = _previous_month(today_ist(ctx.now))
    p = {"m": member["member_id"], "first": first, "last": last}
    comp = ctx.conn.execute(
        f"""SELECT {balance_components_select('%(m)s')} FROM field.money_txn
            WHERE (from_member_id = %(m)s OR to_member_id = %(m)s) AND txn_date BETWEEN %(first)s AND %(last)s""",
        p,
    ).fetchone()
    where = "t.from_member_id = %(m)s AND t.txn_date BETWEEN %(first)s AND %(last)s"
    top = ctx.conn.execute(
        f"""WITH {sm.spend_cte(where)}
            SELECT b.name_hi, SUM(s.amount)::numeric(14, 2) AS spend, count(*) AS n
            FROM spend s JOIN field.exp_bucket b ON b.bucket_id = s.bucket_id
            GROUP BY b.name_hi ORDER BY spend DESC, b.name_hi LIMIT 3""",
        p,
    ).fetchall()
    count = sum(r["n"] for r in ctx.conn.execute(f"WITH {sm.spend_cte(where)} SELECT count(*) AS n FROM spend", p))
    lines = [T.LAST_MONTH_TITLE.format(month=T.MONTHS[first.month], year=first.year)]
    if not any(comp[k] for k in ("received", "spent", "passed_on", "returned")):
        lines.append(T.LM_NONE)
    else:
        lines += [T.LM_SPENT.format(amount=inr(comp["spent"]), count=count),
                  T.LM_RECEIVED.format(amount=inr(comp["received"])),
                  T.LM_PASSED.format(amount=inr(comp["passed_on"])),
                  T.LM_RETURNED.format(amount=inr(comp["returned"]))]
        if top:
            lines += ["", T.LM_TOP] + [T.LM_TOP_ENTRY.format(name=r["name_hi"], amount=inr(r["spend"])) for r in top]
    return "\n".join(lines), keyboard([[btn(T.BTN_ME, "m:me")]] + _menu_rows(member))


def show(ctx: Ctx, member: dict[str, Any], chat_id: int, message_id: int | None = None) -> str:
    text, markup = render(ctx, member)
    if message_id is not None:
        shown = edit_or_send(ctx, chat_id, message_id, text, markup)
    else:
        shown = send(ctx, chat_id, text, markup)
    record(ctx, chat_id, shown, "money")                    # a balance card: cleaned up later
    return "me" if member["finance_scope"] != "none" else "no_permission"   # reports still listed


def on_callback(ctx: Ctx, member: dict[str, Any], chat_id: int, message_id: int, data: str,
                sess: Session | None) -> tuple[str, str | None]:
    if data == "m:me":
        return show(ctx, member, chat_id, message_id), None
    if data == "m:prev":
        text, markup = render_last_month(ctx, member)
        record(ctx, chat_id, edit_or_send(ctx, chat_id, message_id, text, markup), "money")
        return "last_month", None
    if data == "m:ret":
        return flow_return.start(ctx, member, chat_id, message_id, sess)
    if data == flow_pin.CB_SET:
        return flow_pin.start(ctx, member, chat_id)
    if data.startswith("m:ack:"):
        return flow_ack.open_card(ctx, member, chat_id, data[len("m:ack:"):])
    return "card_stale", T.CARD_STALE
