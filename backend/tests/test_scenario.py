"""Scenario projection (LLD 11, master prompt 3.5).

The engine must be arithmetically exact on the point estimate and honest about
the spread.

Every test here now passes `contest` explicitly. That is the contract change of
3.5: the pair used to default to ("JMM", "BJP") inside the engine, which is
wrong in four of the six constituencies - Dumri's contest is JLKM/JMM, Silli's
is JMM/AJSU, Kanke's is INC/BJP - and produced a response that contradicted
itself, naming JMM or BJP as winner while the votes dict beside it showed the
real leader (D5). The pair now comes from `ac_contest` and decides only which
margin is measured; the winner is the argmax.
"""

import pytest

from analytics.scenario import (
    BoothBaseline,
    ScenarioInput,
    effective_transfer,
    jlkm_transfer_scenario,
    project,
)

# Giridih's contest pair, from db/seed/ac_contest.csv.
GIRIDIH = ("JMM", "BJP")

# A miniature AC-32: JMM ahead by 3,838 on a 195,037 vote base, as in 2024.
BOOTHS = [
    BoothBaseline("B0001", {"JMM": 47021, "BJP": 45102, "JLKM": 5393, "NOTA": 1002}, additions=2000),
    BoothBaseline("B0002", {"JMM": 47021, "BJP": 45102, "JLKM": 5394, "NOTA": 1002}, additions=2000),
]


def test_status_quo_reproduces_the_2024_margin():
    r = project(BOOTHS, ScenarioInput(contest=GIRIDIH, new_voter_turnout=0.0, draws=1))
    assert r.margin_point == pytest.approx(3838.0)
    assert r.winner == "JMM"
    assert r.votes["JMM"] == pytest.approx(94042.0)
    assert r.votes["BJP"] == pytest.approx(90204.0)


def test_jlkm_transfer_flips_the_seat():
    """60% of JLKM's 10,787 to BJP is 6,472 for BJP and 4,315 for JMM: a net
    2,157 swing, which is not enough on its own. 100% would be."""
    sixty = project(BOOTHS, ScenarioInput(contest=GIRIDIH,
                                          transfer=jlkm_transfer_scenario(0.6),
                                          new_voter_turnout=0.0, draws=1))
    assert sixty.margin_point == pytest.approx(3838.0 - (0.6 - 0.4) * 10787, abs=1)
    assert sixty.winner == "JMM"

    full = project(BOOTHS, ScenarioInput(contest=GIRIDIH,
                                         transfer=jlkm_transfer_scenario(1.0),
                                         new_voter_turnout=0.0, draws=1))
    assert full.winner == "BJP"


def test_sympathy_swing_moves_bjp_votes_to_jmm():
    r = project(BOOTHS, ScenarioInput(contest=GIRIDIH, sympathy_swing=0.05, new_voter_turnout=0.0, draws=1))
    moved = 90204 * 0.05
    assert r.margin_point == pytest.approx(3838.0 + 2 * moved, abs=1)


def test_turnout_multiplier_scales_everything_including_the_margin():
    r = project(BOOTHS, ScenarioInput(contest=GIRIDIH, turnout_multiplier=1.1, new_voter_turnout=0.0, draws=1))
    assert r.margin_point == pytest.approx(3838.0 * 1.1, abs=1)


def test_new_voters_default_to_the_booths_own_2024_shares():
    r = project(BOOTHS, ScenarioInput(contest=GIRIDIH, new_voter_turnout=0.5, draws=1))
    # 4,000 additions x 0.5 turnout = 2,000 new votes split on 2024 shares,
    # which widens the JMM lead slightly rather than leaving it flat.
    assert r.margin_point > 3838.0
    assert r.total_votes > 195037


def test_percentiles_bracket_the_point_estimate():
    r = project(BOOTHS, ScenarioInput(contest=GIRIDIH, new_voter_turnout=0.0, draws=300, noise=0.05, seed=7))
    assert r.draws == 300
    assert r.margin_p10 <= r.margin_p50 <= r.margin_p90
    assert r.margin_p10 < r.margin_point < r.margin_p90


def test_results_are_reproducible_for_a_given_seed():
    a = project(BOOTHS, ScenarioInput(contest=GIRIDIH, draws=100, seed=11))
    b = project(BOOTHS, ScenarioInput(contest=GIRIDIH, draws=100, seed=11))
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


