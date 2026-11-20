"""Expense entry by bot (LLD §5.1): one message, one tap.

State lives in tg_session (flow 'expense'). Every card carries callback data
`e:<sid>:<v>:<op>[:<arg>]`: sid identifies the draft, v the card version, so a tap on an old or
already-used card is recognised and answered politely. The session row is locked for the whole
update, so two taps on "confirm" post exactly one entry.

Button taps edit the card in place. After a typed reply the card is sent again below it and the
old card loses its buttons, so there is always exactly one live card, and it is visible.
"""
from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from pulse.bot import cards, flow_ack, flow_pin
from pulse.bot import session as sessions
from pulse.bot import texts_hi as T
from pulse.bot.format import day_label, inr
from pulse.bot.session import Session
from pulse.bot.ui import Ctx, btn, edit_or_send, keyboard, record, send, strip_buttons, two_columns
from pulse.common import pii
from pulse.common.clock import today_ist
from pulse.common.text import fold_digits
from pulse.ledger import buckets, classify
from pulse.ledger import service as ledger
from pulse.ledger.errors import LedgerError, PiiRejected
from pulse.ledger.parse_amount import AmountError, ParsedAmount, parse_amount
from pulse.metrics.ledger import member_balance_sql

log = logging.getLogger(__name__)

FLOW = "expense"

S_AWAIT_AMOUNT = "await_amount"
S_CONFIRM_AMOUNT = "confirm_amount"
S_PICK_BUCKET = "pick_bucket"
S_PICK_BUCKET_ALL = "pick_bucket_all"
S_AWAIT_LABEL = "await_label"
S_PICK_AREA = "pick_area"
S_CONFIRM = "confirm"
S_WARN = "warn"
S_D_SUBHEAD = "details_subhead"
S_D_DATE = "details_date"
S_D_PAY_MODE = "details_pay_mode"
S_D_FUND = "details_fund"
S_D_REACH = "details_reach"
S_D_REPORT = "details_report"
S_D_RECEIPT = "details_receipt"
S_D_REMARK = "details_remark"
S_POSTED = "posted"

DETAIL_STATES = (S_D_SUBHEAD, S_D_DATE, S_D_PAY_MODE, S_D_FUND, S_D_REACH, S_D_REPORT, S_D_RECEIPT, S_D_REMARK)
REPORT_CHOICES = 5
PAY_MODE_ORDER = ("cash", "upi", "bank_transfer", "cheque", "credit", "in_kind")
FUND_FOR_PRINCIPAL = ("own", "family_business", "party", "supporter_cash", "supporter_in_kind", "other")
FUND_FOR_OTHERS = ("advance", "own_pocket_reimbursable")
REACH_UNIT_ORDER = ("person", "family", "village", "event")
REMARK_MAX = 500
REACH_MAX = 10_000_000
_DATE_RE = re.compile(r"^\s*(\d{1,2})[/.\-](\d{1,2})(?:[/.\-](\d{2}|\d{4}))?\s*$")

Outcome = tuple[str, str | None]   # (outcome recorded in tg_update, callback toast)
VERDICT_OUTCOME = {"stale": "card_stale", "posted": "already_posted", "expired": "card_expired"}


# ---- draft helpers ----------------------------------------------------------------------------

def _new_draft(old: dict[str, Any] | None = None) -> dict[str, Any]:
    return cards.new_draft(
        old,
        amount=None, remark=None,
        bucket_id=None, source=None, conf=None, label=None,
        block_id=None, area_id=None, area_set=False, areas=[],
        txn_date=None, pay_mode=None, fund_source=None,
        reach_count=None, reach_unit=None, reach_unit_pending="person",
        warnings=[], cash_limit=None, txn_id=None, txn_no=None,
        receipt_asset_id=None, held=[], report_id=None, report_no=None, report_opts=[],
    )


def _cb(d: dict[str, Any], op: str) -> str:
    return cards.cb("e", d, op)


def _amount(d: dict[str, Any]) -> Decimal:
    return Decimal(d["amount"])


def _pii_text(kinds: list[str]) -> str:
    return T.PII_REJECTED.format(what=T.PII_JOIN.join(T.PII_KINDS[k] for k in kinds))


def _bucket(ctx: Ctx, member: dict[str, Any], d: dict[str, Any]) -> dict[str, Any] | None:
    return buckets.get_bucket(ctx.conn, d["bucket_id"], member["ac_id"]) if d.get("bucket_id") else None


def _bucket_text(b: dict[str, Any], d: dict[str, Any]) -> str:
    if b["top_code"] == buckets.OTHER_CODE:
        text = T.OTHER_LABEL.format(label=d.get("label") or "")
    elif b["parent_id"] is not None:
        text = b["top_name_hi"] + T.SUBHEAD_SEP + b["name_hi"]
    else:
        text = b["name_hi"]
    if d.get("source") == "rule":
        text += " " + T.SUGGESTED
    return text


