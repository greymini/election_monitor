"""Scenario projection endpoint (module 10, LLD 11)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from analytics.scenario import ScenarioInput, jlkm_transfer_scenario, load_baseline, project
from api.deps import CurrentAC, StrategistUser
from common.db import query, query_one

router = APIRouter(prefix="/acs/{ac_number}", tags=["scenario"])


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
def run_scenario(body: ScenarioBody, user: StrategistUser, ac: CurrentAC) -> dict:
    block_id = body.block_id
    if user.role == "block":
        block_id = user.block_id

    booths = load_baseline(ac_id=ac.ac_id, area_id=body.area_id, block_id=block_id)
    if not booths:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"No baseline results are loaded for AC-{ac.ac_number} ({ac.name_en}). "
            "Load its Form 20, run the crosswalk, then `python -m analytics.refresh`.",
        )

    transfer = dict(body.transfer)
    if body.jlkm_to_bjp is not None:
        transfer.update(jlkm_transfer_scenario(body.jlkm_to_bjp))

    # The contest pair comes from ac_contest, per AC per event. It used to be
    # hardcoded ("JMM", "BJP") inside the engine, which is wrong in four of the
    # six constituencies and made the response contradict itself (D5).
    contest_row = query_one(
        "SELECT pa.abbr AS party_a, pb.abbr AS party_b, e.label AS baseline_label "
        "FROM ac_contest c "
        "JOIN election e ON e.event_id = c.event_id AND e.ac_id = c.ac_id "
        "JOIN party pa ON pa.party_id = c.party_a "
        "JOIN party pb ON pb.party_id = c.party_b "
        "WHERE c.ac_id = %s AND e.is_baseline",
        (ac.ac_id,),
    )
    if contest_row is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"No contest pair is configured for AC-{ac.ac_number} at its baseline "
            "election. Add a row to db/seed/ac_contest.csv and re-seed: without it "
            "there is no pair for the margin to be measured between.",
        )

    # Alliances as they stand at the baseline event, so a transfer row keyed by
    # alliance resolves to the parties that were actually in it.
    alliance = {
        row["abbr"]: row["alliance"]
        for row in query(
            "SELECT p.abbr, al.alliance FROM party_alliance al "
            "JOIN party p ON p.party_id = al.party_id "
            "JOIN election e ON e.event_id = al.event_id "
            "WHERE e.ac_id = %s AND e.is_baseline",
            (ac.ac_id,),
        )
    }

    result = project(booths, ScenarioInput(
        turnout_multiplier=body.turnout_multiplier,
        transfer=transfer,
        sympathy_swing=body.sympathy_swing,
        new_voter_split=body.new_voter_split,
        new_voter_turnout=body.new_voter_turnout,
        draws=body.draws,
        noise=body.noise,
        seed=body.seed,
        contest=(contest_row["party_a"], contest_row["party_b"]),
        alliance=alliance,
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
        # The projected leader by argmax, which may be neither of the contest
        # pair. `contest` is what the margin is measured between.
        "winner": result.winner,
        "winner_votes": result.winner_votes,
        "runner_up": result.runner_up,
        "contest": list(result.assumptions["contest"]),
        "contest_margin": result.contest_margin,
        "total_votes": result.total_votes,
        "high_variance_booths": result.booth_variance,
        "draws": result.draws,
        "assumptions": result.assumptions,
        "band_label": result.band_label,
        "baseline_source": contest_row["baseline_label"],
        # Whether this AC's seeded facts have been reconciled against ECI/CEO
        # publications. A projection off unverified figures is still arithmetic,
        # but the reader has to know which kind of input it had.
        "ac_verified": ac.verified,
        # The baseline is the booth table, so the projection starts from EVM
        # votes; postal ballots are reported for the whole AC only and are not
        # apportioned to booths. OTH is every other candidate's votes summed,
        # including those whose party the source does not record.
        "baseline_notes": [
            "Baseline is EVM votes at polling stations; postal ballots (Form 20 "
            "'Total Postal Ballot Votes') are not included.",
            "OTH sums every candidate outside the named parties, including candidates "
            "whose party is not recorded in the source.",
        ],
        "disclaimer": (
            "This is arithmetic on the assumptions above applied to the baseline booth "
            "result. It is not a forecast. The sympathy effect after a sitting member's "
            "death cannot be measured in advance and is an input here, not an estimate. "
            "The range is a sensitivity band at the stated noise level, not a confidence "
            "interval."
        ),
    }
