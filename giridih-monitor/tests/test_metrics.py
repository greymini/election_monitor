"""Every metric in master prompt 3.2, against hand-computed fixtures.

The audit's blunt summary was that it would not trust a booth-level margin or
swing from this code, and the reason was a whole layer with no tests: every
metric the product displays was computed in SQL, and nothing exercised it. So
each row of the 3.2 table gets three kinds of test here - the value against a
figure computed by hand, every NULL rule stated in that row, and the specific
audit finding the rule exists to prevent.

The fixtures are deliberately small enough to check with a calculator, and the
Giridih 2024 case is checked against the published ECI figures, because
reproducing 1.85% is the acceptance criterion the whole Form 20 pipeline is
judged on.

These run without a database. `tests/e2e/test_metrics_sql.py` runs the same
fixtures through the SQL views, so the two implementations cannot drift; that
file is skipped without a Postgres and is recorded as NOT RUN in
UAT_READINESS.md.
"""

from __future__ import annotations

import pytest

from analytics.metrics import (
    CROSSWALK_MIN_CONFIDENCE,
    NOTA,
    PRIORITY_WEIGHTS,
    CrosswalkLink,
    PriorityInputs,
    alliance_swing_pct,
    floating_pct,
    margin_pct,
    margin_votes,
    net_roll_change_pct,
    new_voter_pct,
    percentile_ranks,
    priority_score,
    rank_candidates,
    share_pct,
    signed_margin_pct,
    swing_pct,
    transfer_delta,
    turnout_pct,
    valid_votes,
    volatility,
    votes_polled,
)

# --------------------------------------------------------------------------
# The Giridih 2024 AC result, from HLD 1.1 / the published ECI figures.
# Total valid votes 207,598; the margin of 3,838 over that is 1.849% -> 1.85%.
# --------------------------------------------------------------------------
GIRIDIH_2024 = {
    "JMM": 94042,
    "BJP": 90204,
    "JLKM": 10787,
    NOTA: 2004,
    # The published total is 207,598, so 10,561 votes went to the remaining
    # candidates. Held as one bucket here; the parser resolves them individually.
    "OTH": 10561,
}
GIRIDIH_2024_VALID = 207598
GIRIDIH_2024_ELECTORS = 304898

# A three-booth fixture small enough to verify by hand.
BOOTH_A = {"JMM": 500, "BJP": 400, "JLKM": 80, NOTA: 20}   # valid 1000
BOOTH_B = {"JMM": 300, "BJP": 600, "JLKM": 90, NOTA: 10}   # valid 1000
BOOTH_C = {"JMM": 450, "BJP": 450, "JLKM": 95, NOTA: 5}    # valid 1000, a tie


# ==========================================================================
# valid_votes
# ==========================================================================


def test_valid_votes_includes_nota():
    """Form 20's "total valid votes" line includes NOTA, and so must this - it
    is the denominator the ECI's own published margin percentage uses."""
    assert valid_votes(GIRIDIH_2024) == GIRIDIH_2024_VALID
    assert valid_votes(BOOTH_A) == 1000


def test_valid_votes_is_null_with_no_result():
    assert valid_votes(None) is None
    assert valid_votes({}) is None
    assert valid_votes({"JMM": None, "BJP": None}) is None


def test_valid_votes_counts_a_real_zero():
    """A candidate who genuinely polled nothing is not a missing candidate."""
    assert valid_votes({"JMM": 100, "BJP": 0}) == 100


# ==========================================================================
# votes_polled
# ==========================================================================


def test_votes_polled_is_valid_plus_rejected():
    assert votes_polled(1000, 12) == 1012


def test_votes_polled_excludes_tendered_by_construction():
    """Tendered votes are recorded separately and are not in the count; the
    signature has nowhere to put them, which is the point."""
    assert votes_polled(1000, 0) == 1000