def _area_name(ctx: Ctx, member: dict[str, Any], block_id: str | None, area_id: str | None) -> str | None:
    if area_id:
        a = next((a for a in ctx.core.list_areas(member["ac_id"]) if a.area_id == area_id), None)
        return a.name_hi if a else None
    if block_id:
        b = next((b for b in ctx.core.list_blocks(member["ac_id"]) if b.block_id == block_id), None)
        return b.name_hi if b else None
    return None


def _summary(ctx: Ctx, member: dict[str, Any], d: dict[str, Any]) -> str:
    today = today_ist(ctx.now)
    parts = [inr(_amount(d))]
    b = _bucket(ctx, member, d)
    if b:
        parts.append(_bucket_text(b, d))
    parts.append(_area_name(ctx, member, d["block_id"], d["area_id"]) or T.NO_AREA)
    parts.append(day_label(date.fromisoformat(d["txn_date"]) if d["txn_date"] else today, today))
    parts.append(T.PAY_MODES[d["pay_mode"] or "cash"])
    lines = [" · ".join(parts)]
    if d.get("fund_source"):
        lines.append(T.LINE_FUND.format(fund=T.FUND_SOURCES[d["fund_source"]]))
    if d.get("reach_count"):
        lines.append(T.LINE_REACH.format(count=d["reach_count"], unit=T.REACH_UNITS[d["reach_unit"]]))
    if d.get("report_no"):
        lines.append(T.LINE_REPORT.format(report_no=d["report_no"]))
    if d.get("receipt_asset_id"):
        lines.append(T.LINE_RECEIPT)
    if d.get("remark"):
        lines.append(T.LINE_REMARK.format(remark=d["remark"]))
    return "\n".join(lines)


# ---- areas ------------------------------------------------------------------------------------

def _scopes(ctx: Ctx, member: dict[str, Any]) -> list[dict[str, Any]]:
    return ctx.conn.execute(
        """SELECT block_id, area_id FROM field.member_scope WHERE member_id = %s
           ORDER BY block_id NULLS LAST, area_id NULLS FIRST""",
        (member["member_id"],),
    ).fetchall()


def _area_options(ctx: Ctx, member: dict[str, Any]) -> list[list[str | None]]:
    """Each scope area; for a block-only scope, the block itself and every area in it."""
    areas = {a.area_id: a for a in ctx.core.list_areas(member["ac_id"])}
    opts: list[list[str | None]] = []
    for s in _scopes(ctx, member):
        if s["area_id"]:
            a = areas.get(s["area_id"])
            opts.append([s["block_id"] or (a.block_id if a else None), s["area_id"]])
        else:
            opts.append([s["block_id"], None])
            opts.extend([a.block_id, a.area_id] for a in ctx.core.list_areas(member["ac_id"], s["block_id"]))
    unique: list[list[str | None]] = []
    for o in opts:
        if o not in unique:
            unique.append(o)
    return unique


def _resolve_area(ctx: Ctx, member: dict[str, Any], d: dict[str, Any]) -> bool:
    """The member's only scope, else the area used last, else False (ask)."""
    if d["area_set"]:
        return True
    scopes = _scopes(ctx, member)
    chosen: tuple[str | None, str | None] | None = None
    if not scopes:
        chosen = (None, None)
    elif len(scopes) == 1:
        opts = _area_options(ctx, member)
        chosen = (opts[0][0], opts[0][1])
    else:
        last = ctx.conn.execute(
            """SELECT block_id, area_id FROM field.money_txn
               WHERE from_member_id = %s AND kind = 'expense' AND status <> 'void'
                 AND (block_id IS NOT NULL OR area_id IS NOT NULL)
               ORDER BY created_at DESC LIMIT 1""",
            (member["member_id"],),
        ).fetchone()
        if last:
            chosen = (last["block_id"], last["area_id"])
    if chosen is None:
        return False
    d["block_id"], d["area_id"], d["area_set"] = chosen[0], chosen[1], True
    return True


# ---- rendering --------------------------------------------------------------------------------

def _detail_steps(ctx: Ctx, member: dict[str, Any], d: dict[str, Any]) -> list[str]:
    b = _bucket(ctx, member, d)
    steps = []
    if b and b["top_has_subheads"]:
        steps.append(S_D_SUBHEAD)
    steps += [S_D_DATE, S_D_PAY_MODE, S_D_FUND]
    if b and b["ask_reach"]:
        steps.append(S_D_REACH)
    if _report_options(ctx, member):
        steps.append(S_D_REPORT)
    steps += [S_D_RECEIPT, S_D_REMARK]
    return steps


def _report_options(ctx: Ctx, member: dict[str, Any]) -> list[dict[str, Any]]:
    """The member's most recent open reports (filed by them or assigned to them)."""
    return ctx.conn.execute(
        """SELECT r.report_id, r.report_no, c.name_hi AS category FROM field.report r
           LEFT JOIN field.report_category c ON c.category_id = r.category_id
           WHERE r.ac_id = %s AND (r.reporter_member_id = %s OR r.assigned_to = %s)
             AND r.status IN ('new', 'acknowledged', 'assigned', 'in_progress')
           ORDER BY r.filed_at DESC LIMIT %s""",
        (member["ac_id"], member["member_id"], member["member_id"], REPORT_CHOICES),
    ).fetchall()


