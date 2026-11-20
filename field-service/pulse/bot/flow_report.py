"""Ground reports by bot (LLD §5.3).

The draft is a real field.report row (status 'draft'); the session (flow 'report') holds its id,
the card's message id and the rolling window. One card per report, edited in place as items arrive.

    draft card   p:<sid>:<v>:c:<category>    then   p:<sid>:<v>:u:1|0   → filed
    filed card   q:<report hex>:<op>         (works without a session: also on auto-file notices)
                 c:<id> set category while it is still unset · pl change place · a:<i> pick area
                 bk back · lo send location · mo add more (10 minutes)

Media within the join window (3 minutes after the previous item), or from the same album, joins
the open report; after a longer gap an unfinished draft is filed ("superseded") and a new one opens.
While a draft is unfiled every typed text joins its notes. A report is never refused.
"""
from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from pulse.bot import cards, flow_ack
from pulse.bot import session as sessions
from pulse.bot import texts_hi as T
from pulse.bot.client import TelegramApiError
from pulse.bot.session import Session
from pulse.bot.outgoing import Confirm
from pulse.bot.ui import Ctx, btn, edit_or_send, keyboard, record, send, two_columns, edit
from pulse.common.text import normalise
from pulse.reports import classify, media
from pulse.reports import service as reports
from pulse.worker import queue

log = logging.getLogger(__name__)

FLOW = "report"
S_DRAFT, S_URGENCY, S_FILED = "draft", "urgency", "filed"
NOTE_PREVIEW = 120
SESSION_TTL = timedelta(minutes=30)
_URGENT = {normalise(w) for w in T.URGENT_WORDS}


# ---- helpers ----------------------------------------------------------------------------------