def test_votes_polled_treats_missing_rejected_as_zero_but_missing_valid_as_null():
    """Form 20 omits the rejected column when it is zero, so absent means zero
    there. An absent valid total means no result, which is different."""
    assert votes_polled(1000, None) == 1000
    assert votes_polled(None, 12) is None


# ==========================================================================
# turnout_pct
# ==========================================================================


def test_turnout_pct_hand_computed():
    # 1,012 polled of 1,500 electors = 67.4666... -> 67.47
    assert turnout_pct(1012, 1500) == 67.47


def test_turnout_pct_for_giridih_2024_is_close_to_the_published_figure():
    """HLD 1.1 records 68.1% turnout on 3,04,898 electors. Our polled figure
    excludes rejected votes, which are not published per AC, so this is within a
    rounding of the published number rather than equal to it."""
    polled = votes_polled(GIRIDIH_2024_VALID, None)
    assert turnout_pct(polled, GIRIDIH_2024_ELECTORS) == 68.09


def test_turnout_pct_is_null_when_electors_are_unknown():
    """B4: result_booth_meta.electors had no writer at all, so turnout was NULL
    everywhere it appeared - while the map still offered it as a metric and
    coloured every booth grey without saying why."""
    assert turnout_pct(1012, None) is None
    assert turnout_pct(1012, 0) is None


def test_turnout_pct_is_null_with_no_result():
    assert turnout_pct(None, 1500) is None


# ==========================================================================
# share_pct
# ==========================================================================


def test_share_pct_hand_computed_with_nota_in_the_denominator():
    # 500 / 1000 = 50.00, and the 1000 includes NOTA's 20.
    assert share_pct(500, valid_votes(BOOTH_A)) == 50.0


def test_share_pct_matches_the_published_giridih_shares():
    """HLD 1.1: JMM 45.3%, BJP 43.4%, JLKM 5.2%."""
    assert share_pct(94042, GIRIDIH_2024_VALID) == 45.3
    assert share_pct(90204, GIRIDIH_2024_VALID) == 43.45
    assert share_pct(10787, GIRIDIH_2024_VALID) == 5.2


def test_share_pct_is_null_with_no_result():
    assert share_pct(None, 1000) is None
    assert share_pct(500, None) is None
    assert share_pct(500, 0) is None


# ==========================================================================
# rank_candidates - NOTA never ranks, and candidates rank individually
# ==========================================================================


def test_nota_never_wins_and_never_places():
    """NOTA has to be a candidate row so it enters the denominator, but it is
    not a contestant."""
    votes = {"JMM": 100, "BJP": 90, NOTA: 500}
    ranking = rank_candidates(votes)
    assert ranking.winner == "JMM"
    assert ranking.runner_up == "BJP"
    assert ranking.contestants == 2


def test_independents_compete_individually_not_as_a_bucket():
    """D3: the old view grouped on COALESCE(party_id, -1), so every independent
    and unresolved candidate was summed into one pseudo-party that then competed
    for winner. Eight independents on 500 each became a 4,000-vote "party" that
    outranked a real winner on 3,000."""
    votes = {"JMM": 3000, **{f"IND:{i}": 500 for i in range(8)}}
    ranking = rank_candidates(votes)
    assert ranking.winner == "JMM"
    assert ranking.winner_votes == 3000
    assert ranking.runner_up_votes == 500
    assert ranking.contestants == 9


def test_ranking_is_empty_with_no_result():
    assert rank_candidates(None).winner is None
    assert rank_candidates({}).contestants == 0
    assert rank_candidates({NOTA: 100}).contestants == 0


def test_a_tie_is_deterministic():
    """Not correct in any deep sense - a tie is resolved by lot in law - but it
    must not vary between runs, or two refreshes disagree."""
    first = rank_candidates(BOOTH_C)
    second = rank_candidates(dict(reversed(list(BOOTH_C.items()))))
    assert first.winner == second.winner
    assert first.runner_up == second.runner_up


# ==========================================================================
# margin_votes / margin_pct
# ==========================================================================