def _fund_options(member: dict[str, Any]) -> tuple[str, ...]:
    return FUND_FOR_PRINCIPAL if member["is_principal"] else FUND_FOR_OTHERS


def _skip_cancel(d: dict[str, Any]) -> list[dict]:
    return [btn(T.BTN_SKIP, _cb(d, "sk")), btn(T.BTN_CANCEL, _cb(d, "x"))]


def _render(ctx: Ctx, member: dict[str, Any], d: dict[str, Any], state: str) -> tuple[str, dict | None]:
    cancel = [btn(T.BTN_CANCEL, _cb(d, "x"))]
    if state == S_AWAIT_AMOUNT:
        return T.ASK_AMOUNT, keyboard([cancel])
    amount = inr(_amount(d))
    if state == S_CONFIRM_AMOUNT:
        return T.CONFIRM_BIG_AMOUNT.format(amount=amount), keyboard([[btn(T.BTN_YES_CORRECT, _cb(d, "ya"))], cancel])
    if state in (S_PICK_BUCKET, S_PICK_BUCKET_ALL):
        if state == S_PICK_BUCKET:
            rows = buckets.most_used(ctx.conn, member["member_id"], member["ac_id"], 6)
            extra = [btn(T.BTN_MORE_BUCKETS, _cb(d, "more")), btn(T.BTN_OTHER, _cb(d, "oth"))]
        else:
            rows = [b for b in buckets.top_buckets(ctx.conn, member["ac_id"]) if b["code"] != buckets.OTHER_CODE]
            extra = [btn(T.BTN_OTHER, _cb(d, "oth"))]
        grid = two_columns([btn(b["name_hi"], _cb(d, f"b:{b['bucket_id']}")) for b in rows])
        return T.PICK_BUCKET.format(amount=amount), keyboard(grid + [extra, cancel])
    if state == S_AWAIT_LABEL:
        return T.ASK_LABEL.format(amount=amount), keyboard([cancel])
    if state == S_PICK_AREA:
        grid = two_columns([
            btn(_area_name(ctx, member, o[0], o[1]) or T.NO_AREA, _cb(d, f"a:{i}")) for i, o in enumerate(d["areas"])
        ])
        return T.PICK_AREA.format(amount=amount), keyboard(grid + [cancel])

    summary = _summary(ctx, member, d)
    if state == S_CONFIRM:
        rows = [[btn(T.BTN_CONFIRM, _cb(d, "ok"))],
                [btn(T.BTN_CHANGE_BUCKET, _cb(d, "cb"))]
                + ([btn(T.BTN_CHANGE_AREA, _cb(d, "ca"))] if len(_area_options(ctx, member)) > 1 else []),
                [btn(T.BTN_DETAILS, _cb(d, "det")), btn(T.BTN_CANCEL, _cb(d, "x"))]]
        return summary, keyboard(rows)
    if state == S_WARN:
        limits = {"large": inr(ctx.limits.large_entry),
                  "cash_over_limit": inr(Decimal(d["cash_limit"])) if d.get("cash_limit") else "",
                  "sensitive_in_window": ""}
        lines = [T.WARN_HEADER] + [T.WARNINGS[w].format(limit=limits[w]) for w in d["warnings"]]
        rows = [[btn(T.BTN_POST_ANYWAY, _cb(d, "go"))], [btn(T.BTN_BACK, _cb(d, "bk")), btn(T.BTN_CANCEL, _cb(d, "x"))]]
        return "\n".join(lines) + "\n\n" + summary, keyboard(rows)
    if state == S_D_SUBHEAD:
        b = _bucket(ctx, member, d)
        subs = buckets.subheads(ctx.conn, b["top_id"], member["ac_id"])
        grid = two_columns([btn(s["name_hi"], _cb(d, f"s:{s['bucket_id']}")) for s in subs])
        return summary + "\n\n" + T.ASK_SUBHEAD, keyboard(grid + [_skip_cancel(d)])
    if state == S_D_DATE:
        return (summary + "\n\n" + T.ASK_DATE,
                keyboard([[btn(T.TODAY, _cb(d, "d:t")), btn(T.YESTERDAY, _cb(d, "d:y"))], _skip_cancel(d)]))
    if state == S_D_PAY_MODE:
        grid = two_columns([btn(T.PAY_MODES[m], _cb(d, f"p:{i}")) for i, m in enumerate(PAY_MODE_ORDER)])
        return summary + "\n\n" + T.ASK_PAY_MODE, keyboard(grid + [_skip_cancel(d)])
    if state == S_D_FUND:
        grid = two_columns([btn(T.FUND_SOURCES[f], _cb(d, f"f:{i}")) for i, f in enumerate(_fund_options(member))])
        return summary + "\n\n" + T.ASK_FUND, keyboard(grid + [_skip_cancel(d)])
    if state == S_D_REACH:
        unit = d.get("reach_unit_pending") or "person"
        grid = two_columns([btn(("✓ " if u == unit else "") + T.REACH_UNITS[u], _cb(d, f"r:{i}"))
                            for i, u in enumerate(REACH_UNIT_ORDER)])
        return summary + "\n\n" + T.ASK_REACH.format(unit=T.REACH_UNITS[unit]), keyboard(grid + [_skip_cancel(d)])
    if state == S_D_REPORT:
        opts = _report_options(ctx, member)
        d["report_opts"] = [[str(o["report_id"]), o["report_no"]] for o in opts]
        grid = [[btn(f"{o['report_no']} · {o['category'] or T.CATEGORY_PENDING_SHORT}", _cb(d, f"rp:{i}"))]
                for i, o in enumerate(opts)]
        return summary + "\n\n" + T.ASK_REPORT_LINK, keyboard(grid + [_skip_cancel(d)])
    if state == S_D_RECEIPT:
        return summary + "\n\n" + T.ASK_RECEIPT, keyboard([_skip_cancel(d)])
    if state == S_D_REMARK:
        return summary + "\n\n" + T.ASK_REMARK, keyboard([_skip_cancel(d)])
    if state == S_POSTED:
        lines = [T.POSTED.format(txn_no=d["txn_no"]), summary]
        if not member["is_principal"]:
            bal = ctx.conn.execute(member_balance_sql(), {"member_id": member["member_id"]}).fetchone()["balance"]
            lines.append(T.BALANCE_HELD.format(amount=inr(bal)) if bal >= 0 else T.BALANCE_OWED.format(amount=inr(-bal)))
        undo = btn(T.BTN_UNDO, "u:" + UUID(d["txn_id"]).hex)
        return "\n".join(lines), keyboard([[undo]])
    raise ValueError(f"no card for state {state!r}")