# --------------------------------------------------------------------------
# D5: the winner is the argmax, not whichever of the pair is ahead
# --------------------------------------------------------------------------


def test_the_winner_is_the_argmax_not_a_member_of_the_contest_pair():
    """The audited engine returned `jmm if point_margin >= 0 else bjp`, so a
    scenario moving most of the vote to a third party still named one of the
    pair - while the votes dict in the same response showed the real leader.
    The response contradicted itself, and the frontend rendered the wrong party
    in the wrong colour."""
    # Move nearly all of JMM's and BJP's vote to JLKM.
    to_jlkm = {"JMM": {"JLKM": 0.9, "JMM": 0.1}, "BJP": {"JLKM": 0.9, "BJP": 0.1}}
    result = project(BOOTHS, ScenarioInput(contest=GIRIDIH, transfer=to_jlkm,
                                           new_voter_turnout=0.0, draws=1))

    assert result.winner == "JLKM"
    assert result.votes["JLKM"] > result.votes["JMM"]
    assert result.votes["JLKM"] > result.votes["BJP"]
    # And the response no longer contradicts itself: the winner is the top of
    # the votes dict.
    assert max(
        (p for p in result.votes if p != "NOTA"), key=lambda p: result.votes[p]
    ) == result.winner


def test_the_contest_margin_is_reported_separately_from_the_winner():
    """Two different questions. Who wins is the argmax; the margin is between
    the configured pair, which may not include the winner at all."""
    to_jlkm = {"JMM": {"JLKM": 0.9, "JMM": 0.1}, "BJP": {"JLKM": 0.9, "BJP": 0.1}}
    result = project(BOOTHS, ScenarioInput(contest=GIRIDIH, transfer=to_jlkm,
                                           new_voter_turnout=0.0, draws=1))
    assert result.winner == "JLKM"
    assert result.contest_margin is not None
    # The pair's margin is small because both lost most of their vote equally.
    assert abs(result.contest_margin) < 1000


def test_nota_can_never_win():
    """NOTA is in the baseline because it belongs in the denominator, but it
    cannot take a seat."""
    to_nota = {"JMM": {"NOTA": 1.0}, "BJP": {"NOTA": 1.0}, "JLKM": {"NOTA": 1.0}}
    result = project(BOOTHS, ScenarioInput(contest=GIRIDIH, transfer=to_nota,
                                           new_voter_turnout=0.0, draws=1))
    assert result.votes["NOTA"] > result.votes.get("JMM", 0)
    assert result.winner != "NOTA"


def test_a_different_ac_uses_its_own_pair():
    """Dumri's contest is JLKM/JMM. With the old hardcoded pair the margin was
    measured between JMM and a party that did not stand."""
    dumri = [BoothBaseline("33-B0001", {"JLKM": 94496, "JMM": 83551, "NOTA": 1000})]
    result = project(dumri, ScenarioInput(contest=("JLKM", "JMM"),
                                          new_voter_turnout=0.0, draws=1))
    assert result.winner == "JLKM"
    assert result.contest_margin == pytest.approx(94496 - 83551, abs=1)


# --------------------------------------------------------------------------
# D6: the band is a sensitivity range, and identity rows now carry variance
# --------------------------------------------------------------------------


def test_the_band_is_labelled_as_a_sensitivity_range():
    """It was displayed next to a point estimate as though it were empirical."""
    result = project(BOOTHS, ScenarioInput(contest=GIRIDIH, draws=50, noise=0.05, seed=5))
    assert "sensitivity range" in result.band_label
    assert "noise=0.05" in result.band_label
    assert "not a confidence interval" in result.band_label


def test_noise_produces_a_band_even_with_no_multi_destination_transfer_row():
    """D6. With every row an identity row, the audited engine's perturbation
    cancelled exactly against its own renormalisation, so the band collapsed to
    zero width and all the apparent uncertainty came from whichever rows
    happened to have more than one destination - in practice the single JLKM
    split the frontend always sent."""
    result = project(BOOTHS, ScenarioInput(contest=GIRIDIH, transfer={},
                                           new_voter_turnout=0.0,
                                           draws=400, noise=0.05, seed=13))
    assert result.margin_p90 > result.margin_p10, (
        "an all-identity matrix must still produce a band, from the source totals"
    )


def test_zero_noise_gives_a_zero_width_band():
    result = project(BOOTHS, ScenarioInput(contest=GIRIDIH, transfer={},
                                           new_voter_turnout=0.0,
                                           draws=100, noise=0.0, seed=13))
    assert result.margin_p10 == result.margin_p50 == result.margin_p90


