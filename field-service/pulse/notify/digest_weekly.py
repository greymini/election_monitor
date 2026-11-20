"""The weekly digest (LLD §5.4): Monday 09:00 Asia/Kolkata, to the principal (rule digest_weekly).

Covers the seven days before that Monday (Monday to Sunday, Asia/Kolkata):
  reports filed and closed, issues opened and closed, the median acknowledgement time of the week's
  reports, quiet members (active, not the principal, with no entry and no report all week), and
  the five areas with the largest need-versus-spend rank gap (pulse/metrics/areas.py) — that last
  part only when the recipient has finance scope all.
Sent once per week per recipient; if the worker was down on Monday it goes out at the next run that
week. The text is drawn when the message is sent (pulse/notify/render.py).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import psycopg

from pulse.bot import texts_hi as T
from pulse.bot.format import inr
from pulse.common.clock import IST
from pulse.core_adapter import CoreAdapter
from pulse.metrics import areas as am
from pulse.metrics import reports as rm
from pulse.metrics import spend as sm
from pulse.notify import alerts, outbox
from pulse.reports.lifecycle import CLOSED

KIND = "digest_weekly"
RULE = "digest_weekly"
TOP_AREAS = 5
QUIET_LISTED = 10
_DAY = f"AT TIME ZONE {sm.IST_SQL})::date"


def week_of(now: datetime) -> date:
    """The Monday (Asia/Kolkata) of the week `now` falls in."""
    local = now.astimezone(IST).date()
    return local - timedelta(days=local.isoweekday() - 1)


def due(now: datetime, rule: dict[str, Any]) -> date | None:
    """The Monday to send for, or None before Monday's send time."""
    monday = week_of(now)
    local = now.astimezone(IST)
    if local.date() == monday and local.time() < rule["at_local_time"]:
        return None
    return monday


def build(conn: psycopg.Connection, core: CoreAdapter, member: dict[str, Any], monday: date) -> str:
    ac = member["ac_id"]
    start, end = monday - timedelta(days=7), monday - timedelta(days=1)
    p = {"ac": ac, "start": start, "end": end}
    filed = conn.execute(f"""SELECT count(*) AS n FROM field.report r WHERE r.ac_id = %(ac)s AND r.status <> 'draft'
                             AND (r.filed_at {_DAY} BETWEEN %(start)s AND %(end)s""", p).fetchone()["n"]
    closed = conn.execute(
        f"""SELECT count(DISTINCT e.report_id) AS n FROM field.report_event e JOIN field.report r ON r.report_id = e.report_id
            WHERE r.ac_id = %(ac)s AND e.kind = 'status_changed' AND e.data->>'to' = ANY (%(closed)s)
              AND (e.at {_DAY} BETWEEN %(start)s AND %(end)s""", {**p, "closed": list(CLOSED)}).fetchone()["n"]
    issues = conn.execute(
        f"""SELECT count(*) FILTER (WHERE (i.created_at {_DAY} BETWEEN %(start)s AND %(end)s) AS opened,
                   count(*) FILTER (WHERE (i.closed_at {_DAY} BETWEEN %(start)s AND %(end)s) AS closed
            FROM field.issue i WHERE i.ac_id = %(ac)s""", p).fetchone()
    medians = conn.execute(rm.medians_sql(
        f"r.ac_id = %(ac)s AND r.status <> 'draft' AND (r.filed_at {_DAY} BETWEEN %(start)s AND %(end)s"), p).fetchone()
    quiet = conn.execute(
        f"""SELECT m.display_name FROM field.member m
            WHERE m.ac_id = %(ac)s AND m.active AND NOT m.is_principal
              AND NOT EXISTS (SELECT 1 FROM field.money_txn t WHERE t.created_by = m.member_id AND t.status <> 'void'
                              AND (t.created_at {_DAY} BETWEEN %(start)s AND %(end)s)
              AND NOT EXISTS (SELECT 1 FROM field.report r WHERE r.reporter_member_id = m.member_id
                              AND r.status <> 'draft' AND (r.filed_at {_DAY} BETWEEN %(start)s AND %(end)s)
            ORDER BY m.display_name""", p).fetchall()

    ack = medians["ack_minutes"]
    lines = [T.WEEKLY_TITLE.format(start=start.strftime("%d-%m"), end=end.strftime("%d-%m-%Y")),
             T.WEEKLY_REPORTS.format(filed=filed, closed=closed),
             T.WEEKLY_ISSUES.format(opened=issues["opened"], closed=issues["closed"]),
             T.WEEKLY_ACK.format(minutes=ack) if ack is not None else T.WEEKLY_ACK_NONE,
             T.WEEKLY_QUIET.format(n=len(quiet))]
    if quiet:
        names = ", ".join(q["display_name"] for q in quiet[:QUIET_LISTED])
        lines.append(names + (T.WEEKLY_AND_MORE.format(n=len(quiet) - QUIET_LISTED) if len(quiet) > QUIET_LISTED else ""))
    if member["finance_scope"] == "all":
        lines += ["", T.WEEKLY_GAP_TITLE]
        rows = conn.execute(am.need_vs_spend_sql(sm.spend_cte("t.ac_id = %(nv_ac)s")), am.area_params(core, ac)).fetchall()
        top = [r for r in rows if r["rank_gap"] is not None][:TOP_AREAS]
        names = {a.area_id: a.name_hi for a in core.list_areas(ac)}
        lines += [T.WEEKLY_GAP_LINE.format(area=names.get(r["area_id"], r["area_id"]), gap=r["rank_gap"],
                                           reports=r["open_reports"], spend=inr(r["spend"])) for r in top]
        if not top:
            lines.append(T.DIGEST_NONE)
        lines.append(T.WEEKLY_GAP_NOTE)
    return "\n".join(lines)


def send_weekly(conn: psycopg.Connection, core: CoreAdapter, now: datetime) -> int:
    rule = alerts.rule(conn, RULE)
    if rule is None or not rule["active"]:
        return 0
    monday = due(now, rule)
    if monday is None:
        return 0
    roles = set(rule["recipients"])
    members = conn.execute(
        """SELECT * FROM field.member m WHERE m.active AND (
               (%(p)s AND m.is_principal) OR (%(c)s AND m.is_coordinator))
           AND NOT EXISTS (SELECT 1 FROM field.alert_message a WHERE a.recipient_member_id = m.member_id
                           AND a.kind = %(kind)s AND a.local_date = %(monday)s)
           ORDER BY m.ac_id, m.display_name""",
        {"p": "principal" in roles, "c": "coordinators" in roles, "kind": KIND, "monday": monday}).fetchall()
    for m in members:
        outbox.enqueue(conn, recipient=m["member_id"], kind=KIND, ac_id=m["ac_id"], rule_code=rule["code"],
                       body=outbox.body(KIND), local_date=monday, now=now, run_after=now)
    return len(members)
