"""Caste blending (LLD 5). These are estimates, and the confidence score is as
much a deliverable as the percentages."""

import pytest

from analytics.caste_estimate import (
    LOW_CONFIDENCE,
    SURVEY_CONFIDENCE,
    BoothInputs,
    CommunityRef,
    blend_booth,
    census_recency,
)

# community_id: 1 Kurmi(OBC) 2 Muslim 3 Santhal(ST) 4 SC(other) 5 Dusadh(SC) 6 ST(other)
COMMUNITIES = {
    1: CommunityRef(1, "Kurmi (Mahato)", "OBC"),
    2: CommunityRef(2, "Muslim", "MUSLIM"),
    3: CommunityRef(3, "Santhal", "ST"),
    4: CommunityRef(4, "SC (other)", "SC"),
    5: CommunityRef(5, "Dusadh / Paswan", "SC"),
    6: CommunityRef(6, "ST (other)", "ST"),
}


def test_survey_overrides_inference_and_is_most_confident():
    bi = BoothInputs(
        booth_uid="B0001", electors=1000,
        surname_counts={1: 800, 2: 200}, matched_tokens=1000, confident_tokens=900,
        survey={1: 500, 2: 500},
    )
    pct, conf = blend_booth(bi, COMMUNITIES)
    assert pct[1] == pytest.approx(50.0)
    assert pct[2] == pytest.approx(50.0)
    assert conf == SURVEY_CONFIDENCE


def test_percentages_always_sum_to_100():
    bi = BoothInputs(
        booth_uid="B0002", electors=1000,
        surname_counts={1: 600, 2: 250, 3: 150}, matched_tokens=1000, confident_tokens=800,
        census_sc_pct=12.0, census_st_pct=20.0, census_year=2011,
    )
    pct, _ = blend_booth(bi, COMMUNITIES)
    assert sum(pct.values()) == pytest.approx(100.0)


def test_census_pulls_st_share_toward_the_published_figure():
    """Surnames find 15% Santhal; the census says the village is 35% ST. The
    blend should land between the two, weighted 0.7 / 0.3."""
    bi = BoothInputs(
        booth_uid="B0003", electors=1000,
        surname_counts={1: 850, 3: 150}, matched_tokens=1000, confident_tokens=900,
        census_st_pct=35.0, census_year=2011,
    )
    pct, _ = blend_booth(bi, COMMUNITIES)
    expected_st = 0.7 * 15.0 + 0.3 * 35.0        # 21.0, before renormalisation
    assert pct[3] > 15.0
    assert pct[3] == pytest.approx(100 * expected_st / (85.0 + expected_st), abs=0.01)


def test_census_sc_lands_in_the_other_bucket_when_surnames_found_none():
    bi = BoothInputs(
        booth_uid="B0004", electors=1000,
        surname_counts={1: 1000}, matched_tokens=1000, confident_tokens=1000,
        census_sc_pct=20.0, census_year=2011,
    )
    pct, _ = blend_booth(bi, COMMUNITIES)
    assert pct.get(4, 0) > 0, "census SC share must not be silently dropped"


def test_low_surname_coverage_produces_low_confidence():
    """Only 15% of electors had a surname the dictionary recognises. The
    estimate must be flagged too weak to show."""
    bi = BoothInputs(
        booth_uid="B0005", electors=1000,
        surname_counts={1: 150}, matched_tokens=150, confident_tokens=150,
        census_sc_pct=10.0, census_st_pct=5.0, census_year=2011,
    )
    _, conf = blend_booth(bi, COMMUNITIES)
    assert conf < LOW_CONFIDENCE


def test_good_coverage_and_unambiguous_surnames_raise_confidence():
    bi = BoothInputs(
        booth_uid="B0006", electors=1000,
        surname_counts={1: 900}, matched_tokens=900, confident_tokens=880,
        census_sc_pct=10.0, census_st_pct=5.0, census_year=2011,
    )
    _, conf = blend_booth(bi, COMMUNITIES)
    assert conf > 0.7


def test_no_surnames_means_no_estimate():
    bi = BoothInputs(booth_uid="B0007", electors=1000)
    pct, conf = blend_booth(bi, COMMUNITIES)
    assert pct == {} and conf == 0.0


def test_census_recency_decays():
    assert census_recency(2011, 2026) == pytest.approx(0.25)
    assert census_recency(2026, 2026) == pytest.approx(1.0)
    assert census_recency(None, 2026) == 0.0
    assert census_recency(1991, 2026) == 0.0
