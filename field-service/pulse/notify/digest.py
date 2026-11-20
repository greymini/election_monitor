"""The daily digest (LLD §5.4): 20:00 Asia/Kolkata, once a day per recipient.

Report part (principal and coordinators): reports filed today by category and by area, open urgent
reports, and today's escalations. Finance part (finance scope all only): spend today by bucket, and
advances or returns awaiting acknowledgement or in dispute. A finance-all member who is neither
principal nor coordinator gets only the finance part.
"""
from __future__ import annotations

from datetime import date, datetime, time
from typing import Any

import psycopg

from pulse.bot import texts_hi as T
from pulse.bot.format import inr
from pulse.common.clock import IST
from pulse.core_adapter import CoreAdapter
from pulse.metrics import spend as sm
from pulse.notify import alerts, outbox

OPEN_URGENT_LIMIT = 10
_IST_DAY = f"((r.filed_at AT TIME ZONE {sm.IST_SQL})::date)"


def report_part(conn: psycopg.Connection, core: CoreAdapter, ac_id: str, day: date, now: datetime) -> list[str]:
    p = {"ac": ac_id, "day": day}
    total = conn.execute(f"SELECT count(*) AS n FROM field.report r WHERE r.ac_id = %(ac)s AND r.status <> 'draft' "
                         f"AND {_IST_DAY} = %(day)s", p).fetchone()["n"]
    lines = [T.DIGEST_REPORTS_TODAY.format(n=total)]
    if total == 0:
        lines.append(T.DIGEST_NONE)
    else:
        by_cat = conn.execute(
            f"""SELECT COALESCE(c.name_hi, %(pending)s) AS name, count(*) AS n FROM field.report r
                LEFT JOIN field.report_category c ON c.category_id = r.category_id
                WHERE r.ac_id = %(ac)s AND r.status <> 'draft' AND {_IST_DAY} = %(day)s
                GROUP BY 1 ORDER BY 2 DESC, 1""",
            {**p, "pending": T.CATEGORY_PENDING_SHORT},
        ).fetchall()
        lines += [T.DIGEST_BY_CATEGORY] + [T.DIGEST_LINE.format(name=r["name"], n=r["n"]) for r in by_cat]
        by_area = conn.execute(
            f"""SELECT r.block_id, r.area_id, count(*) AS n FROM field.report r
                WHERE r.ac_id = %(ac)s AND r.status <> 'draft' AND {_IST_DAY} = %(day)s
                GROUP BY 1, 2 ORDER BY 3 DESC, 1, 2""",
            p,
        ).fetchall()
        lines += [T.DIGEST_BY_AREA] + [T.DIGEST_LINE.format(name=alerts._area(core, {"ac_id": ac_id, **r}), n=r["n"])
                                       for r in by_area]
    urgent = conn.execute(
        """SELECT r.report_no, r.status, r.filed_at, c.name_hi FROM field.report r
           LEFT JOIN field.report_category c ON c.category_id = r.category_id
           WHERE r.ac_id = %(ac)s AND r.urgency = 'urgent' AND r.status IN ('new', 'acknowledged', 'assigned', 'in_progress')
           ORDER BY r.filed_at""",
        p,
    ).fetchall()
    lines += ["", T.DIGEST_OPEN_URGENT.format(n=len(urgent))]
    lines += [T.DIGEST_URGENT_LINE.format(report_no=u["report_no"], category=u["name_hi"] or T.CATEGORY_PENDING_SHORT,
                                          status=T.REPORT_STATUS[u["status"]],
                                          hours=int((now - u["filed_at"]).total_seconds() // 3600))
              for u in urgent[:OPEN_URGENT_LIMIT]]
    escalated = conn.execute(
        f"""SELECT count(*) AS n FROM field.report r WHERE r.ac_id = %(ac)s
            AND (r.escalated_at AT TIME ZONE {sm.IST_SQL})::date = %(day)s""", p).fetchone()["n"]
    lines.append(T.DIGEST_ESCALATED.format(n=escalated))
    return lines


def finance_part(conn: psycopg.Connection, ac_id: str, day: date) -> list[str]:
    p = {"ac": ac_id, "day": day}
    where = "t.ac_id = %(ac)s AND t.txn_date = %(day)s"
    total = conn.execute(f"WITH {sm.spend_cte(where)} SELECT COALESCE(SUM(amount), 0) AS v FROM spend", p).fetchone()["v"]
    by_bucket = conn.execute(
        f"""WITH {sm.spend_cte(where)}
            SELECT b.name_hi, SUM(s.amount) AS v FROM spend s JOIN field.exp_bucket b ON b.bucket_id = s.bucket_id
            GROUP BY b.name_hi ORDER BY 2 DESC, 1""", p).fetchall()
    pending = conn.execute(
        """SELECT count(*) FILTER (WHERE status = 'pending_ack') AS pn,
                  COALESCE(SUM(amount) FILTER (WHERE status = 'pending_ack'), 0) AS pv,
                  count(*) FILTER (WHERE status = 'disputed') AS dn,
                  COALESCE(SUM(amount) FILTER (WHERE status = 'disputed'), 0) AS dv
           FROM field.money_txn WHERE ac_id = %(ac)s AND kind IN ('advance', 'return')""", p).fetchone()
    lines = ["", T.DIGEST_SPEND_TODAY.format(amount=inr(total))]
    lines += [T.DIGEST_SPEND_LINE.format(name=b["name_hi"], amount=inr(b["v"])) for b in by_bucket]
    lines.append(T.DIGEST_UNCONFIRMED.format(n=pending["pn"], amount=inr(pending["pv"])))
    lines.append(T.DIGEST_DISPUTED.format(n=pending["dn"], amount=inr(pending["dv"])))
    return lines


def build(conn: psycopg.Connection, core: CoreAdapter, member: dict[str, Any], day: date, now: datetime) -> str | None:
    lines = [T.DIGEST_TITLE.format(date=day.strftime("%d-%m-%Y"))]
    if member["is_principal"] or member["is_coordinator"]:
        lines += report_part(conn, core, member["ac_id"], day, now)
    if member["finance_scope"] == "all":
        lines += finance_part(conn, member["ac_id"], day)
    return "\n".join(lines) if len(lines) > 1 else None


def due(now: datetime, at: time) -> tuple[bool, date]:
    local = now.astimezone(IST)
    return local.time() >= at, local.date()


def send_daily(conn: psycopg.Connection, core: CoreAdapter, now: datetime) -> int:
    """Queue today's digest for every recipient who has not had it. Runs often; sends once per day."""
    rule = alerts.rule(conn, "digest_daily")
    if rule is None:
        return 0
    ready, day = due(now, rule["at_local_time"])
    if not ready:
        return 0
    roles = set(rule["recipients"])
    members = conn.execute(
        """SELECT * FROM field.member m WHERE m.active AND (
               (%(p)s AND m.is_principal) OR (%(c)s AND m.is_coordinator) OR (%(f)s AND m.finance_scope = 'all'))
           AND NOT EXISTS (SELECT 1 FROM field.alert_message a WHERE a.recipient_member_id = m.member_id
                           AND a.kind = 'digest' AND a.local_date = %(day)s)
           ORDER BY m.ac_id, m.display_name""",
        {"p": "principal" in roles, "c": "coordinators" in roles, "f": "finance_all" in roles, "day": day},
    ).fetchall()
    n = 0
    for m in members:
        if build(conn, core, m, day, now):           # something to say; the text itself is drawn when sent
            outbox.enqueue(conn, recipient=m["member_id"], kind="digest", ac_id=m["ac_id"], rule_code=rule["code"],
                           body=outbox.body("digest"), local_date=day, now=now)
            n += 1
    return n

