"""Who is calling a ledger route: core token → core user → that user's active member row in this AC.

A platform user without a member row (for example the technical admin) sees no money. The finance
scope on the member row decides what the caller may see and do; the scope itself is applied inside
each query (pulse/ledger/scope.py).
"""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, HTTPException

from pulse.api.deps import current_user, get_state
from pulse.core_adapter import Ac, CoreUser, ac_by_number
from pulse.ledger.scope import Viewer

SCOPE_RANK = {"none": 0, "own": 1, "team": 2, "all": 3}


@dataclass(frozen=True)
class LedgerCaller:
    user: CoreUser
    ac: Ac
    viewer: Viewer

    def require(self, minimum: str) -> None:
        if SCOPE_RANK[self.viewer.finance_scope] < SCOPE_RANK[minimum]:
            raise HTTPException(403, "finance scope too low for this route")


def ledger_caller(ac_number: int, user: CoreUser = Depends(current_user), state=Depends(get_state)) -> LedgerCaller:
    ac = ac_by_number(state.core, ac_number)
    if ac is None:
        raise HTTPException(404, "unknown AC")
    if ac.ac_id not in user.ac_ids:
        raise HTTPException(403, "no access to this AC")
    with state.pool.connection() as conn:
        row = conn.execute(
            """SELECT member_id, ac_id, finance_scope, is_principal, can_give_advance, display_name
               FROM field.member WHERE user_id = %s AND ac_id = %s AND active""",
            (user.user_id, ac.ac_id),
        ).fetchone()
    if row is None:
        raise HTTPException(403, "not a team member in this AC")
    return LedgerCaller(user=user, ac=ac, viewer=Viewer(**row))


def at_least(minimum: str):
    def dep(caller: LedgerCaller = Depends(ledger_caller)) -> LedgerCaller:
        caller.require(minimum)
        return caller
    return dep
