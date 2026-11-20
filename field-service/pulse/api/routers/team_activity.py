"""GET /team/activity: per member, active days, entries, reports, last seen and logging lag.

Who may call: a core admin for the AC (every member), or a member with finance scope team (their
subtree) or all (every member). Anyone else: 403. Counts cover the last `days` days (default 14,
as member_active_days); last seen is the member's latest update to the bot (NULL if never linked).
"""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query

from pulse.api.deps import current_user, get_state
from pulse.api.routers.ledger import plain
from pulse.common.clock import IST, utcnow
from pulse.core_adapter import CoreUser, ac_by_number
from pulse.db import transaction
from pulse.ledger import scope
from pulse.metrics import activity as act
from pulse.metrics import spend as sm

router = APIRouter(prefix="/acs/{ac_number}/field")


@router.get("/team/activity")
def team_activity(ac_number: int, days: int = Query(act.ACTIVE_WINDOW_DAYS, ge=1, le=90),
                  user: CoreUser = Depends(current_user), state=Depends(get_state)):
    ac = ac_by_number(state.core, ac_number)
    if ac is None:
        raise HTTPException(404, "unknown AC")
    if ac.ac_id not in user.ac_ids:
        raise HTTPException(403, "no access to this AC")
    now = utcnow()
    with transaction(state.pool) as conn:
        me = conn.execute("SELECT member_id, finance_scope FROM field.member WHERE user_id = %s AND ac_id = %s AND active",
                          (user.user_id, ac.ac_id)).fetchone()
        if user.role == "admin" or (me and me["finance_scope"] == "all"):
            members = "(SELECT member_id FROM field.member WHERE ac_id = %(v_ac)s)"
        elif me and me["finance_scope"] == "team":
            members = scope.TEAM_MEMBERS
        else:
            raise HTTPException(403, "admin or finance scope team required")
        p = {"v_ac": ac.ac_id, "v_me": me["member_id"] if me else None, "today": now.astimezone(IST).date(),
             "since": now - timedelta(days=days)}
        rows = conn.execute(
            f"""SELECT m.member_id, m.display_name, m.parent_member_id, m.is_principal, l.last_seen_at,
                       ad.active_days, en.entries, rp.reports, lag.logging_lag_days
                FROM field.member m
                LEFT JOIN field.tg_link l ON l.member_id = m.member_id AND l.revoked_at IS NULL
                CROSS JOIN LATERAL ({act.active_days_sql('m.member_id', '%(today)s')}) ad
                CROSS JOIN LATERAL (SELECT count(*)::int AS entries FROM field.money_txn t
                                    WHERE t.created_by = m.member_id AND t.status <> 'void'
                                      AND t.created_at >= %(since)s) en
                CROSS JOIN LATERAL (SELECT count(*)::int AS reports FROM field.report r
                                    WHERE r.reporter_member_id = m.member_id AND r.status <> 'draft'
                                      AND r.filed_at >= %(since)s) rp
                CROSS JOIN LATERAL ({sm.logging_lag_sql('m.member_id')}) lag
                WHERE m.ac_id = %(v_ac)s AND m.active AND m.member_id IN {members}
                ORDER BY ad.active_days, m.display_name""", p).fetchall()
    return plain({"days": days, "active_days_window": act.ACTIVE_WINDOW_DAYS, "members": rows})