def _show(ctx: Ctx, member: dict[str, Any], chat_id: int, d: dict[str, Any], state: str, *,
          cb_message_id: int | None = None, prefix: str | None = None) -> None:
    """Render the card for `state`, edit it in place (button tap) or re-send it (typed reply), save."""
    cards.show(ctx, chat_id, FLOW, d, state, lambda dd: _render(ctx, member, dd, state),
               cb_message_id=cb_message_id, prefix=prefix)


# ---- transitions ------------------------------------------------------------------------------

def _after_amount(ctx, member, chat_id, d, **kw) -> str:
    if d["remark"]:
        s = classify.suggest(ctx.conn, member["ac_id"], d["remark"])
        if s is not None:
            d.update(bucket_id=s.bucket_id, source="rule", conf=str(s.conf))
            return _after_bucket(ctx, member, chat_id, d, **kw)
    _show(ctx, member, chat_id, d, S_PICK_BUCKET, **kw)
    return "pick_bucket"


def _after_bucket(ctx, member, chat_id, d, **kw) -> str:
    if not _resolve_area(ctx, member, d):
        d["areas"] = _area_options(ctx, member)
        _show(ctx, member, chat_id, d, S_PICK_AREA, **kw)
        return "pick_area"
    _show(ctx, member, chat_id, d, S_CONFIRM, **kw)
    return "confirm"


def _next_detail(ctx, member, chat_id, d, current: str, **kw) -> str:
    steps = _detail_steps(ctx, member, d)
    i = steps.index(current) if current in steps else -1
    nxt = steps[i + 1] if i + 1 < len(steps) else S_CONFIRM
    _show(ctx, member, chat_id, d, nxt, **kw)
    return nxt


def _input(d: dict[str, Any]) -> ledger.ExpenseInput:
    return ledger.ExpenseInput(
        amount=_amount(d), bucket_id=d["bucket_id"],
        suggest_source=d["source"] or "user",
        suggest_conf=Decimal(d["conf"]) if d.get("source") == "rule" and d.get("conf") else None,
        txn_date=date.fromisoformat(d["txn_date"]) if d["txn_date"] else None,
        block_id=d["block_id"], area_id=d["area_id"], fund_source=d["fund_source"], pay_mode=d["pay_mode"],
        custom_label=d["label"], remark=d["remark"],
        reach_count=d["reach_count"], reach_unit=d["reach_unit"] if d["reach_count"] else None,
        receipt_asset_id=UUID(d["receipt_asset_id"]) if d.get("receipt_asset_id") else None,
        created_via="telegram",
        report_id=UUID(d["report_id"]) if d.get("report_id") else None,
    )


def _post(ctx, member, chat_id, message_id, d, *, force: bool) -> Outcome:
    if flow_pin.locked_reply(ctx, member, chat_id):           # locked since the draft opened: no money action
        return "pin_locked", None
    try:
        prepared = ledger.prepare_expense(ctx.conn, ctx.core, member["member_id"], _input(d),
                                          limits=ctx.limits, now=ctx.now)
        if prepared.warnings and not force:
            d["warnings"] = prepared.warnings
            d["cash_limit"] = str(prepared.window["cash_limit"]) if prepared.window and prepared.window["cash_limit"] else None
            _show(ctx, member, chat_id, d, S_WARN, cb_message_id=message_id)
            return "warn", None
        if not d.pop("pin_ok", False):
            waiting = flow_pin.require(ctx, member, chat_id, prepared.inp.amount)
            if waiting is not None:                       # the draft waits; only a flag is kept, never the PIN
                d["pin_wait"], d["warn_ok"] = True, force
                sessions.save(ctx.conn, chat_id, FLOW, S_WARN if force else S_CONFIRM, d, ttl=cards.ttl(ctx), now=ctx.now)
                return waiting, None
        d.pop("pin_wait", None)
        row = ledger.create_expense(ctx.conn, ctx.core, member["member_id"], _input(d), limits=ctx.limits, now=ctx.now)
    except PiiRejected as e:
        _show(ctx, member, chat_id, d, S_CONFIRM, cb_message_id=message_id, prefix=_pii_text(e.kinds))
        return "pii_rejected", None
    except LedgerError:
        log.warning("expense rejected by ledger", exc_info=True)
        return "not_saved", T.NOT_SAVED
    d.update(txn_id=str(row["txn_id"]), txn_no=row["txn_no"])
    _show(ctx, member, chat_id, d, S_POSTED, cb_message_id=message_id)
    _release_held(ctx, chat_id, d)
    return "posted", None