def test_margin_votes_hand_computed():
    assert margin_votes(rank_candidates(BOOTH_A)) == 100
    assert margin_votes(rank_candidates(BOOTH_B)) == 300
    assert margin_votes(rank_candidates(BOOTH_C)) == 0  # a genuine tie


def test_margin_votes_for_giridih_2024_is_the_published_3838():
    assert margin_votes(rank_candidates(GIRIDIH_2024)) == 3838


def test_margin_pct_for_giridih_2024_reproduces_the_published_1_85():
    """The acceptance criterion for the whole Form 20 pipeline.

    D1: the old view divided by a NOTA-excluding total while displaying a
    NOTA-including one in the same row, giving 1.87% against the published
    1.85% - and making the row impossible to reconcile against itself.
    """
    ranking = rank_candidates(GIRIDIH_2024)
    assert margin_pct(ranking, GIRIDIH_2024_VALID) == 1.85

    # And the wrong denominator gives the wrong answer, which is why it matters.
    without_nota = GIRIDIH_2024_VALID - GIRIDIH_2024[NOTA]
    assert margin_pct(ranking, without_nota) == 1.87


def test_margin_is_null_with_fewer_than_two_candidates():
    """Not zero. An uncontested seat has no margin, and 0 would put it at the
    top of a tightest-contests list."""
    single = rank_candidates({"JMM": 500, NOTA: 20})
    assert margin_votes(single) is None
    assert margin_pct(single, 520) is None


def test_margin_pct_is_null_with_no_valid_total():
    assert margin_pct(rank_candidates(BOOTH_A), None) is None


# ==========================================================================
# signed_margin_pct - the map ramp
# ==========================================================================


def test_signed_margin_is_positive_when_the_first_contest_party_wins():
    assert signed_margin_pct(rank_candidates(BOOTH_A), 1000, "JMM", "BJP") == 10.0


def test_signed_margin_is_negative_when_the_second_wins():
    """F1: margin_pct is unsigned, and feeding it into a diverging ramp used only
    the ramp's upper half - so a JMM-held booth and a BJP-held booth on the same
    margin rendered identically while the legend claimed the arms were
    opposites."""
    assert signed_margin_pct(rank_candidates(BOOTH_B), 1000, "JMM", "BJP") == -30.0


def test_signed_margin_is_null_when_a_third_party_wins():
    """NULL, not 0: zero sits at the ramp's neutral midpoint, visually identical
    to a knife-edge contest between the pair, which is the opposite of what
    happened."""
    jlkm_wins = {"JLKM": 600, "JMM": 300, "BJP": 100, NOTA: 0}
    assert signed_margin_pct(rank_candidates(jlkm_wins), 1000, "JMM", "BJP") is None


def test_signed_margin_uses_the_pair_it_is_given_not_a_hardcoded_one():
    """Dumri's contest is JLKM/JMM and Silli's is JMM/AJSU. A hardcoded JMM/BJP
    pair - which is what the scenario engine had - is wrong in four of six."""
    dumri = {"JLKM": 94496, "JMM": 83551, NOTA: 1000}
    valid = valid_votes(dumri)
    assert signed_margin_pct(rank_candidates(dumri), valid, "JLKM", "JMM") > 0
    assert signed_margin_pct(rank_candidates(dumri), valid, "JMM", "JLKM") < 0
    assert signed_margin_pct(rank_candidates(dumri), valid, "JMM", "BJP") is None


def test_signed_margin_is_null_with_no_result():
    assert signed_margin_pct(rank_candidates({}), None, "JMM", "BJP") is None


# ==========================================================================
# swing_pct
# ==========================================================================


def test_swing_pct_hand_computed():
    assert swing_pct(45.3, 48.2) == -2.9
    assert swing_pct(43.45, 38.7) == 4.75


def test_swing_pct_is_null_with_no_prior_election():
    """D2, the fabricated-swing bug. The old view COALESCEd a missing LAG to 0,
    so the earliest loaded election reported each party's entire vote share as
    its swing - a +38.3 point BJP "swing" at every booth in 2014."""
    assert swing_pct(38.3, None) is None


