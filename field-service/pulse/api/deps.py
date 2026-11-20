from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, Request

from pulse.core_adapter import Ac, CoreUser, ac_by_number


def get_state(request: Request):
    return request.app.state.pulse


def current_user(authorization: str | None = Header(default=None), state=Depends(get_state)) -> CoreUser:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(401, "bearer token required", headers={"WWW-Authenticate": "Bearer"})
    user = state.core.verify_token(token.strip())
    if user is None:
        raise HTTPException(401, "invalid or expired token", headers={"WWW-Authenticate": "Bearer"})
    return user


@dataclass(frozen=True)
class AdminContext:
    user: CoreUser
    ac: Ac


def require_admin(ac_number: int, user: CoreUser = Depends(current_user), state=Depends(get_state)) -> AdminContext:
    ac = ac_by_number(state.core, ac_number)
    if ac is None:
        raise HTTPException(404, "unknown AC")
    if user.role != "admin" or ac.ac_id not in user.ac_ids:
        raise HTTPException(403, "admin access to this AC required")
    return AdminContext(user=user, ac=ac)