def post_after_pin(ctx: Ctx, member: dict[str, Any], chat_id: int, d: dict[str, Any]) -> Outcome:
    """The PIN for this draft was just given (pulse/bot/flow_pin.py)."""
    return _post(ctx, member, chat_id, d.get("card"), d, force=bool(d.get("warn_ok")))


def _cancel(ctx: Ctx, chat_id: int, d: dict[str, Any], message_id: int | None) -> str:
    return cards.cancel(ctx, chat_id, d, message_id)


def _expire(ctx: Ctx, chat_id: int, sess: Session) -> None:
    d = sess.payload
    sessions.clear(ctx.conn, chat_id)
    strip_buttons(ctx, chat_id, d.get("card"))
    if d.get("amount"):
        send(ctx, chat_id, T.DRAFT_EXPIRED.format(amount=inr(_amount(d))))
    else:
        send(ctx, chat_id, T.DRAFT_EXPIRED_NO_AMOUNT)
    _release_held(ctx, chat_id, d)


def _release_held(ctx: Ctx, chat_id: int, d: dict[str, Any]) -> None:
    """Media held while this draft was open becomes a report now that the draft is over."""
    from pulse.bot import flow_report      # late import: flow_report uses this module's area rule

    flow_report.adopt_held_payload(ctx, chat_id, d)


def _no_permission(member: dict[str, Any]) -> bool:
    return member["finance_scope"] == "none"


# ---- entry points -----------------------------------------------------------------------------

def start_from_menu(ctx: Ctx, member: dict[str, Any], chat_id: int, message_id: int) -> Outcome:
    """Menu button "expense": turn the menu message into the amount prompt."""
    if flow_pin.locked_reply(ctx, member, chat_id):
        return "pin_locked", None
    if _no_permission(member):
        edit_or_send(ctx, chat_id, message_id, T.NO_FINANCE_PERMISSION, None)
        return "no_permission", None
    old = sessions.load(ctx.conn, chat_id)
    prefix = None
    if old and old.flow == FLOW and old.state != S_POSTED and old.payload.get("amount") and not old.expired(ctx.now):
        prefix = T.DRAFT_REPLACED.format(amount=inr(Decimal(old.payload["amount"])))
        strip_buttons(ctx, chat_id, old.payload.get("card"))
    d = _new_draft()
    _show(ctx, member, chat_id, d, S_AWAIT_AMOUNT, cb_message_id=message_id, prefix=prefix)
    return "await_amount", None


def start_draft(ctx: Ctx, member: dict[str, Any], chat_id: int, parsed: ParsedAmount, *,
                old: dict[str, Any] | None = None, notice: str | None = None) -> str:
    if _no_permission(member):
        send(ctx, chat_id, T.NO_FINANCE_PERMISSION)
        return "no_permission"
    if flow_pin.locked_reply(ctx, member, chat_id):
        return "pin_locked"
    kinds = pii.screen(parsed.description)
    if kinds:
        send(ctx, chat_id, _pii_text(kinds))
        return "pii_rejected"
    if len(parsed.description) > REMARK_MAX:
        send(ctx, chat_id, T.REMARK_TOO_LONG)
        return "remark_too_long"
    d = _new_draft(old)
    d["held"] = (old or {}).get("held") or []
    d["amount"] = str(parsed.amount)
    d["remark"] = parsed.description or None
    if parsed.needs_confirm:
        _show(ctx, member, chat_id, d, S_CONFIRM_AMOUNT, prefix=notice)
        return "confirm_amount"
    return _after_amount(ctx, member, chat_id, d, prefix=notice)