def test_a_party_that_contested_and_polled_nothing_has_a_real_swing():
    """The distinction the None/0.0 typing exists for: None means "did not
    contest or unknown", 0.0 means "contested and got nothing"."""
    assert swing_pct(5.0, 0.0) == 5.0


def test_swing_pct_is_null_for_a_weak_unreviewed_crosswalk():
    """A 0.70 match may be right, but a multi-year comparison built on it is
    provisional and must not be presented as a number."""
    weak = CrosswalkLink(confidence=0.70, reviewed=False)
    assert swing_pct(45.0, 40.0, link=weak) is None


def test_swing_pct_is_allowed_once_a_human_reviews_a_weak_crosswalk():
    reviewed = CrosswalkLink(confidence=0.70, reviewed=True)
    assert swing_pct(45.0, 40.0, link=reviewed) == 5.0


def test_swing_pct_is_allowed_for_an_auto_accepted_crosswalk():
    auto = CrosswalkLink(confidence=CROSSWALK_MIN_CONFIDENCE, reviewed=False)
    assert swing_pct(45.0, 40.0, link=auto) == 5.0


def test_swing_pct_is_null_for_a_split_booth_until_the_lineage_is_aggregated():
    """Half a booth's electorate against the whole of last time's is not a
    swing."""
    assert swing_pct(45.0, 40.0, lineage_kind="split", lineage_aggregated=False) is None
    assert swing_pct(45.0, 40.0, lineage_kind="split", lineage_aggregated=True) == 5.0


# ==========================================================================
# alliance_swing_pct
# ==========================================================================


ALLIANCE_2019 = {"JMM": "INDIA", "INC": "INDIA", "BJP": "NDA", "AJSU": "NONE", "JVM": "NONE"}
ALLIANCE_2024 = {"JMM": "INDIA", "INC": "INDIA", "BJP": "NDA", "AJSU": "NDA", "JLKM": "NONE"}


def test_alliance_swing_uses_each_events_own_alliance_map():
    """AJSU was outside the NDA in 2019 and inside it in 2024. Using one event's
    map for both would attribute its votes to a bloc it was not in."""
    prev = {"BJP": 38.7, "AJSU": 4.0}
    now = {"BJP": 43.45, "AJSU": 3.0}
    # NDA in 2019 is BJP alone (38.7); in 2024 it is BJP + AJSU (46.45).
    assert alliance_swing_pct(now, prev, ALLIANCE_2024, ALLIANCE_2019, "NDA") == 7.75


def test_alliance_swing_is_null_when_a_side_has_no_members_with_a_share():
    assert alliance_swing_pct({}, {"BJP": 38.7}, ALLIANCE_2024, ALLIANCE_2019, "NDA") is None
    assert alliance_swing_pct(
        {"JLKM": 5.0}, {"JLKM": 1.0}, ALLIANCE_2024, ALLIANCE_2019, "NDA"
    ) is None


def test_alliance_swing_respects_the_crosswalk_rule():
    weak = CrosswalkLink(confidence=0.70, reviewed=False)
    assert alliance_swing_pct(
        {"BJP": 43.0}, {"BJP": 38.0}, ALLIANCE_2024, ALLIANCE_2019, "NDA", link=weak
    ) is None


# ==========================================================================
# new_voter_pct / net_roll_change_pct
# ==========================================================================


def test_new_voter_pct_hand_computed():
    # 40,084 additions on 3,04,898 electors at window end = 13.1466% -> 13.15
    assert new_voter_pct(40084, 304898) == 13.15


def test_new_voter_pct_is_null_when_a_roll_is_missing():
    """B1: nothing wrote election_roll_link, so the view's baseline CTE was
    empty, every booth got additions = 0 and new_voter_pct = 0.00, and a
    different screen reading roll_change showed the real numbers. Two
    contradictory figures for one metric, neither flagged."""
    assert new_voter_pct(None, 304898) is None
    assert new_voter_pct(40084, None) is None
    assert new_voter_pct(40084, 0) is None


