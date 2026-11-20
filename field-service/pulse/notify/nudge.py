"""A nudge to members holding money they have not accounted for (LLD §5.4): a positive balance and
no expense logged, nor advance received, for 5 days; at most once per 5 days; from 10:00 IST."""
from __future__ import annotations

from datetime import datetime, timedelta

import psycopg

from pulse.metrics.ledger import member_balance_sql
from pulse.notify import alerts, digest, outbox


def send(conn: psycopg.Connection, now: datetime) -> int:
    rule = alerts.rule(conn, "balance_nudge")
    if rule is None:
        return 0
    ready, _ = digest.due(now, rule["at_local_time"])
    if not ready:
        return 0
    quiet = now - timedelta(minutes=rule["after_minutes"])
    candidates = conn.execute(
        """SELECT m.* FROM field.member m
           WHERE m.active AND NOT m.is_principal AND m.finance_scope <> 'none'
             AND NOT EXISTS (SELECT 1 FROM field.money_txn t WHERE t.status <> 'void' AND t.created_at > %(quiet)s
                             AND ((t.kind = 'expense' AND t.from_member_id = m.member_id)
                                  OR (t.kind = 'advance' AND t.to_member_id = m.member_id)))
             AND NOT EXISTS (SELECT 1 FROM field.alert_message a WHERE a.recipient_member_id = m.member_id
                             AND a.kind = 'nudge' AND a.created_at > %(quiet)s)""",
        {"quiet": quiet},
    ).fetchall()
    n = 0
    for m in candidates:
        balance = conn.execute(member_balance_sql(), {"member_id": m["member_id"]}).fetchone()["balance"]
        if balance <= 0:
            continue
        outbox.enqueue(conn, recipient=m["member_id"], kind="nudge", ac_id=m["ac_id"], rule_code=rule["code"],
                       body=outbox.body("nudge"), now=now)
        n += 1
    return n
