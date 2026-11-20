"""Report alerts (LLD §5.4): who is told, what their card shows, escalation and reporter feedback.

Rules come from field.alert_rule. Recipients are resolved from roles and then filtered by visibility
(pulse/reports/access.py): nobody is ever sent a report they could not open in the API, so a
restricted report reaches only the principal and coordinators. The reporter is never alerted about
their own report; they get feedback messages instead.

Cards are drawn when sent and redrawn after every change, so first acknowledgement wins: everyone
else's card then shows who acknowledged and when, without the acknowledge button.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import psycopg

from pulse.bot import texts_hi as T
from pulse.bot.ui import btn, keyboard, two_columns
from pulse.common.clock import IST
from pulse.core_adapter import CoreAdapter
from pulse.notify import outbox
from pulse.reports import access, duplicates
from pulse.reports import service as reports
from pulse.reports import transcripts

log = logging.getLogger(__name__)

CAPTION_MAX = 1024              # Telegram photo caption limit
TEXT_MAX = 4000
NOTE_IN_CARD = 500


# ---- rules and recipients ---------------------------------------------------------------------

def rules(conn: psycopg.Connection, trigger: str) -> list[dict[str, Any]]:
    return conn.execute("SELECT * FROM field.alert_rule WHERE trigger = %s AND active ORDER BY priority, code",
                        (trigger,)).fetchall()


def rule(conn: psycopg.Connection, code: str) -> dict[str, Any] | None:
    return conn.execute("SELECT * FROM field.alert_rule WHERE code = %s AND active", (code,)).fetchone()


def matches(rule_row: dict[str, Any], report: dict[str, Any]) -> bool:
    return all(report.get(k) == v for k, v in (rule_row["condition"] or {}).items())


def _role_members(conn: psycopg.Connection, ac_id: str, role: str, reporter: dict[str, Any] | None) -> list[UUID]:
    if role == "principal":
        q, p = "SELECT member_id FROM field.member WHERE ac_id = %s AND active AND is_principal", (ac_id,)
    elif role == "coordinators":
        q, p = "SELECT member_id FROM field.member WHERE ac_id = %s AND active AND is_coordinator ORDER BY display_name", (ac_id,)
    elif role == "finance_all":
        q, p = "SELECT member_id FROM field.member WHERE ac_id = %s AND active AND finance_scope = 'all' ORDER BY display_name", (ac_id,)
    elif role == "reporter_parent" and reporter is not None:
        q, p = "SELECT member_id FROM field.member WHERE member_id = %s AND active", (reporter["parent_member_id"],)
    elif role == "next_level_up" and reporter is not None:
        q, p = ("""SELECT g.member_id FROM field.member m JOIN field.member g ON g.member_id = m.parent_member_id
                   WHERE m.member_id = %s AND g.active""", (reporter["parent_member_id"],))
    elif role == "self" and reporter is not None:
        return [reporter["member_id"]]
    else:
        return []
    return [r["member_id"] for r in conn.execute(q, p)]


def recipients(conn: psycopg.Connection, rule_row: dict[str, Any], report: dict[str, Any]) -> list[UUID]:
    reporter = conn.execute("SELECT * FROM field.member WHERE member_id = %s", (report["reporter_member_id"],)).fetchone()

    def resolve(roles: list[str]) -> list[UUID]:
        out: list[UUID] = []
        for role in roles:
            for m in _role_members(conn, report["ac_id"], role, reporter):
                if m in out or m == report["reporter_member_id"]:
                    continue
                v = access.viewer_for(conn, m)
                if v is not None and access.can_see(conn, v, report["report_id"]):
                    out.append(m)
        return out

    return resolve(rule_row["recipients"]) or resolve(rule_row["fallback"] or [])


def _send_cards(conn: psycopg.Connection, rule_row: dict[str, Any], report: dict[str, Any], kind: str,
                **extra: Any) -> int:
    n = 0
    for m in recipients(conn, rule_row, report):
        outbox.enqueue(conn, recipient=m, kind=kind, ac_id=report["ac_id"], report_id=report["report_id"],
                       rule_code=rule_row["code"], body={"render": "report_card", **extra})
        n += 1
    if n == 0:
        reports.event(conn, report["report_id"], "alert_not_delivered", actor=None, now=report["last_activity_at"],
                      data={"rule": rule_row["code"], "reason": "no_recipient"})
    return n


def on_filed(conn: psycopg.Connection, report: dict[str, Any], how: str) -> None:
    """A report was filed: the first matching report_filed rule decides who is told (LLD §5.4).
    An auto-filed report with no category also goes to the coordinators to be sorted."""
    for r in rules(conn, "report_filed"):
        if matches(r, report):
            _send_cards(conn, r, report, "alert")
            break
    if report["category_id"] is None and how in ("auto", "superseded"):
        for r in rules(conn, "report_unsorted"):
            _send_cards(conn, r, report, "unsorted")


def escalate(conn: psycopg.Connection, now: datetime) -> int:
    """Urgent reports still not acknowledged: re-alert at 30 and 60 minutes (rules urgent_escalate_*),
    then mark them escalated for the digest. Each round is sent once. Catch-up: if several rounds are
    overdue (the worker was down), only the latest is sent and the earlier ones are skipped."""
    steps = [r for r in rules(conn, "report_unacked") if r["after_minutes"]]
    steps.sort(key=lambda r: r["after_minutes"])
    if not steps:
        return 0
    due = conn.execute(
        """SELECT * FROM field.report WHERE status = 'new' AND urgency = 'urgent' AND escalation_level < %s
           AND filed_at <= %s ORDER BY filed_at FOR UPDATE SKIP LOCKED""",
        (len(steps), now - timedelta(minutes=steps[0]["after_minutes"])),
    ).fetchall()
    sent = 0
    for report in due:
        waited = now - report["filed_at"]
        level = max(i for i, r in enumerate(steps, start=1) if waited >= timedelta(minutes=r["after_minutes"]))
        if level <= report["escalation_level"]:
            continue
        r = steps[level - 1]
        minutes = int(waited.total_seconds() // 60)
        _send_cards(conn, r, report, "escalation", minutes=minutes)
        reports.mark_escalated(conn, report["report_id"], level, final=level == len(steps), now=now,
                               data={"rule": r["code"], "minutes": minutes,
                                     "skipped_rounds": level - 1 - report["escalation_level"]})
        sent += 1
    return sent


# ---- feedback to the reporter, and the assignee's card ----------------------------------------

def feedback(conn: psycopg.Connection, report: dict[str, Any], what: str, *, into: UUID | None = None) -> None:
    """The reporter hears back at acknowledged and at every closing status (LLD §5.3). The text is
    drawn when sent (pulse/notify/render.py)."""
    if what not in ("acknowledged", "resolved", "closed_no_action", "duplicate"):
        return
    params = {"what": what, **({"into_report_id": into} if into is not None else {})}
    outbox.enqueue(conn, recipient=report["reporter_member_id"], kind="feedback", ac_id=report["ac_id"],
                   report_id=report["report_id"], body=outbox.body("feedback", **params))


def assignee_card(conn: psycopg.Connection, report: dict[str, Any]) -> None:
    outbox.enqueue(conn, recipient=report["assigned_to"], kind="assignee", ac_id=report["ac_id"],
                   report_id=report["report_id"], body={"render": "report_card"})


# ---- drawing a card ---------------------------------------------------------------------------

def _name(conn: psycopg.Connection, member_id: UUID | None) -> str:
    if member_id is None:
        return ""
    row = conn.execute("SELECT display_name FROM field.member WHERE member_id = %s", (member_id,)).fetchone()
    return row["display_name"] if row else ""


def _area(core: CoreAdapter, report: dict[str, Any]) -> str:
    if report["area_id"]:
        return next((a.name_hi for a in core.list_areas(report["ac_id"]) if a.area_id == report["area_id"]), "—")
    if report["block_id"]:
        return next((b.name_hi for b in core.list_blocks(report["ac_id"]) if b.block_id == report["block_id"]), "—")
    return "—"


def when(dt: datetime) -> str:
    return dt.astimezone(IST).strftime("%d-%m %H:%M")


def _reporter_telegram_id(conn: psycopg.Connection, member_id: UUID) -> int | None:
    row = conn.execute("SELECT telegram_user_id FROM field.tg_link WHERE member_id = %s AND revoked_at IS NULL",
                       (member_id,)).fetchone()
    return row["telegram_user_id"] if row else None


def lifecycle_open() -> tuple[str, ...]:
    from pulse.reports.lifecycle import OPEN
    return OPEN


def _manages_issues(conn: psycopg.Connection, member_id: UUID | None) -> bool:
    """The principal and coordinators link reports to issues (pulse/reports/issues.py)."""
    if member_id is None:
        return False
    row = conn.execute("SELECT is_principal OR is_coordinator AS ok FROM field.member WHERE member_id = %s AND active",
                       (member_id,)).fetchone()
    return bool(row and row["ok"])


def first_photo(conn: psycopg.Connection, report_id: UUID) -> str | None:
    row = conn.execute(
        """SELECT a.tg_file_id FROM field.report_media m JOIN field.media_asset a ON a.asset_id = m.asset_id
           WHERE m.report_id = %s AND a.kind = 'photo' AND m.role = 'report' AND a.status <> 'failed'
           ORDER BY m.seq LIMIT 1""",
        (report_id,),
    ).fetchone()
    return row["tg_file_id"] if row else None


def render_card(conn: psycopg.Connection, core: CoreAdapter, alert: dict[str, Any]) -> tuple[str, dict | None, str | None]:
    """(text, keyboard, photo file_id or None) for an alert, escalation, unsorted or assignee card."""
    s = reports.summary(conn, alert["report_id"])
    r, cat = s.report, s.category
    category = cat["name_hi"] if cat else T.CATEGORY_PENDING_SHORT
    kind, body = alert["kind"], alert["body"] or {}
    if kind == "escalation":
        head = T.ALERT_ESCALATION.format(minutes=body.get("minutes", 30), category=category)
    elif kind == "unsorted":
        head = T.ALERT_UNSORTED.format(report_no=r["report_no"])
    elif kind == "assignee":
        head = T.ALERT_ASSIGNEE.format(category=category)
    elif r["restricted"]:
        head = T.ALERT_RESTRICTED.format(category=category)
    elif r["urgency"] == "urgent":
        head = T.ALERT_URGENT.format(category=category)
    else:
        head = T.ALERT_NORMAL.format(category=category)
    lines = [head, T.ALERT_PLACE_TIME.format(area=_area(core, r), time=when(r["filed_at"])),
             T.ALERT_REPORTER.format(name=_name(conn, r["reporter_member_id"]))]
    if r["note_text"]:
        note = r["note_text"]
        lines.append(note if len(note) <= NOTE_IN_CARD else note[:NOTE_IN_CARD] + "…")
    shown = transcripts.display(r)
    if shown.text:
        excerpt = shown.text if len(shown.text) <= transcripts.CARD_CHARS else shown.text[:transcripts.CARD_CHARS] + "…"
        lines.append(T.CARD_TRANSCRIPT_HUMAN.format(text=excerpt) if shown.source == "human"
                     else T.CARD_TRANSCRIPT_MACHINE.format(engine=shown.engine or "?", text=excerpt))
    suggested = None
    if cat is None and r["suggested_category_id"] is not None:
        suggested = conn.execute("SELECT * FROM field.report_category WHERE category_id = %s AND active",
                                 (r["suggested_category_id"],)).fetchone()
        if suggested:
            lines.append(T.CARD_SUGGESTED_CATEGORY.format(category=suggested["name_hi"]))
    if r["suggested_urgency"] == "urgent" and r["urgency"] != "urgent":
        lines.append(T.CARD_SUGGESTED_URGENT)
    lines.append(T.ALERT_REF.format(report_no=r["report_no"]))
    dups = duplicates.suggestions(conn, r["report_id"])
    if dups and r["status"] in ("new", "acknowledged", "assigned", "in_progress"):
        lines.append(T.ALERT_DUPLICATE_HINT.format(numbers=", ".join(d["report_no"] for d in dups[:3])))
    if r["ack_at"] is not None:
        lines.append(T.ALERT_SEEN_BY.format(name=_name(conn, r["ack_by"]), time=when(r["ack_at"])))
    if r["assigned_to"] is not None:
        lines.append(T.ALERT_ASSIGNED_TO.format(name=_name(conn, r["assigned_to"])))
    if r["status"] not in ("new", "acknowledged"):
        lines.append(T.ALERT_STATUS.format(status=T.REPORT_STATUS[r["status"]]))
    if r["issue_id"] is not None:
        issue = conn.execute("SELECT issue_no, title FROM field.issue WHERE issue_id = %s", (r["issue_id"],)).fetchone()
        if issue:
            lines.append(T.CARD_ISSUE.format(issue_no=issue["issue_no"], title=issue["title"]))

    key = UUID(str(r["report_id"])).hex
    rows: list[list[dict]] = []
    if kind == "assignee":
        if r["status"] == "assigned":
            rows.append([btn(T.BTN_WORK_START, f"w:{key}:s")])
        if r["status"] in ("assigned", "in_progress"):
            rows.append([btn(T.BTN_WORK_DONE, f"w:{key}:d"), btn(T.BTN_WORK_CANNOT, f"w:{key}:n")])
    else:
        if kind == "unsorted" and cat is None:
            cats = reports.categories(conn)
            if suggested:                                  # the machine's suggestion first, marked; a person taps
                cats = [suggested] + [c for c in cats if c["category_id"] != suggested["category_id"]]
            rows += two_columns([btn((T.SUGGESTED_MARK if suggested and c["category_id"] == suggested["category_id"]
                                      else "") + c["name_hi"], f"t:{key}:c:{c['category_id']}") for c in cats])
        top = []
        if r["status"] == "new":
            top.append(btn(T.BTN_SEEN, f"t:{key}:a"))
        if r["status"] in ("new", "acknowledged", "assigned", "in_progress"):
            top.append(btn(T.BTN_ASSIGN, f"t:{key}:s"))
        rows.append(top)
        if r["status"] in ("new", "acknowledged", "assigned"):
            rows += [[btn(T.BTN_MERGE_INTO.format(report_no=d["report_no"]), f"t:{key}:m:{i}")]
                     for i, d in enumerate(dups[:2])]
        if (r["issue_id"] is None and r["area_id"] is not None and r["status"] in lifecycle_open()
                and _manages_issues(conn, alert.get("recipient_member_id"))):
            rows.append([btn(T.BTN_ISSUE_LINK, f"t:{key}:i")])
        tg_id = _reporter_telegram_id(conn, r["reporter_member_id"])
        if tg_id is not None and not body.get("no_contact"):
            rows.append([{"text": T.BTN_TALK_TO_REPORTER, "url": f"tg://user?id={tg_id}"}])

    rule_row = rule(conn, alert["rule_code"]) if alert.get("rule_code") else None
    with_media = (not r["restricted"]) and (rule_row is None or rule_row["include_media"])
    photo = first_photo(conn, r["report_id"]) if with_media and not body.get("no_photo") else None
    text = "\n".join(lines)
    limit = CAPTION_MAX if photo else TEXT_MAX
    if len(text) > limit:
        text = text[:limit - 1] + "…"
    markup = keyboard([row for row in rows if row]) if any(rows) else None
    return text, markup, photo