def test_net_roll_change_pct_hand_computed():
    # (1,200 - 300) / 10,000 = 9.00
    assert net_roll_change_pct(1200, 300, 10000) == 9.0


def test_net_roll_change_can_be_negative_after_a_revision():
    """Deletions exceeding additions is the SIR case, and a real finding."""
    assert net_roll_change_pct(200, 900, 10000) == -7.0


def test_net_roll_change_pct_is_null_when_a_roll_is_missing():
    assert net_roll_change_pct(None, 300, 10000) is None
    assert net_roll_change_pct(1200, None, 10000) is None
    assert net_roll_change_pct(1200, 300, None) is None


# ==========================================================================
# transfer_delta / floating_pct
# ==========================================================================


def test_transfer_delta_hand_computed():
    assert transfer_delta(45.3, 29.3) == 16.0
    assert transfer_delta(5.2, 27.5) == -22.3


def test_transfer_delta_is_null_when_either_leg_is_missing():
    assert transfer_delta(45.3, None) is None
    assert transfer_delta(None, 29.3) is None


def test_floating_pct_hand_computed():
    ls = {"AJSU": 35.7, "JMM": 29.3, "JLKM": 27.5, "OTH": 7.5}
    vs = {"AJSU": 0.0, "JMM": 45.3, "JLKM": 5.2, "BJP": 43.45, "OTH": 6.05}
    # |0-35.7| + |45.3-29.3| + |5.2-27.5| + |43.45-0| + |6.05-7.5|
    #   = 35.7 + 16.0 + 22.3 + 43.45 + 1.45 = 118.9; half = 59.45
    assert floating_pct(ls, vs) == 59.45


def test_floating_pct_is_null_when_only_one_poll_type_exists():
    """D4, and the most consequential NULL rule here. A Pedersen index over one
    poll gives exactly 100/2, so **every booth in the constituency** reported
    floating_pct = 50.00. It appeared on the map, in the transfer view, on the
    booth card and in the priority score; it looked like a finding; and because
    it was uniform, PERCENT_RANK flattened it and the priority score silently
    lost its 0.20 floating-vote term."""
    ls = {"AJSU": 35.7, "JMM": 29.3, "JLKM": 27.5}
    assert floating_pct(ls, {}) is None
    assert floating_pct({}, ls) is None
    assert floating_pct(None, None) is None


def test_floating_pct_of_identical_polls_is_zero():
    """A real zero: nothing moved between the two polls."""
    shares = {"JMM": 45.0, "BJP": 43.0, "JLKM": 12.0}
    assert floating_pct(shares, dict(shares)) == 0.0


def test_floating_pct_counts_a_party_that_contested_only_one_poll():
    """Its whole share moved, which is a real delta, not a missing one."""
    assert floating_pct({"AJSU": 40.0, "JMM": 60.0}, {"JMM": 100.0}) == 40.0


# ==========================================================================
# volatility
# ==========================================================================


def test_volatility_hand_computed():
    # Sample stdev of [-6.6, 9.5, 1.85]: mean 1.58333, deviations -8.18333 /
    # 7.91667 / 0.26667, sum of squares 129.711, /(n-1)=64.856, sqrt 8.05331.
    assert volatility([-6.6, 9.5, 1.85]) == 8.05


def test_volatility_is_null_with_fewer_than_two_years():
    assert volatility([1.85]) is None
    assert volatility([]) is None
    assert volatility([None, 1.85]) is None


def test_volatility_ignores_missing_years_rather_than_treating_them_as_zero():
    """A year we do not hold is not a year of zero margin. Treating it as one
    would report a wildly volatile booth as stable, or the reverse."""
    assert volatility([None, -6.6, 9.5, 1.85]) == 8.05
    assert volatility([-6.6, 9.5, 1.85, None]) == volatility([-6.6, 9.5, 1.85])


