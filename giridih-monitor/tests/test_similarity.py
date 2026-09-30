"""Audit A7: the crosswalk thresholds were validated against code that never ran
in production.

`jellyfish` was declared in requirements-worker.txt but not in anything
requirements-dev.txt pulled in, so `common/similarity.jaro_winkler` fell back to
its pure-Python branch under pytest and used jellyfish in the worker. The
docstring claimed the two "give the same answers either way"; they did not. The
fallback applied the Winkler prefix bonus unconditionally, jellyfish applies it
only above a Jaro of 0.7, and on the abbreviation case the crosswalk exists to
handle the gap was 0.12 - deeper than the whole 0.65-0.85 review band. A station
could therefore be banded 'review' by the tests and 'new booth' by production.

These tests hold the two implementations together. `jellyfish` is now in
requirements-dev.txt so the parity test has something to compare against; it
skips rather than silently passing if the library is missing.
"""

from __future__ import annotations

import pytest

from common.similarity import BOOST_THRESHOLD, _jaro, jaro_winkler, token_overlap

try:
    from jellyfish import jaro_winkler_similarity as _jelly

    HAVE_JELLYFISH = True
except ImportError:  # pragma: no cover
    _jelly = None
    HAVE_JELLYFISH = False


def _fallback(s1: str, s2: str, prefix_weight: float = 0.1) -> float:
    """`jaro_winkler`'s fallback branch, reached directly so parity can be
    asserted in an environment where jellyfish *is* installed (which is now
    every environment, which would otherwise make the parity test vacuous)."""
    if s1 is None or s2 is None:
        return 0.0
    if s1 == s2:
        return 1.0
    if not s1 or not s2:
        return 0.0
    jaro = _jaro(s1, s2)
    if jaro <= BOOST_THRESHOLD:
        return jaro
    prefix = 0
    for a, b in zip(s1[:4], s2[:4], strict=False):
        if a != b:
            break
        prefix += 1
    return jaro + prefix * prefix_weight * (1 - jaro)


# Building and place names as the crosswalk actually sees them: transliterated to
# Latin by common.textnorm, with the abbreviation/expansion pairs, the spelling
# variants and the near-miss villages that the 0.85 and 0.65 bands turn on.
CROSSWALK_PAIRS = [
    # The pair that exposed the divergence. prathamik vidyalaya, abbreviated.
    ("pra vi chataro", "prathamik vidyalaya chataro"),
    ("pra vi", "prathamik vidyalaya"),
    ("ma vi pirtand", "madhya vidyalaya pirtand"),
    ("u ma vi pirtand", "utkramit madhya vidyalaya pirtand"),
    ("madhya vidyalaya pirtand", "utkramit ma vi pirtand"),
    # Spelling variants of the same station.
    ("panchayat bhawan madhuban", "panchayat bhavan madhuban"),
    ("anganbadi kendra nawadih", "aanganwadi kendra nawadih"),
    ("rajkiya madhya vidyalaya giridih", "rajkeeya madhya vidyalaya giridih"),
    ("prathamik vidyalaya chataro", "prathamik vidyalaya chatara"),
    # Different stations that must not collapse together.
    ("prathamik vidyalaya chataro", "madhya vidyalaya chataro"),
    ("panchayat bhawan madhuban", "prathamik vidyalaya madhuban"),
    ("giridih nagar nigam ward 12", "giridih nagar nigam ward 21"),
    # Village names alone, which carry 0.3 of the score.
    ("chataro", "chatara"),
    ("madhuban", "madhupur"),
    ("pirtand", "pirtanr"),
    ("nawadih", "nawadeeh"),
    ("parasnath", "parasnath hill"),
    # Adversarial: short strings, transpositions, no overlap, common prefix but
    # nothing else, which is where the gate decides the answer.
    ("a", "b"),
    ("ab", "ba"),
    ("abc", "abxyz"),
    ("abcd", "abce"),
    ("abcde", "abcxy"),
    ("prat", "pratxxxxxxxx"),
    ("xyz", "abc"),
    ("school", "schooll"),
]