def on_text(ctx: Ctx, member: dict[str, Any], chat_id: int, text: str, sess: Session | None) -> str | None:
    """Handle typed text. Returns the outcome, or None when this flow has nothing to do with it."""
    if sess is not None and sess.flow == FLOW:
        if sess.state == S_POSTED:
            sess = None                      # finished; keep the row so double taps are recognised
        elif sess.expired(ctx.now):
            _expire(ctx, chat_id, sess)
            sess = None
    else:
        sess = None

    if sess is None:
        try:
            parsed = parse_amount(text, confirm_above=ctx.limits.confirm_amount_above)
        except AmountError as e:
            if _no_permission(member):
                send(ctx, chat_id, T.NO_FINANCE_PERMISSION)
                return "no_permission"
            send(ctx, chat_id, T.AMOUNT_ERRORS[e.reason])
            return "amount_error"
        if parsed is None:
            return None
        return start_draft(ctx, member, chat_id, parsed)

    d, state = sess.payload, sess.state
    if text.strip().lower() in T.CANCEL_WORDS:
        return _cancel(ctx, chat_id, d, None)

    if state == S_AWAIT_AMOUNT:
        try:
            parsed = parse_amount(text, confirm_above=ctx.limits.confirm_amount_above)
        except AmountError as e:
            _show(ctx, member, chat_id, d, state, prefix=T.AMOUNT_ERRORS[e.reason])
            return "amount_error"
        if parsed is None:
            _show(ctx, member, chat_id, d, state, prefix=T.AMOUNT_NOT_UNDERSTOOD)
            return "amount_not_understood"
        return start_draft(ctx, member, chat_id, parsed, old=d)

    if state == S_AWAIT_LABEL:
        label = text.strip()
        if len(label) > ledger.CUSTOM_LABEL_MAX:
            _show(ctx, member, chat_id, d, state, prefix=T.LABEL_TOO_LONG)
            return "label_too_long"
        kinds = pii.screen(label)
        if kinds:
            _show(ctx, member, chat_id, d, state, prefix=_pii_text(kinds))
            return "pii_rejected"
        other = buckets.bucket_by_code(ctx.conn, buckets.OTHER_CODE)
        d.update(bucket_id=other["bucket_id"], label=label, source="user", conf=None)
        return _after_bucket(ctx, member, chat_id, d)

    if state == S_D_DATE:
        parsed_date = parse_date(text, today_ist(ctx.now))
        if parsed_date is None:
            _show(ctx, member, chat_id, d, state, prefix=T.DATE_INVALID)
            return "date_invalid"
        if parsed_date > today_ist(ctx.now):
            _show(ctx, member, chat_id, d, state, prefix=T.DATE_FUTURE)
            return "date_future"
        d["txn_date"] = parsed_date.isoformat()
        return _next_detail(ctx, member, chat_id, d, state)

    if state == S_D_REACH:
        raw = fold_digits(text).strip().replace(",", "")
        if not raw.isdigit() or not 0 < int(raw) <= REACH_MAX:
            _show(ctx, member, chat_id, d, state, prefix=T.REACH_INVALID)
            return "reach_invalid"
        d["reach_count"], d["reach_unit"] = int(raw), d.get("reach_unit_pending") or "person"
        return _next_detail(ctx, member, chat_id, d, state)

    if state == S_D_REMARK:
        remark = text.strip()
        if len(remark) > REMARK_MAX:
            _show(ctx, member, chat_id, d, state, prefix=T.REMARK_TOO_LONG)
            return "remark_too_long"
        kinds = pii.screen(remark)
        if kinds:
            _show(ctx, member, chat_id, d, state, prefix=_pii_text(kinds))
            return "pii_rejected"
        d["remark"] = remark or None
        return _next_detail(ctx, member, chat_id, d, state)

    # a state that waits for a button: a new amount starts a new draft, anything else re-shows the card
    try:
        parsed = parse_amount(text, confirm_above=ctx.limits.confirm_amount_above)
    except AmountError:
        parsed = None
    if parsed is not None:
        notice = T.DRAFT_REPLACED.format(amount=inr(_amount(d))) if d.get("amount") else None
        return start_draft(ctx, member, chat_id, parsed, old=d, notice=notice)
    _show(ctx, member, chat_id, d, state, prefix=T.USE_BUTTONS)
    return "use_buttons"