def is_urgent(text: str | None) -> tuple[bool, str]:
    """A caption or text starting with an urgent word, and the text after it."""
    if not text:
        return False, ""
    words = text.strip().split(maxsplit=1)
    if words and normalise(words[0].strip(":!,.-")) in _URGENT:
        return True, (words[1] if len(words) > 1 else "").lstrip(":!,.- ")
    return False, text


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _when(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _save(ctx: Ctx, chat_id: int, state: str, d: dict[str, Any]) -> None:
    sessions.save(ctx.conn, chat_id, FLOW, state, d, ttl=SESSION_TTL, now=ctx.now)


def _new_payload(report_id: UUID) -> dict[str, Any]:
    return {"sid": secrets.token_hex(3), "v": 0, "card": None, "report_id": str(report_id), "last_item_at": None,
            "groups": [], "notified": [], "add_until": None, "await_location": False, "suggested": None}


def _area_name(ctx: Ctx, report: dict[str, Any]) -> str | None:
    if report["area_id"]:
        a = next((a for a in ctx.core.list_areas(report["ac_id"]) if a.area_id == report["area_id"]), None)
        return a.name_hi if a else None
    if report["block_id"]:
        b = next((b for b in ctx.core.list_blocks(report["ac_id"]) if b.block_id == report["block_id"]), None)
        return b.name_hi if b else None
    return None


def _items_line(s: reports.Summary) -> str:
    c, r = s.counts, s.report
    parts = []
    for key, template in (("photo", T.ITEM_PHOTO), ("voice", T.ITEM_VOICE), ("video", T.ITEM_VIDEO),
                          ("document", T.ITEM_DOC)):
        if c[key]:
            parts.append(template.format(n=c[key]))
    if r["lat"] is not None:
        parts.append(T.ITEM_LOCATION)
    if r["note_text"]:
        parts.append(T.ITEM_NOTE)
    line = T.REPORT_RECEIVED.format(items=T.ITEMS_JOIN.join(parts))
    return line


def _common_lines(ctx: Ctx, s: reports.Summary) -> list[str]:
    lines = [_items_line(s), T.REPORT_AREA.format(area=_area_name(ctx, s.report) or "—")]
    if s.report["note_text"]:
        note = s.report["note_text"]
        lines.append(T.REPORT_NOTE_PREVIEW.format(text=note if len(note) <= NOTE_PREVIEW else note[:NOTE_PREVIEW] + "…"))
    return lines


def _category_buttons(ctx: Ctx, data_for, suggested: int | None) -> list[list[dict]]:
    cats = reports.categories(ctx.conn)
    first = [c for c in cats if c["category_id"] == suggested]
    rest = [c for c in cats if c["category_id"] != suggested]
    rows = [[btn(f"{c['name_hi']} {T.SUGGESTED}", data_for(c["category_id"]))] for c in first]
    return rows + two_columns([btn(c["name_hi"], data_for(c["category_id"])) for c in rest])


# ---- rendering --------------------------------------------------------------------------------

def render_draft(ctx: Ctx, d: dict[str, Any], state: str) -> tuple[str, dict]:
    s = reports.summary(ctx.conn, UUID(d["report_id"]))
    lines = _common_lines(ctx, s)
    if state == S_URGENCY:
        lines += [T.REPORT_CATEGORY_LINE.format(name=s.category["name_hi"]), "", T.ASK_URGENT]
        return "\n".join(lines), keyboard([[btn(T.BTN_URGENT_YES, cards.cb("p", d, "u:1")),
                                            btn(T.BTN_URGENT_NO, cards.cb("p", d, "u:0"))]])
    suggestion = classify.suggest_category(ctx.conn, s.report["note_text"])
    d["suggested"] = suggestion.bucket_id if suggestion else None
    lines += ["", T.ASK_CATEGORY]
    return "\n".join(lines), keyboard(_category_buttons(ctx, lambda cid: cards.cb("p", d, f"c:{cid}"), d["suggested"]))


def render_filed_for(conn, core, report_id, *, header: str | None = None) -> tuple[str, dict]:
    """render_filed without an update in hand (used when the worker draws a queued notice)."""
    from pulse.common.clock import utcnow
    from pulse.config import LedgerLimits

    return render_filed(Ctx(conn=conn, client=None, core=core, limits=LedgerLimits(), now=utcnow()),
                        UUID(str(report_id)), header=header)


def render_filed(ctx: Ctx, report_id: UUID, *, header: str | None = None) -> tuple[str, dict]:
    s = reports.summary(ctx.conn, report_id)
    r = s.report
    key = UUID(str(report_id)).hex
    title = header or (T.REPORT_FILED_URGENT if r["urgency"] == "urgent" else T.REPORT_FILED)
    lines = [title.format(report_no=r["report_no"])] + _common_lines(ctx, s)
    lines.append(T.REPORT_CATEGORY_LINE.format(name=s.category["name_hi"]) if s.category else T.REPORT_CATEGORY_PENDING)
    if r["urgency"] == "urgent" and header is not None:     # notices have their own header
        lines.append(T.REPORT_URGENT_LINE)
    rows = []
    if s.category is None:
        suggestion = classify.suggest_category(ctx.conn, r["note_text"])
        rows += _category_buttons(ctx, lambda cid: f"q:{key}:c:{cid}", suggestion.bucket_id if suggestion else None)
    rows.append([btn(T.BTN_CHANGE_PLACE, f"q:{key}:pl"), btn(T.BTN_SEND_LOCATION, f"q:{key}:lo")])
    rows.append([btn(T.BTN_ADD_MORE, f"q:{key}:mo")])
    return "\n".join(lines), keyboard(rows)


def _show(ctx: Ctx, chat_id: int, d: dict[str, Any], state: str, *, cb_message_id: int | None = None) -> None:
    """Render the report's card and put it on screen: edit in place, never one message per item.
    If an edit fails (e.g. Telegram rate-limits a fast album), a coalesced refresh job retries it."""
    d["v"] += 1
    report_id = UUID(d["report_id"])
    if state == S_FILED:
        text, markup = render_filed(ctx, report_id)
    else:
        text, markup = render_draft(ctx, d, state)
    target = cb_message_id if cb_message_id is not None else d.get("card")
    confirms = Confirm("report", report_id=report_id)
    if target is None:
        d["card"] = send(ctx, chat_id, text, markup, confirms=confirms)
        record(ctx, chat_id, d["card"], "report_card", report_id=report_id)
    elif cb_message_id is not None:
        d["card"] = edit_or_send(ctx, chat_id, target, text, markup, confirms=confirms)
    else:
        def refresh_later(conn, rid=d["report_id"], now=ctx.now):
            queue.enqueue(conn, "report_card", {"chat_id": chat_id, "report_id": rid},
                          run_after=now + timedelta(seconds=2), dedupe_key=f"report_card:{rid}")
        edit(ctx, chat_id, target, text, markup, on_fail=refresh_later)    # e.g. a fast album is rate-limited
    _save(ctx, chat_id, state, d)


def refresh_card(ctx: Ctx, chat_id: int, report_id: UUID) -> str:
    """The report_card job: bring the card up to date. Sends it anew only if it can no longer be edited."""
    sess = sessions.load(ctx.conn, chat_id)
    if sess is None or sess.flow != FLOW or sess.payload.get("report_id") != str(report_id):
        return "gone"
    d = sess.payload
    d["v"] += 1
    text, markup = render_filed(ctx, report_id) if sess.state == S_FILED else render_draft(ctx, d, sess.state)
    try:
        ctx.client.edit_message_text(chat_id, d["card"], text, reply_markup=markup)
    except TelegramApiError as e:
        if e.error_code == 429:
            raise                                    # retried with backoff by the queue
        d["card"] = send(ctx, chat_id, text, markup)
    _save(ctx, chat_id, sess.state, d)
    return "refreshed"


# ---- intake -----------------------------------------------------------------------------------

def _open(ctx: Ctx, member: dict[str, Any], chat_id: int, sess: Session | None) -> dict[str, Any]:
    if cards.is_open(sess, ctx) and sess.flow != FLOW:
        cards.abandon(ctx, chat_id, sess)
    report = reports.open_draft(ctx.conn, ctx.core, member, now=ctx.now)
    return _new_payload(report["report_id"])


def _target(ctx: Ctx, member: dict[str, Any], chat_id: int, sess: Session | None,
            group: str | None) -> tuple[dict[str, Any], dict[str, Any], bool]:
    """(payload, report row, after_filing) for an incoming item: the open report if the item still
    belongs to it, else a new draft (filing an unfinished one first)."""
    if sess is not None and sess.flow == FLOW and sess.payload.get("report_id"):
        d = sess.payload
        report = reports.get(ctx.conn, UUID(d["report_id"]), lock=True)
        last = _when(d.get("last_item_at"))
        in_window = last is not None and ctx.now - last <= timedelta(seconds=ctx.reports.join_window_seconds)
        same_group = group is not None and group in d.get("groups", [])
        if report["status"] == "draft":
            if in_window or same_group or last is None:
                return d, report, False
            filed = reports.autofile(ctx.conn, ctx.core, report["report_id"], now=ctx.now, how="superseded")
            _tell_filed(ctx, chat_id, filed, d, T.REPORT_SUPERSEDED)
        else:
            adding = _when(d.get("add_until"))
            if same_group or in_window or (adding is not None and ctx.now <= adding):
                return d, report, True
    d = _open(ctx, member, chat_id, sess)
    return d, reports.get(ctx.conn, UUID(d["report_id"]), lock=True), False


def _notice_once(ctx: Ctx, chat_id: int, d: dict[str, Any], key: str, text: str) -> None:
    """One notice per album and reason, never one per item."""
    if key in d.setdefault("notified", []):
        return
    d["notified"].append(key)
    send(ctx, chat_id, text)


def refusal_text(ctx: Ctx, reason: str) -> str:
    return {"too_big": T.MEDIA_TOO_BIG.format(mb=ctx.reports.max_download_bytes // (1024 * 1024)),
            "image_unsupported": T.MEDIA_IMAGE_UNSUPPORTED, "type_unsupported": T.MEDIA_TYPE_UNSUPPORTED}[reason]


def on_media(ctx: Ctx, member: dict[str, Any], chat_id: int, message: dict[str, Any], sess: Session | None) -> str | None:
    """A photo, voice note, video, document or location. None if the message carries none of these."""
    found = media.describe(message, ctx.reports.max_download_bytes)
    if found is None:
        return None
    caption = message.get("caption")
    group = message.get("media_group_id")

    if isinstance(found, media.Location) and sess is not None and sess.flow == FLOW and sess.payload.get("await_location"):
        return _location_for_filed(ctx, chat_id, sess, found)
    if isinstance(found, media.Refusal):
        if not caption:
            if sess is not None and sess.flow == FLOW:
                _notice_once(ctx, chat_id, sess.payload, f"{group or message.get('message_id')}:{found.reason}",
                             refusal_text(ctx, found.reason))
                _save(ctx, chat_id, sess.state, sess.payload)
            else:
                send(ctx, chat_id, refusal_text(ctx, found.reason))
            return "media_refused"

    d, report, after = _target(ctx, member, chat_id, sess, group)
    rid = report["report_id"]
    outcome = "report_item"
    if isinstance(found, media.Refusal):
        reports.refuse_item(ctx.conn, rid, found.reason, actor=member["member_id"], now=ctx.now)
        _notice_once(ctx, chat_id, d, f"{group or message.get('message_id')}:{found.reason}", refusal_text(ctx, found.reason))
        outcome = "media_refused"
    elif isinstance(found, media.Location):
        reports.set_location(ctx.conn, rid, found.lat, found.lon, actor=member["member_id"], now=ctx.now)
    else:
        try:
            reports.check_room(ctx.conn, rid, found.kind, found.duration_s, ctx.reports)
            asset = media.register(ctx.conn, member, found, now=ctx.now)
            reports.add_media(ctx.conn, rid, asset["asset_id"], actor=member["member_id"], now=ctx.now, after_filing=after)
        except reports.LimitReached as e:
            reports.refuse_item(ctx.conn, rid, f"limit_{e.kind}", actor=member["member_id"], now=ctx.now)
            text = (T.PHOTO_LIMIT.format(n=ctx.reports.max_photos) if e.kind == "photos"
                    else T.VOICE_LIMIT.format(minutes=ctx.reports.max_voice_seconds // 60))
            _notice_once(ctx, chat_id, d, f"{group or rid}:limit_{e.kind}", text)
            outcome = "media_refused"

    if group and group not in d["groups"]:
        d["groups"].append(group)
    d["last_item_at"] = _iso(ctx.now)
    if after:
        state = S_FILED
    elif (sess is not None and sess.flow == FLOW and sess.payload.get("report_id") == d["report_id"]
          and sess.state == S_URGENCY):
        state = S_URGENCY                                  # keep the question that is on screen
    else:
        state = S_DRAFT
    if caption:
        state = _take_text(ctx, member, chat_id, d, caption, filed=after) or state
    _show(ctx, chat_id, d, state)
    return outcome


def _take_text(ctx: Ctx, member: dict[str, Any], chat_id: int, d: dict[str, Any], text: str, *,
               filed: bool) -> str | None:
    """Add text to the report; an urgent word at the start of a draft's text files it at once.
    Returns the new state if it changed."""
    rid = UUID(d["report_id"])
    urgent, rest = is_urgent(text)
    reports.add_note(ctx.conn, rid, rest if urgent else text, actor=member["member_id"], now=ctx.now)
    if urgent and not filed:
        reports.set_urgency(ctx.conn, rid, "urgent", source="user", actor=member["member_id"], now=ctx.now)
        reports.file(ctx.conn, ctx.core, rid, how="urgent", actor=member["member_id"], now=ctx.now)
        return S_FILED
    return None


def on_text(ctx: Ctx, member: dict[str, Any], chat_id: int, text: str, sess: Session | None) -> str | None:
    """While a draft is open every text joins it (numbers too); after filing only during "add more"."""
    if sess is None or sess.flow != FLOW or not sess.payload.get("report_id"):
        return None
    d = sess.payload
    report = reports.get(ctx.conn, UUID(d["report_id"]), lock=True)
    if report["status"] == "draft":
        d["last_item_at"] = _iso(ctx.now)
        new_state = _take_text(ctx, member, chat_id, d, text, filed=False)
        _show(ctx, chat_id, d, new_state or sess.state)
        return "report_note"
    adding = _when(d.get("add_until"))
    if adding is not None and ctx.now <= adding:
        reports.add_note(ctx.conn, report["report_id"], text, actor=member["member_id"], now=ctx.now)
        d["last_item_at"] = _iso(ctx.now)
        _show(ctx, chat_id, d, S_FILED)
        return "report_note"
    return None


def try_urgent_text(ctx: Ctx, member: dict[str, Any], chat_id: int, text: str, sess: Session | None) -> str | None:
    """"turant ..." with no report open: a text-only report, filed at once as urgent."""
    urgent, _ = is_urgent(text)
    if not urgent:
        return None
    d = _open(ctx, member, chat_id, sess)
    d["last_item_at"] = _iso(ctx.now)
    _take_text(ctx, member, chat_id, d, text, filed=False)
    _show(ctx, chat_id, d, S_FILED)
    return "report_urgent"


def adopt(ctx: Ctx, member: dict[str, Any], chat_id: int, held: list[dict[str, Any]], *,
          notice: str | None = None) -> str | None:
    """Open a report from items held during an expense draft (nothing sent is ever lost)."""
    if not held:
        return None
    report = reports.open_draft(ctx.conn, ctx.core, member, now=ctx.now)
    rid = report["report_id"]
    d = _new_payload(rid)
    for h in held:
        if "asset_id" in h:
            reports.add_media(ctx.conn, rid, UUID(h["asset_id"]), actor=member["member_id"], now=ctx.now)
            if h.get("media_group_id") and h["media_group_id"] not in d["groups"]:
                d["groups"].append(h["media_group_id"])
        elif "lat" in h:
            reports.set_location(ctx.conn, rid, h["lat"], h["lon"], actor=member["member_id"], now=ctx.now)
        if h.get("caption"):
            reports.add_note(ctx.conn, rid, h["caption"], actor=member["member_id"], now=ctx.now)
    d["last_item_at"] = _iso(ctx.now)
    if notice:
        send(ctx, chat_id, notice)
    _show(ctx, chat_id, d, S_DRAFT)
    return "report_adopted"


# ---- buttons ----------------------------------------------------------------------------------

def on_callback(ctx: Ctx, member: dict[str, Any], chat_id: int, message_id: int, data: str,
                sess: Session | None) -> tuple[str, str | None]:
    """Draft-card taps: p:<sid>:<v>:c:<id> and p:<sid>:<v>:u:1|0."""
    parsed = cards.parse(data)
    if parsed is None or sess is None or sess.flow != FLOW or sess.payload.get("sid") != parsed[0]:
        return "card_stale", T.CARD_STALE
    sid, v, op = parsed
    d = sess.payload
    if v != str(d["v"]):
        return "card_stale", T.CARD_STALE
    rid = UUID(d["report_id"])
    report = reports.get(ctx.conn, rid, lock=True)
    if report["status"] != "draft":
        return "card_stale", T.CARD_STALE
    name, _, arg = op.partition(":")
    if sess.state == S_DRAFT and name == "c" and arg.isdigit():
        try:
            reports.set_category(ctx.conn, rid, int(arg), actor=member["member_id"], now=ctx.now,
                                 suggested_id=d.get("suggested"))
        except reports.ReportError:
            return "card_stale", T.CARD_STALE
        _show(ctx, chat_id, d, S_URGENCY, cb_message_id=message_id)
        return "report_category", None
    if sess.state == S_URGENCY and name == "u" and arg in ("0", "1"):
        reports.set_urgency(ctx.conn, rid, "urgent" if arg == "1" else "normal", source="user",
                            actor=member["member_id"], now=ctx.now)
        reports.file(ctx.conn, ctx.core, rid, how="user", actor=member["member_id"], now=ctx.now)
        _show(ctx, chat_id, d, S_FILED, cb_message_id=message_id)
        return "report_filed", None
    return "card_stale", T.CARD_STALE


def _area_options(ctx: Ctx, member: dict[str, Any]) -> list[list[str | None]]:
    from pulse.bot.flow_expense import _area_options as expense_area_options   # same rule as expenses
    return expense_area_options(ctx, member)


def _ensure_session(ctx: Ctx, chat_id: int, sess: Session | None, report_id: UUID) -> dict[str, Any]:
    if sess is not None and sess.flow == FLOW and sess.payload.get("report_id") == str(report_id):
        return sess.payload
    if cards.is_open(sess, ctx) and sess.flow != FLOW:
        cards.abandon(ctx, chat_id, sess)
    return _new_payload(report_id)


def on_report_callback(ctx: Ctx, member: dict[str, Any], chat_id: int, message_id: int, data: str,
                       sess: Session | None) -> tuple[str, str | None]:
    """Filed-report taps: q:<report hex>:<op>. Only the reporter; works without an open session."""
    parts = data.split(":", 2)
    if len(parts) != 3:
        return "card_stale", T.CARD_STALE
    try:
        rid = UUID(hex=parts[1])
    except ValueError:
        return "card_stale", T.CARD_STALE
    report = reports.get(ctx.conn, rid, lock=True)
    if report is None or report["reporter_member_id"] != member["member_id"] or report["status"] == "draft":
        return "card_stale", T.CARD_STALE
    op, _, arg = parts[2].partition(":")
    key = rid.hex
    if report["status"] == "duplicate" and op in ("c", "pl", "a", "lo", "mo"):
        # merged into another report meanwhile: an old card must not change or add to the merged copy
        into = reports.get(ctx.conn, report["duplicate_of"]) if report["duplicate_of"] else None
        return "report_merged_already", T.REPORT_MERGED_ALREADY.format(report_no=(into or report)["report_no"])

    def redraw() -> None:
        text, markup = render_filed(ctx, rid)
        edit_or_send(ctx, chat_id, message_id, text, markup)

    if op == "c" and arg.isdigit():
        if report["category_id"] is not None:
            return "report_category_set_already", T.CATEGORY_ALREADY_SET
        try:
            reports.set_category(ctx.conn, rid, int(arg), actor=member["member_id"], now=ctx.now)
        except reports.ReportError:
            return "card_stale", T.CARD_STALE
        redraw()
        return "report_category", None
    if op == "pl":
        options = _area_options(ctx, member)
        names = {(b, a): name for b, a, name in _option_names(ctx, report, options)}
        grid = two_columns([btn(names[(o[0], o[1])], f"q:{key}:a:{i}") for i, o in enumerate(options)])
        edit_or_send(ctx, chat_id, message_id, T.ASK_REPORT_AREA, keyboard(grid + [[btn(T.BTN_BACK, f"q:{key}:bk")]]))
        return "report_pick_area", None
    if op == "a" and arg.isdigit():
        options = _area_options(ctx, member)
        if int(arg) >= len(options):
            return "card_stale", T.CARD_STALE
        block_id, area_id = options[int(arg)]
        if report["issue_id"] is not None and area_id != report["area_id"]:
            # an issue holds reports of one area; moving a linked report would break that
            issue = ctx.conn.execute("SELECT issue_no FROM field.issue WHERE issue_id = %s", (report["issue_id"],)).fetchone()
            return "report_area_locked", T.REPORT_AREA_LOCKED.format(issue_no=issue["issue_no"] if issue else "")
        reports.set_area(ctx.conn, ctx.core, rid, block_id, area_id, "chosen", actor=member["member_id"], now=ctx.now)
        redraw()
        return "report_area", None
    if op == "bk":
        redraw()
        return "report_card", None
    if op == "lo":
        d = _ensure_session(ctx, chat_id, sess, rid)
        d["await_location"] = True
        _save(ctx, chat_id, S_FILED, d)
        send(ctx, chat_id, T.ASK_LOCATION, {
            "keyboard": [[{"text": T.BTN_LOCATION_REQUEST, "request_location": True}]],
            "resize_keyboard": True, "one_time_keyboard": True})
        return "report_ask_location", None
    if op == "mo":
        d = _ensure_session(ctx, chat_id, sess, rid)
        d["add_until"] = _iso(ctx.now + timedelta(minutes=ctx.reports.add_more_minutes))
        if d.get("card") is None:
            d["card"] = message_id
        _save(ctx, chat_id, S_FILED, d)
        reports.event(ctx.conn, rid, "more_opened", actor=member["member_id"], now=ctx.now)
        send(ctx, chat_id, T.ADD_MORE_OPEN.format(minutes=ctx.reports.add_more_minutes, report_no=report["report_no"]),
             confirms=Confirm("report", report_id=rid))
        return "report_add_more", None
    return "card_stale", T.CARD_STALE


def _option_names(ctx: Ctx, report: dict[str, Any], options: list[list[str | None]]):
    areas = {a.area_id: a.name_hi for a in ctx.core.list_areas(report["ac_id"])}
    blocks = {b.block_id: b.name_hi for b in ctx.core.list_blocks(report["ac_id"])}
    for block_id, area_id in options:
        yield block_id, area_id, (areas.get(area_id) if area_id else blocks.get(block_id)) or "—"


def _location_for_filed(ctx: Ctx, chat_id: int, sess: Session, loc: media.Location) -> str:
    d = sess.payload
    rid = UUID(d["report_id"])
    report = reports.set_location(ctx.conn, rid, loc.lat, loc.lon, actor=None, now=ctx.now)
    d["await_location"] = False
    _save(ctx, chat_id, sess.state, d)
    send(ctx, chat_id, T.LOCATION_SAVED.format(report_no=report["report_no"] or ""), {"remove_keyboard": True},
         confirms=Confirm("report", report_id=rid))
    if d.get("card"):
        _show(ctx, chat_id, d, sess.state)
    return "report_location"


# ---- filed without the reporter finishing (auto-file, superseded) -----------------------------

def _tell_filed(ctx: Ctx, chat_id: int, report: dict[str, Any], d: dict[str, Any] | None, header: str) -> None:
    text, markup = render_filed(ctx, report["report_id"], header=header)
    if d is not None and d.get("card"):
        edit_or_send(ctx, chat_id, d["card"], text, markup)
    else:
        send(ctx, chat_id, text, markup)


def tell_autofiled(ctx: Ctx, report: dict[str, Any]) -> None:
    """Worker: a draft filed itself. Its card becomes the filed notice (category still settable),
    through the queue like every message the worker sends."""
    from pulse.notify import outbox

    chat_id = flow_ack.member_chat(ctx, report["reporter_member_id"])
    sess = sessions.load(ctx.conn, chat_id) if chat_id is not None else None
    d = sess.payload if sess is not None and sess.flow == FLOW and sess.payload.get("report_id") == str(report["report_id"]) else None
    if d is not None and d.get("card"):
        outbox.enqueue_raw_edit(ctx.conn, chat_id, d["card"], template="report_filed_notice",
                                report_id=report["report_id"], header="autofiled")
        _save(ctx, chat_id, S_FILED, d)
    else:
        outbox.enqueue(ctx.conn, recipient=report["reporter_member_id"], kind="notice", ac_id=report["ac_id"],
                       report_id=report["report_id"], body=outbox.body("report_filed_notice", header="autofiled"))


def tell_media_failed(ctx: Ctx, report: dict[str, Any]) -> None:
    from pulse.notify import outbox

    outbox.enqueue(ctx.conn, recipient=report["reporter_member_id"], kind="notice", ac_id=report["ac_id"],
                   report_id=report["report_id"], body=outbox.body("media_failed"))


# ---- hand-overs from other flows --------------------------------------------------------------

def close_draft(ctx: Ctx, chat_id: int, sess: Session) -> None:
    """Another flow starts while a report draft is open: file it now (never drop it) and say so."""
    d = sess.payload
    sessions.clear(ctx.conn, chat_id)
    report = reports.get(ctx.conn, UUID(d["report_id"]), lock=True)
    if report is not None and report["status"] == "draft":
        report = reports.autofile(ctx.conn, ctx.core, report["report_id"], now=ctx.now, how="superseded")
        _tell_filed(ctx, chat_id, report, d, T.REPORT_SUPERSEDED)


def _member_for_chat(ctx: Ctx, chat_id: int) -> dict[str, Any] | None:
    return ctx.conn.execute(
        """SELECT m.member_id, m.ac_id, m.display_name, m.is_principal, m.finance_scope, m.can_give_advance,
                  m.parent_member_id
           FROM field.tg_link l JOIN field.member m ON m.member_id = l.member_id
           WHERE l.chat_id = %s AND l.revoked_at IS NULL AND m.active""",
        (chat_id,),
    ).fetchone()


def adopt_held_payload(ctx: Ctx, chat_id: int, d: dict[str, Any]) -> None:
    """Items an expense draft was holding open a report when that draft ends (posted, cancelled,
    expired or replaced), so nothing that was sent is lost."""
    held = d.get("held") or []
    if not held:
        return
    member = _member_for_chat(ctx, chat_id)
    if member is not None:
        d["held"] = []
        adopt(ctx, member, chat_id, held, notice=T.HELD_TO_REPORT)


def adopt_held(ctx: Ctx, chat_id: int, sess: Session) -> None:
    adopt_held_payload(ctx, chat_id, sess.payload)