# Raw Devanagari is deliberately NOT in the parity set above. jellyfish 1.x
# segments by grapheme cluster and this fallback by code point, so the two
# genuinely disagree there - 'चतरो' vs 'चतरा' is 0.77778 to jellyfish (2 of 3
# clusters match: च | त | रो) and 0.83333 here (3 of 4 code points: च त र, ो
# differs). Asserting parity on those would be asserting something false.
#
# What makes that harmless is an upstream invariant, not luck:
# `crosswalk.score_pair` scores `to_latin(canonical_building(...))`, never the
# original string. `test_crosswalk_never_scores_devanagari` below pins it, so if
# anyone removes the transliteration step the divergence stops being theoretical
# and a test says so.
DEVANAGARI_PAIRS = [
    ("प्रा०वि० चतरो", "प्राथमिक विद्यालय चतरो"),
    ("चतरो", "चतरा"),
    ("पंचायत भवन मधुबन", "पंचायत भवन मधुपुर"),
]


@pytest.mark.skipif(not HAVE_JELLYFISH, reason="jellyfish not installed")
@pytest.mark.parametrize(("left", "right"), CROSSWALK_PAIRS)
def test_fallback_agrees_with_jellyfish_to_three_places(left, right):
    """The assertion the audit asked for. 3 dp is tighter than any band edge."""
    mine = _fallback(left, right)
    theirs = float(_jelly(left, right))
    assert mine == pytest.approx(theirs, abs=1e-3), (
        f"{left!r} vs {right!r}: fallback {mine:.5f}, jellyfish {theirs:.5f}"
    )


@pytest.mark.skipif(not HAVE_JELLYFISH, reason="jellyfish not installed")
@pytest.mark.parametrize(("left", "right"), CROSSWALK_PAIRS)
def test_public_function_agrees_with_jellyfish(left, right):
    """Whichever branch `jaro_winkler` takes, the answer is the same."""
    assert jaro_winkler(left, right) == pytest.approx(float(_jelly(left, right)), abs=1e-3)


@pytest.mark.skipif(not HAVE_JELLYFISH, reason="jellyfish not installed")
@pytest.mark.parametrize(("left", "right"), CROSSWALK_PAIRS)
def test_both_implementations_agree_on_which_side_of_each_band(left, right):
    """Parity to 3 dp is the mechanism; agreeing on the band is what matters.
    A disagreement here is a silently dropped or silently merged booth."""
    from ingest.crosswalk import AUTO_ACCEPT, REVIEW_FLOOR

    def band(score: float) -> str:
        if score >= AUTO_ACCEPT:
            return "auto"
        if score >= REVIEW_FLOOR:
            return "review"
        return "new"

    assert band(_fallback(left, right)) == band(float(_jelly(left, right)))


@pytest.mark.skipif(not HAVE_JELLYFISH, reason="jellyfish not installed")
@pytest.mark.parametrize(("left", "right"), DEVANAGARI_PAIRS)
def test_crosswalk_never_scores_devanagari(left, right):
    """The invariant that makes the code-point/grapheme divergence harmless.

    `score_pair` must compare transliterated Latin, so no Devanagari character
    ever reaches `jaro_winkler`. If this fails, the parity guarantee above no
    longer covers what the crosswalk actually does.
    """
    from ingest.crosswalk import Station

    for value in (left, right):
        station = Station(ps_number=1, building=value, place=value)
        for key in (station.building_key, station.place_key):
            assert key, f"{value!r} transliterated to nothing"
            assert not any("ऀ" <= ch <= "ॿ" for ch in key), (
                f"{value!r} -> {key!r} still contains Devanagari; "
                "jaro_winkler would diverge from jellyfish here"
            )