# ==========================================================================
# priority_score
# ==========================================================================


def test_priority_weights_sum_to_one():
    assert sum(PRIORITY_WEIGHTS.values()) == pytest.approx(1.0)


def test_priority_score_hand_computed_with_all_inputs():
    # 0.35*0.9 + 0.25*0.8 + 0.20*0.5 + 0.20*0.4 = 0.315+0.2+0.1+0.08 = 0.695
    result = priority_score(PriorityInputs(0.9, 0.8, 0.5, 0.4))
    assert result.score == 0.695
    assert result.inputs_used == ["closeness", "floating_pct", "new_voter_pct", "volatility"]
    assert result.weight_used == 1.0


def test_priority_score_renormalises_when_inputs_are_missing():
    """0.35*0.9 + 0.25*0.8 = 0.515 over a weight of 0.60 -> 0.858333 -> 0.8583."""
    result = priority_score(PriorityInputs(closeness=0.9, new_voter_pct=0.8))
    assert result.score == 0.8583
    assert result.inputs_used == ["closeness", "new_voter_pct"]
    assert result.weight_used == 0.6


def test_priority_score_records_which_inputs_contributed():
    """Two of the four inputs were constant in the audited system - new voters 0
    everywhere and floating 50.00 everywhere - so a score that looked like a
    four-factor ranking was a two-factor one with no way to tell."""
    result = priority_score(PriorityInputs(closeness=0.5))
    assert result.inputs_used == ["closeness"]
    assert result.score == 0.5


def test_priority_score_is_null_when_every_input_is_missing():
    result = priority_score(PriorityInputs())
    assert result.score is None
    assert result.inputs_used == []


# ==========================================================================
# percentile_ranks
# ==========================================================================


def test_percentile_ranks_match_sql_percent_rank():
    """PostgreSQL's PERCENT_RANK: smallest is 0, largest is 1, ties share the
    lower rank."""
    assert percentile_ranks([10, 20, 30]) == [0.0, 0.5, 1.0]
    assert percentile_ranks([10, 10, 30]) == [0.0, 0.0, 1.0]


def test_percentile_ranks_preserve_nulls():
    assert percentile_ranks([10, None, 30]) == [0.0, None, 1.0]


def test_percentile_ranks_of_a_single_value_are_null():
    """0 would rank it bottom and 1 would rank it top; neither is true."""
    assert percentile_ranks([42]) == [None]
    assert percentile_ranks([None]) == [None]


# ==========================================================================
# The three booths end to end, so the metrics compose
# ==========================================================================


def test_three_booths_end_to_end():
    booths = {"A": BOOTH_A, "B": BOOTH_B, "C": BOOTH_C}
    signed = {}
    for name, votes in booths.items():
        valid = valid_votes(votes)
        assert valid == 1000
        ranking = rank_candidates(votes)
        signed[name] = signed_margin_pct(ranking, valid, "JMM", "BJP")

    assert signed == {"A": 10.0, "B": -30.0, "C": 0.0}

    # A tie gives a real zero, and that is right: the two contest parties are
    # level. It stays distinguishable from the None a third-party win gives,
    # which is the distinction the ramp depends on.
    assert signed["C"] == 0.0
    assert signed["C"] is not None

    # Its *sign* is arbitrary, though. rank_candidates breaks a tie by name for
    # determinism, so BJP wins BOOTH_C alphabetically and the signed margin
    # comes out as -0.0 rather than +0.0. Harmless for a zero - it plots at the
    # ramp's midpoint either way - but worth pinning so nobody reads meaning
    # into the sign of a tie.
    assert abs(signed["C"]) == 0.0

    # Sample stdev of [10.0, -30.0, 0.0]: mean -6.66667, sum of squares 866.67,
    # /(n-1)=433.33, sqrt 20.81666.
    assert volatility(list(signed.values())) == 20.82