def test_a_wider_noise_gives_a_wider_band():
    """Monotonicity, which is the least a sensitivity range must satisfy."""
    narrow = project(BOOTHS, ScenarioInput(contest=GIRIDIH, new_voter_turnout=0.0,
                                           draws=400, noise=0.02, seed=21))
    wide = project(BOOTHS, ScenarioInput(contest=GIRIDIH, new_voter_turnout=0.0,
                                         draws=400, noise=0.10, seed=21))
    assert (wide.margin_p90 - wide.margin_p10) > (narrow.margin_p90 - narrow.margin_p10)


# --------------------------------------------------------------------------
# Alliance-based transfers
# --------------------------------------------------------------------------


ALLIANCE_2024 = {"JMM": "INDIA", "BJP": "NDA", "AJSU": "NDA", "JLKM": "NONE", "NOTA": "NONE"}


def test_a_transfer_row_can_be_keyed_by_alliance():
    """The master prompt requires the matrix to be built on the target event's
    alliances: an AJSU voter in 2024 is being asked to back the NDA candidate."""
    booths = [BoothBaseline("32-B0001", {"JMM": 1000, "BJP": 800, "AJSU": 200, "NOTA": 10})]
    result = project(booths, ScenarioInput(
        contest=GIRIDIH,
        transfer={"NONE": {"NDA": 1.0}},
        alliance=ALLIANCE_2024,
        new_voter_turnout=0.0, draws=1,
    ))
    # NOTA is in the NONE alliance, so its vote moves to the NDA members.
    assert result.votes.get("NOTA", 0) == pytest.approx(0.0)


def test_an_alliance_destination_splits_across_its_members():
    booths = [BoothBaseline("32-B0001", {"JLKM": 1000, "BJP": 0, "AJSU": 0})]
    result = project(booths, ScenarioInput(
        contest=GIRIDIH,
        transfer={"JLKM": {"NDA": 1.0}},
        alliance=ALLIANCE_2024,
        new_voter_turnout=0.0, draws=1,
    ))
    assert result.votes["BJP"] == pytest.approx(500.0)
    assert result.votes["AJSU"] == pytest.approx(500.0)


def test_a_party_keyed_row_beats_an_alliance_keyed_one():
    """The more specific statement wins."""
    booths = [BoothBaseline("32-B0001", {"AJSU": 1000, "JMM": 0, "BJP": 0})]
    result = project(booths, ScenarioInput(
        contest=GIRIDIH,
        transfer={"NDA": {"BJP": 1.0}, "AJSU": {"JMM": 1.0}},
        alliance=ALLIANCE_2024,
        new_voter_turnout=0.0, draws=1,
    ))
    assert result.votes["JMM"] == pytest.approx(1000.0)
    assert result.votes.get("BJP", 0) == pytest.approx(0.0)


def test_alliances_are_echoed_back_with_the_assumptions():
    """The response echoes every input verbatim, so a screenshot is
    reproducible."""
    result = project(BOOTHS, ScenarioInput(contest=GIRIDIH, alliance=ALLIANCE_2024, draws=1))
    assert result.assumptions["alliance"] == ALLIANCE_2024
    assert result.assumptions["contest"] == list(GIRIDIH)


# --- sympathy swing in both directions -------------------------------------
# A negative swing is documented as reversing the effect (api/routers/
# scenario.py). It read party A's share out of party B's row - where it is 0 -
# so a negative swing moved nothing and the result equalled a swing of zero.

def _margin(swing: float) -> float:
    return project(BOOTHS, ScenarioInput(contest=GIRIDIH, sympathy_swing=swing,
                                         new_voter_turnout=0.0, draws=1)).margin_point


def test_a_negative_sympathy_swing_moves_votes_from_party_a_to_party_b():
    # JMM (party A) polled 94,042 = BJP's 90,204 + the 3,838 margin. A -5%
    # swing moves 5% of JMM's vote to BJP, closing the margin by twice that.
    moved = 94042 * 0.05
    assert _margin(-0.05) == pytest.approx(3838.0 - 2 * moved, abs=1)


def test_a_swing_of_zero_changes_nothing_and_signs_are_opposite():
    assert _margin(0.0) == pytest.approx(3838.0, abs=1)
    assert _margin(0.05) > _margin(0.0) > _margin(-0.05)