def test_devanagari_divergence_is_real_and_recorded():
    """Documents the divergence rather than asserting a falsehood about it, so
    nobody 'fixes' the fallback to match on input it never receives."""
    if not HAVE_JELLYFISH:
        pytest.skip("jellyfish not installed")
    left, right = "चतरो", "चतरा"
    ours = _fallback(left, right)
    theirs = float(_jelly(left, right))
    # We see 4 code points (च त र ो) with a 3-character common prefix, so Jaro
    # is 0.83333 and the boost takes it to 0.88333. jellyfish sees 3 grapheme
    # clusters (च | त | रो vs च | त | रा), 2 of which match, so Jaro is 0.77778
    # and its 2-cluster prefix boost takes it to 0.82222.
    assert _jaro(left, right) == pytest.approx(0.83333, abs=1e-5)
    assert ours == pytest.approx(0.88333, abs=1e-5)
    assert theirs == pytest.approx(0.82222, abs=1e-5)
    assert abs(ours - theirs) > 0.06


def test_building_canonicalisation_is_what_neutralises_the_worst_case():
    """Why the 0.12 gap never actually corrupted a match: textnorm collapses the
    abbreviation and its expansion to the same token before scoring, so the
    similarity function is handed two identical strings.

    This is defence in depth, not a reason to leave the gate out - a building
    spelling that canonicalisation does not know still reaches jaro_winkler raw.
    """
    from common.textnorm import canonical_building, to_latin

    abbreviated = to_latin(canonical_building("प्रा०वि० चतरो"))
    expanded = to_latin(canonical_building("प्राथमिक विद्यालय चतरो"))
    assert abbreviated == expanded
    assert jaro_winkler(abbreviated, expanded) == 1.0


def test_the_gate_is_what_made_them_disagree():
    """Regression pin on the specific defect, with the measured numbers, so a
    future edit that drops the gate fails here with an explanation rather than
    somewhere in the crosswalk."""
    left, right = "pra vi chataro", "prathamik vidyalaya chataro"
    jaro = _jaro(left, right)

    # Below the gate, so the bonus must not apply and the answer is bare Jaro.
    assert jaro < BOOST_THRESHOLD
    assert _fallback(left, right) == pytest.approx(jaro, abs=1e-9)

    # What the ungated version returned, and how far off it was.
    prefix = 3  # 'p', 'r', 'a' match; ' ' vs 't' does not
    ungated = jaro + prefix * 0.1 * (1 - jaro)
    assert ungated == pytest.approx(0.71642, abs=1e-5)
    assert jaro == pytest.approx(0.59489, abs=1e-5)
    assert ungated - jaro > 0.12


def test_gate_lets_the_bonus_through_for_genuinely_similar_names():
    """The gate must not be so blunt that it kills the bonus the crosswalk needs
    for ordinary spelling variants."""
    left, right = "panchayat bhawan madhuban", "panchayat bhavan madhuban"
    assert _jaro(left, right) > BOOST_THRESHOLD
    assert _fallback(left, right) > _jaro(left, right)


def test_symmetry_and_bounds():
    for left, right in CROSSWALK_PAIRS:
        forward = jaro_winkler(left, right)
        assert 0.0 <= forward <= 1.0
        assert forward == pytest.approx(jaro_winkler(right, left), abs=1e-9)


def test_identity_and_empty_cases():
    assert jaro_winkler("chataro", "chataro") == 1.0
    assert jaro_winkler("", "chataro") == 0.0
    assert jaro_winkler("chataro", "") == 0.0
    assert jaro_winkler(None, "chataro") == 0.0
    assert jaro_winkler("chataro", None) == 0.0


def test_token_overlap_unchanged():
    """Not part of A7, pinned because the crosswalk tie-breaker depends on it."""
    assert token_overlap("prathamik vidyalaya chataro", "prathamik vidyalaya chataro") == 1.0
    assert token_overlap("prathamik vidyalaya chataro", "prathamik vidyalaya") == pytest.approx(2 / 3)
    assert token_overlap("chataro", "madhuban") == 0.0
    assert token_overlap("", "chataro") == 0.0