def on_callback(ctx: Ctx, member: dict[str, Any], chat_id: int, message_id: int, data: str,
                sess: Session | None) -> Outcome:
    verdict, op, toast = cards.check(ctx, chat_id, message_id, FLOW, sess, data, posted_ops=("ok", "go"))
    if verdict != "ok":
        return VERDICT_OUTCOME[verdict], toast
    d, state = sess.payload, sess.state
    if op == "x":
        return _cancel(ctx, chat_id, d, message_id), None

    kw = {"cb_message_id": message_id}
    op_name, _, arg = op.partition(":")

    if state == S_CONFIRM_AMOUNT and op == "ya":
        return _after_amount(ctx, member, chat_id, d, **kw), None

    if state in (S_PICK_BUCKET, S_PICK_BUCKET_ALL):
        if op == "more":
            _show(ctx, member, chat_id, d, S_PICK_BUCKET_ALL, **kw)
            return "pick_bucket_all", None
        if op == "oth":
            _show(ctx, member, chat_id, d, S_AWAIT_LABEL, **kw)
            return "await_label", None
        if op_name == "b" and arg.isdigit():
            b = buckets.get_bucket(ctx.conn, int(arg), member["ac_id"])
            if b and b["parent_id"] is None and b["code"] != buckets.OTHER_CODE:
                d.update(bucket_id=b["bucket_id"], source="user", conf=None, label=None)
                return _after_bucket(ctx, member, chat_id, d, **kw), None

    if state == S_PICK_AREA and op_name == "a" and arg.isdigit() and int(arg) < len(d["areas"]):
        d["block_id"], d["area_id"] = d["areas"][int(arg)]
        d["area_set"] = True
        _show(ctx, member, chat_id, d, S_CONFIRM, **kw)
        return "confirm", None

    if state == S_CONFIRM:
        if op == "ok":
            return _post(ctx, member, chat_id, message_id, d, force=False)
        if op == "cb":
            _show(ctx, member, chat_id, d, S_PICK_BUCKET, **kw)
            return "pick_bucket", None
        if op == "ca":
            d["areas"] = _area_options(ctx, member)
            _show(ctx, member, chat_id, d, S_PICK_AREA, **kw)
            return "pick_area", None
        if op == "det":
            first = _detail_steps(ctx, member, d)[0]
            _show(ctx, member, chat_id, d, first, **kw)
            return first, None

    if state == S_WARN:
        if op == "go":
            return _post(ctx, member, chat_id, message_id, d, force=True)
        if op == "bk":
            _show(ctx, member, chat_id, d, S_CONFIRM, **kw)
            return "confirm", None

    if state in DETAIL_STATES:
        if op == "sk":
            return _next_detail(ctx, member, chat_id, d, state, **kw), None
        if state == S_D_SUBHEAD and op_name == "s" and arg.isdigit():
            b = buckets.get_bucket(ctx.conn, int(arg), member["ac_id"])
            current = _bucket(ctx, member, d)
            if b and current and b["parent_id"] == current["top_id"]:
                d.update(bucket_id=b["bucket_id"], source="user", conf=None)
                return _next_detail(ctx, member, chat_id, d, state, **kw), None
        if state == S_D_DATE and op in ("d:t", "d:y"):
            today = today_ist(ctx.now)
            d["txn_date"] = (today if op == "d:t" else today - timedelta(days=1)).isoformat()
            return _next_detail(ctx, member, chat_id, d, state, **kw), None
        if state == S_D_PAY_MODE and op_name == "p" and arg.isdigit() and int(arg) < len(PAY_MODE_ORDER):
            d["pay_mode"] = PAY_MODE_ORDER[int(arg)]
            return _next_detail(ctx, member, chat_id, d, state, **kw), None
        if state == S_D_FUND and op_name == "f" and arg.isdigit() and int(arg) < len(_fund_options(member)):
            d["fund_source"] = _fund_options(member)[int(arg)]
            return _next_detail(ctx, member, chat_id, d, state, **kw), None
        if state == S_D_REPORT and op_name == "rp" and arg.isdigit() and int(arg) < len(d.get("report_opts") or []):
            d["report_id"], d["report_no"] = d["report_opts"][int(arg)]
            return _next_detail(ctx, member, chat_id, d, state, **kw), None
        if state == S_D_REACH and op_name == "r" and arg.isdigit() and int(arg) < len(REACH_UNIT_ORDER):
            d["reach_unit_pending"] = REACH_UNIT_ORDER[int(arg)]
            _show(ctx, member, chat_id, d, state, **kw)
            return state, None

    return "card_stale", T.CARD_STALE


def on_undo(ctx: Ctx, member: dict[str, Any], chat_id: int, message_id: int, data: str) -> Outcome:
    """Any posted card's undo button (expense, advance, return): void with reason "undo" within the
    undo window. An advance or return can be undone only while the other side has not yet answered;
    its receiver is then told the entry is gone."""
    try:
        txn_id = UUID(hex=data[2:])
    except ValueError:
        return "card_stale", T.CARD_STALE
    txn = ctx.conn.execute("SELECT * FROM field.money_txn WHERE txn_id = %s FOR UPDATE", (txn_id,)).fetchone()
    if txn is None or txn["created_by"] != member["member_id"]:
        return "card_stale", T.CARD_STALE
    if txn["status"] == "void":
        return "already_undone", T.ALREADY_UNDONE
    if txn["kind"] in ("advance", "return") and txn["status"] != "pending_ack":
        return "undo_not_allowed", T.UNDO_NOT_ALLOWED
    if ctx.now - txn["created_at"] > timedelta(minutes=ctx.limits.undo_minutes):
        return "undo_expired", T.UNDO_EXPIRED.format(minutes=ctx.limits.undo_minutes)
    ledger.void(ctx.conn, txn_id, member["member_id"], "undo", limits=ctx.limits, now=ctx.now)
    edit_or_send(ctx, chat_id, message_id, T.UNDONE.format(txn_no=txn["txn_no"]), None)
    if txn["kind"] in ("advance", "return"):
        flow_ack.tell_removed(ctx, txn)
    sess = sessions.load(ctx.conn, chat_id)
    if sess and sess.payload.get("txn_id") == str(txn_id):
        sessions.clear(ctx.conn, chat_id)
    return "undone", T.UNDONE_TOAST


# ---- typed dates ------------------------------------------------------------------------------

