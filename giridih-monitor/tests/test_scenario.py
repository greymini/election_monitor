"""Scenario projection (LLD 11). The engine must be arithmetically exact on the
point estimate and honest about the spread."""

import pytest

from analytics.scenario import (
    BoothBaseline,
    ScenarioInput,
    effective_transfer,
    jlkm_transfer_scenario,
    project,
)

# A miniature AC-32: JMM ahead by 3,838 on a 195,037 vote base, as in 2024.
BOOTHS = [
    BoothBaseline("B0001", {"JMM": 47021, "BJP": 45102, "JLKM": 5393, "NOTA": 1002}, additions=2000),
    BoothBaseline("B0002", {"JMM": 47021, "BJP": 45102, "JLKM": 5394, "NOTA": 1002}, additions=2000),
]


def test_status_quo_reproduces_the_2024_margin():
    r = project(BOOTHS, ScenarioInput(new_voter_turnout=0.0, draws=1))
    assert r.margin_point == pytest.approx(3838.0)
    assert r.winner == "JMM"
    assert r.votes["JMM"] == pytest.approx(94042.0)
    assert r.votes["BJP"] == pytest.approx(90204.0)


def test_jlkm_transfer_flips_the_seat():
    """60% of JLKM's 10,787 to BJP is 6,472 for BJP and 4,315 for JMM: a net
    2,157 swing, which is not enough on its own. 100% would be."""
    sixty = project(BOOTHS, ScenarioInput(transfer=jlkm_transfer_scenario(0.6),
                                          new_voter_turnout=0.0, draws=1))
    assert sixty.margin_point == pytest.approx(3838.0 - (0.6 - 0.4) * 10787, abs=1)
    assert sixty.winner == "JMM"

    full = project(BOOTHS, ScenarioInput(transfer=jlkm_transfer_scenario(1.0),
                                         new_voter_turnout=0.0, draws=1))
    assert full.winner == "BJP"


def test_sympathy_swing_moves_bjp_votes_to_jmm():
    r = project(BOOTHS, ScenarioInput(sympathy_swing=0.05, new_voter_turnout=0.0, draws=1))
    moved = 90204 * 0.05
    assert r.margin_point == pytest.approx(3838.0 + 2 * moved, abs=1)


def test_turnout_multiplier_scales_everything_including_the_margin():
    r = project(BOOTHS, ScenarioInput(turnout_multiplier=1.1, new_voter_turnout=0.0, draws=1))
    assert r.margin_point == pytest.approx(3838.0 * 1.1, abs=1)


def test_new_voters_default_to_the_booths_own_2024_shares():
    r = project(BOOTHS, ScenarioInput(new_voter_turnout=0.5, draws=1))
    # 4,000 additions x 0.5 turnout = 2,000 new votes split on 2024 shares,
    # which widens the JMM lead slightly rather than leaving it flat.
    assert r.margin_point > 3838.0
    assert r.total_votes > 195037


def test_percentiles_bracket_the_point_estimate():
    r = project(BOOTHS, ScenarioInput(new_voter_turnout=0.0, draws=300, noise=0.05, seed=7))
    assert r.draws == 300
    assert r.margin_p10 <= r.margin_p50 <= r.margin_p90
    assert r.margin_p10 < r.margin_point < r.margin_p90


def test_results_are_reproducible_for_a_given_seed():
    a = project(BOOTHS, ScenarioInput(draws=100, seed=11))
    b = project(BOOTHS, ScenarioInput(draws=100, seed=11))
    assert (a.margin_p10, a.margin_p50, a.margin_p90) == (b.margin_p10, b.margin_p50, b.margin_p90)


def test_transfer_rows_are_normalised():
    matrix = effective_transfer(
        ScenarioInput(transfer={"JLKM": {"BJP": 6, "JMM": 4}}), ["JMM", "BJP", "JLKM"]
    )
    assert matrix["JLKM"]["BJP"] == pytest.approx(0.6)
    assert matrix["JMM"] == {"JMM": 1.0}      # untouched parties hold their vote


def test_high_variance_booths_are_reported():
    r = project(BOOTHS, ScenarioInput(draws=50))
    assert r.booth_variance
    assert all(len(item) == 2 for item in r.booth_variance)
