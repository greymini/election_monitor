"""Scenario projection endpoint (module 10, LLD 11)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from analytics.scenario import ScenarioInput, jlkm_transfer_scenario, load_baseline, project
from api.deps import StrategistUser

router = APIRouter(tags=["scenario"])


class ScenarioBody(BaseModel):
    turnout_multiplier: float = Field(1.0, ge=0.5, le=1.5)
    sympathy_swing: float = Field(0.0, ge=-0.5, le=0.5,
                                  description="Share of BJP 2024 vote moving to JMM. Negative reverses it.")
    transfer: dict[str, dict[str, float]] = Field(default_factory=dict)
    jlkm_to_bjp: float | None = Field(None, ge=0.0, le=1.0,
                                      description="Shortcut for the common JLKM split question.")
    new_voter_turnout: float = Field(0.6, ge=0.0, le=1.0)
    new_voter_split: dict[str, float] | None = None
    area_id: int | None = None
    block_id: int | None = None
    draws: int = Field(500, ge=1, le=2000)
    noise: float = Field(0.05, ge=0.0, le=0.25)
    seed: int | None = 42


@router.post("/scenario")
def run_scenario(body: ScenarioBody, user: StrategistUser) -> dict:
    block_id = body.block_id
    if user.role == "block":
        block_id = user.block_id

    booths = load_baseline(area_id=body.area_id, block_id=block_id)
    if not booths:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "No baseline results are loaded yet. Load the VS-2024 Form 20 and run the "
            "crosswalk and analytics refresh first.",
        )

    transfer = dict(body.transfer)
    if body.jlkm_to_bjp is not None:
        transfer.update(jlkm_transfer_scenario(body.jlkm_to_bjp))

    result = project(booths, ScenarioInput(
        turnout_multiplier=body.turnout_multiplier,
        transfer=transfer,
        sympathy_swing=body.sympathy_swing,
        new_voter_split=body.new_voter_split,
        new_voter_turnout=body.new_voter_turnout,
        draws=body.draws,
        noise=body.noise,
        seed=body.seed,
    ))

    return {
        "booths": len(booths),
        "votes": result.votes,
        "margin": {
            "point": result.margin_point,
            "p10": result.margin_p10,
            "p50": result.margin_p50,
            "p90": result.margin_p90,
        },
        "winner": result.winner,
        "total_votes": result.total_votes,
        "high_variance_booths": result.booth_variance,
        "draws": result.draws,
        "assumptions": result.assumptions,
        "disclaimer": (
            "This is arithmetic on the assumptions above applied to the 2024 booth result. "
            "It is not a forecast. The sympathy effect after the sitting member's death cannot "
            "be measured in advance and is an input here, not an estimate."
        ),
    }
