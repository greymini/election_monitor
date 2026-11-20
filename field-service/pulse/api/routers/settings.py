"""Per-AC settings API: view the effective values and change overrides (admin only, audited)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from pulse import settings_ac
from pulse.api.deps import AdminContext, get_state, require_admin
from pulse.api.routers.ledger import plain
from pulse.db import transaction

router = APIRouter(prefix="/acs/{ac_number}/field")


def _view(eff: settings_ac.Effective, defaults: settings_ac.Defaults) -> dict[str, Any]:
    base = settings_ac.Effective(ac_id=eff.ac_id, ledger=defaults.ledger, reports=defaults.reports,
                                 retention=defaults.retention, sources={})
    return {key: {"value": eff.value(key), "default": base.value(key), "source": eff.sources[key],
                  "description": settings_ac.KEYS[key][4]} for key in settings_ac.KEYS}


@router.get("/settings")
def get_settings(ctx: AdminContext = Depends(require_admin), state=Depends(get_state)):
    defaults = settings_ac.defaults_from(state.settings)
    with transaction(state.pool) as conn:
        return plain(_view(settings_ac.effective(conn, ctx.ac.ac_id, defaults), defaults))


@router.patch("/settings")
def patch_settings(changes: dict[str, Any] = Body(...), ctx: AdminContext = Depends(require_admin),
                   state=Depends(get_state)):
    """{key: value} sets an override; {key: null} returns it to the environment default."""
    if not changes:
        raise HTTPException(422, "nothing to change")
    defaults = settings_ac.defaults_from(state.settings)
    try:
        with transaction(state.pool) as conn:
            eff = settings_ac.change(conn, ctx.ac.ac_id, changes, actor_user_id=ctx.user.user_id, defaults=defaults)
    except settings_ac.SettingError as e:
        raise HTTPException(422, str(e)) from None
    return plain(_view(eff, defaults))