def parse_date(text: str, today: date) -> date | None:
    """dd/mm, dd/mm/yy or dd/mm/yyyy (also - or .). Without a year: this year, or last year if that
    would be in the future (so "28/12" typed on 2 January means last December)."""
    m = _DATE_RE.match(fold_digits(text))
    if not m:
        return None
    day, month, year = int(m.group(1)), int(m.group(2)), m.group(3)
    try:
        if year is None:
            d = date(today.year, month, day)
            return d if d <= today else date(today.year - 1, month, day)
        y = int(year)
        return date(2000 + y if y < 100 else y, month, day)
    except ValueError:
        return None


# ---- media while an expense draft is open (receipt step, or hold and ask) ---------------------

HOLD_PREFIX = "h"


def on_media(ctx: Ctx, member: dict[str, Any], chat_id: int, message: dict[str, Any], sess: Session) -> str | None:
    """A photo, file or location arrives while this expense draft is open. In the receipt step a
    photo, image or PDF is the receipt. Otherwise it is stored at once and held on the draft, and one
    question asks whether it is this expense's receipt or a new report. Nothing is lost either way."""
    from pulse.bot import flow_report
    from pulse.reports import media

    found = media.describe(message, ctx.reports.max_download_bytes)
    if found is None:
        return None
    d, state = sess.payload, sess.state
    if isinstance(found, media.Refusal):
        send(ctx, chat_id, flow_report.refusal_text(ctx, found.reason))
        return "media_refused"
    if isinstance(found, media.Location):
        d.setdefault("held", []).append({"lat": found.lat, "lon": found.lon, "caption": message.get("caption")})
    else:
        asset = media.register(ctx.conn, member, found, now=ctx.now)
        entry = {"asset_id": str(asset["asset_id"]), "kind": found.kind, "receipt": found.receipt_capable,
                 "media_group_id": found.media_group_id, "caption": message.get("caption")}
        if state == S_D_RECEIPT and found.receipt_capable and not d.get("receipt_asset_id"):
            d["receipt_asset_id"] = entry["asset_id"]
            if entry["caption"]:
                d.setdefault("held", []).append({"caption": entry["caption"]})
            _next_detail(ctx, member, chat_id, d, state)
            return "receipt_added"
        d.setdefault("held", []).append(entry)
    group = message.get("media_group_id")
    if d.get("asked_group") is not None and group is not None and d["asked_group"] == group:
        sessions.save(ctx.conn, chat_id, FLOW, state, d, ttl=cards.ttl(ctx), now=ctx.now)
        return "held"                                     # one question per album
    d["asked_group"] = group or "single"
    can_receipt = any(h.get("receipt") for h in d["held"]) and not d.get("receipt_asset_id")
    sid = d["sid"]
    rows = ([[btn(T.BTN_HOLD_RECEIPT, f"{HOLD_PREFIX}:{sid}:r")]] if can_receipt else []) + \
           [[btn(T.BTN_HOLD_REPORT, f"{HOLD_PREFIX}:{sid}:n")], [btn(T.BTN_HOLD_LATER, f"{HOLD_PREFIX}:{sid}:l")]]
    record(ctx, chat_id, send(ctx, chat_id, T.HOLD_QUESTION if can_receipt else T.HOLD_QUESTION_NO_RECEIPT,
                              keyboard(rows)), "money")
    sessions.save(ctx.conn, chat_id, FLOW, state, d, ttl=cards.ttl(ctx), now=ctx.now)
    return "held_asked"


def on_hold_callback(ctx: Ctx, member: dict[str, Any], chat_id: int, message_id: int, data: str,
                     sess: Session | None) -> Outcome:
    """h:<sid>:r (it is the receipt) | n (it is a new report) | l (decide after the expense)."""
    from pulse.bot import flow_report

    parts = data.split(":")
    if len(parts) != 3 or sess is None or sess.flow != FLOW or sess.payload.get("sid") != parts[1] \
            or sess.state == S_POSTED:
        return "card_stale", T.CARD_STALE
    d, state, op = sess.payload, sess.state, parts[2]
    held = d.get("held") or []
    d["asked_group"] = None
    if op == "r":
        first = next((h for h in held if h.get("receipt")), None)
        if first is None or d.get("receipt_asset_id"):
            return "card_stale", T.CARD_STALE
        d["receipt_asset_id"] = first["asset_id"]
        d["held"] = [h for h in held if h is not first] + ([{"caption": first["caption"]}] if first.get("caption") else [])
        edit_or_send(ctx, chat_id, message_id, T.HOLD_RECEIPT_SET, None)
        _show(ctx, member, chat_id, d, state, cb_message_id=d.get("card"))
        return "receipt_added", None
    if op == "n":
        edit_or_send(ctx, chat_id, message_id, T.DRAFT_ABANDONED, None)
        sessions.clear(ctx.conn, chat_id)
        strip_buttons(ctx, chat_id, d.get("card"))
        d["held"] = []
        flow_report.adopt(ctx, member, chat_id, held)
        return "held_to_report", None
    if op == "l":
        edit_or_send(ctx, chat_id, message_id, T.HOLD_KEPT, None)
        sessions.save(ctx.conn, chat_id, FLOW, state, d, ttl=cards.ttl(ctx), now=ctx.now)
        return "held_kept", None
    return "card_stale", T.CARD_STALE
