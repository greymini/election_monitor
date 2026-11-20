"""Who is signed in, and the places of an AC: what the web screens need before anything else.

`GET /acs/{n}/field/me` returns the core user, this AC, the user's team member row (or null for a
platform user with no member row, who sees no money) and the other ACs the user may open.
`GET /acs/{n}/field/places` lists the AC's blocks and areas with elector counts (from the
CoreAdapter), for filters and names.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from pulse.api.deps import current_user, get_state
from pulse.api.routers.ledger import plain
from pulse.core_adapter import CoreUser, ac_by_number

router = APIRouter(prefix="/acs/{ac_number}/field")


def _ac(state, ac_number: int, user: CoreUser):
    ac = ac_by_number(state.core, ac_number)
    if ac is None:
        raise HTTPException(404, "unknown AC")
    if ac.ac_id not in user.ac_ids:
        raise HTTPException(403, "no access to this AC")
    return ac


@router.get("/me")
def me(ac_number: int, user: CoreUser = Depends(current_user), state=Depends(get_state)):
    ac = _ac(state, ac_number, user)
    with state.pool.connection() as conn:
        member = conn.execute(
            """SELECT member_id, display_name, parent_member_id, is_principal, is_coordinator, finance_scope,
                      can_give_advance FROM field.member WHERE user_id = %s AND ac_id = %s AND active""",
            (user.user_id, ac.ac_id)).fetchone()
    acs = [{"ac_number": a.ac_number, "name_hi": a.name_hi, "name_en": a.name_en}
           for a in state.core.list_acs() if a.ac_id in user.ac_ids]
    return plain({"user": {"user_id": user.user_id, "display_name": user.display_name, "role": user.role},
                  "ac": {"ac_number": ac.ac_number, "name_hi": ac.name_hi, "name_en": ac.name_en},
                  "member": dict(member) if member else None, "acs": acs})


@router.get("/places")
def places(ac_number: int, user: CoreUser = Depends(current_user), state=Depends(get_state)):
    ac = _ac(state, ac_number, user)
    blocks = [{"block_id": b.block_id, "name_hi": b.name_hi, "name_en": b.name_en}
              for b in state.core.list_blocks(ac.ac_id)]
    areas = [{"area_id": a.area_id, "block_id": a.block_id, "kind": a.kind, "name_hi": a.name_hi,
              "name_en": a.name_en, "electors": state.core.electors_for_area(a.area_id)}
             for a in state.core.list_areas(ac.ac_id)]
    return {"blocks": blocks, "areas": areas,
            "map": {"tile_url": state.settings.map_tile_url, "attribution": state.settings.map_attribution}}
