"""Form 20 column resolution (master prompt 3.1), against real header shapes.

Audit C1. The audited resolver split a header on a trailing parenthesis and
looked the result up against `abbr` and `name_en` only - never `name_hi`, which
is seeded for every party. So a Devanagari header could not resolve by
construction, and a bare-name header (which is what a real Form 20 prints) had
nothing to look up at all. Every candidate loaded with party_id NULL, and
mv_result_booth_wide reported a 100% margin with a NULL winner for every booth.

The headers here are the ones the audit obtained by running the parser against
representative documents, plus the abbreviation and OCR variants the alias table
exists for.
"""

from __future__ import annotations

import pytest

from ingest.resolve import (
    FIRST_PART_THRESHOLD,
    NAME_MATCH_MARGIN,
    NAME_MATCH_THRESHOLD,
    Candidate,
    bracket_text,
    is_nota,
    is_tail_column,
    normalise_header,
    resolve_column,
    resolve_columns,
    review_payload,
    strip_brackets,
    unresolved,
)

# db/seed/party_alias.csv, as the loader keys it.
ALIASES = {
    "झामुमो": "JMM", "jmm": "JMM", "jharkhand mukti morcha": "JMM",
    "भाजपा": "BJP", "bjp": "BJP", "bharatiya janata party": "BJP",
    "आजसू": "AJSU", "ajsu": "AJSU",
    "झालोक्रांमो": "JLKM", "jlkm": "JLKM",
    "बसपा": "BSP", "bsp": "BSP",
    "निर्दलीय": "IND", "ind": "IND", "independent": "IND",
    "नोटा": "NOTA", "nota": "NOTA",
}

# The seeded candidates for Giridih VS-2024, from db/seed/ac_totals.csv.
SEEDED = [
    Candidate(1, "Sudivya Kumar", "JMM", 94042),
    Candidate(2, "Nirbhay Kumar Shahabadi", "BJP", 90204),
    Candidate(3, "Navin Anand", "JLKM", 10787),
]


# --------------------------------------------------------------------------
# Step 1: normalisation
# --------------------------------------------------------------------------


def test_normalisation_collapses_whitespace_and_newlines():
    assert normalise_header("  Sudivya\n  Kumar ") == "Sudivya Kumar"


def test_normalisation_keeps_parentheses_and_drops_other_punctuation():
    """The brackets carry the party, so they survive; a full stop inside an
    abbreviated name is a separator, so "Nirbhay Kr. Shahabadi" and
    "Nirbhay Kr Shahabadi" must normalise the same."""
    assert normalise_header("Sudivya Kumar (JMM)") == "Sudivya Kumar (JMM)"
    assert normalise_header("Nirbhay Kr. Shahabadi") == "Nirbhay Kr Shahabadi"
    assert normalise_header("J.M.M.") == "J M M"


def test_normalisation_handles_devanagari():
    assert normalise_header("सुदिव्य  कुमार") == "सुदिव्य कुमार"


def test_bracket_and_strip():
    assert bracket_text("सुदिव्य कुमार (झामुमो)") == "झामुमो"
    assert bracket_text("Sudivya Kumar") is None
    assert strip_brackets("सुदिव्य कुमार (झामुमो)") == "सुदिव्य कुमार"
    assert strip_brackets("Sudivya Kumar") == "Sudivya Kumar"


# --------------------------------------------------------------------------
# Tail columns
# --------------------------------------------------------------------------


@pytest.mark.parametrize("cell", [
    "Total", "कुल", "Total of Valid Votes", "कुल वैध मत",
    "No. of Rejected Votes", "अस्वीकृत मत", "No. of Tendered Votes", "निविदत्त मत",
    "Serial No. of Polling Station", "क्रम सं", "",
])
def test_tail_columns_are_recognised_in_both_scripts(cell):
    assert is_tail_column(cell)


@pytest.mark.parametrize("cell", [
    "Sudivya Kumar", "सुदिव्य कुमार", "Navin Anand", "नोटा",
])
def test_candidate_columns_are_not_tails(cell):
    assert not is_tail_column(cell)


# --------------------------------------------------------------------------
# Step 4: NOTA
# --------------------------------------------------------------------------


@pytest.mark.parametrize("cell", ["NOTA", "नोटा", "None of the Above",
                                  "इनमें से कोई नहीं", "उपरोक्त में से कोई नहीं"])
def test_nota_is_recognised_in_both_scripts(cell):
    assert is_nota(cell)


def test_nota_resolves_to_the_nota_party_as_a_candidate_row():
    """D1: NOTA must load as a candidate or it cannot enter the denominator, and
    the seeded NOTA party and its pivot bucket stay dead code."""
    resolution = resolve_column(3, "नोटा", ALIASES, SEEDED)
    assert resolution.resolved
    assert resolution.party_abbr == "NOTA"
    assert resolution.method == "nota"
    assert resolution.name == "NOTA"


def test_nota_is_not_fuzzy_matched_against_a_real_candidate():
    """Treating it as a name to match would occasionally hit a real candidate."""
    resolution = resolve_column(3, "NOTA", ALIASES, SEEDED)
    assert resolution.candidate_id is None


# --------------------------------------------------------------------------
# Step 2: the bracket against party_alias - the Devanagari case C1 could not do
# --------------------------------------------------------------------------


def test_a_devanagari_party_abbreviation_resolves():
    """The exact case the audited code could not handle: it built its lookup
    from abbr and name_en, so 'झामुमो' had nothing to match."""
    resolution = resolve_column(0, "सुदिव्य कुमार (झामुमो)", ALIASES, SEEDED)
    assert resolution.party_abbr == "JMM"
    assert resolution.resolved


def test_a_latin_party_abbreviation_resolves():
    resolution = resolve_column(0, "Sudivya Kumar (JMM)", ALIASES, SEEDED)
    assert resolution.party_abbr == "JMM"


def test_a_full_party_name_resolves():
    resolution = resolve_column(0, "Sudivya Kumar (Jharkhand Mukti Morcha)", ALIASES, SEEDED)
    assert resolution.party_abbr == "JMM"


# --------------------------------------------------------------------------
# Step 3: the bare name, which is what a real Form 20 actually prints
# --------------------------------------------------------------------------


def test_a_bare_latin_name_resolves_by_fuzzy_match():
    """The usual header. The audited code produced party_id NULL here, for every
    column, on every document."""
    resolution = resolve_column(0, "Sudivya Kumar", ALIASES, SEEDED)
    assert resolution.resolved
    assert resolution.method == "name"
    assert resolution.party_abbr == "JMM"
    assert resolution.candidate_id == 1
    assert resolution.score >= NAME_MATCH_THRESHOLD


def test_a_bare_devanagari_name_resolves_by_transliteration():
    resolution = resolve_column(0, "सुदिव्य कुमार", ALIASES, SEEDED)
    assert resolution.resolved
    assert resolution.party_abbr == "JMM"
    assert resolution.candidate_id == 1


def test_a_spelling_variant_resolves():
    """Form 20 and the ECI result page do not always agree on a name."""
    resolution = resolve_column(1, "Nirbhay Kr Shahabadi", ALIASES, SEEDED)
    assert resolution.resolved
    assert resolution.candidate_id == 2
    assert resolution.party_abbr == "BJP"


def test_the_bracket_and_the_name_together_are_recorded_as_such():
    resolution = resolve_column(0, "Sudivya Kumar (JMM)", ALIASES, SEEDED)
    assert resolution.method == "alias+name"
    assert resolution.candidate_id == 1


# --------------------------------------------------------------------------
# Step 5: independents compete individually
# --------------------------------------------------------------------------


def test_an_independent_resolves_to_ind():
    """D3: independents must keep a distinct identity so they rank individually
    rather than being summed into one bucket that can outrank a real winner."""
    resolution = resolve_column(4, "Ram Prasad (निर्दलीय)", ALIASES, SEEDED)
    assert resolution.resolved
    assert resolution.party_abbr == "IND"
    assert resolution.name == "Ram Prasad"


def test_two_independents_keep_different_names():
    resolutions = resolve_columns(
        ["Ram Prasad (Independent)", "Shyam Lal (Independent)"], ALIASES, SEEDED
    )
    assert all(r.resolved for r in resolutions)
    assert resolutions[0].name != resolutions[1].name


# --------------------------------------------------------------------------
# Step 6: an unresolved column aborts the load
# --------------------------------------------------------------------------


def test_an_unknown_name_does_not_resolve():
    """The previous behaviour was to log a warning and load the column
    unattributed, which is how a 100% margin reached the dashboard."""
    resolution = resolve_column(0, "Completely Different Person", ALIASES, SEEDED)
    assert not resolution.resolved
    assert resolution.party_abbr is None


def test_a_name_far_from_every_candidate_does_not_resolve():
    """0.88 is deliberately higher than the crosswalk's 0.85: a mismatched
    candidate attributes votes to the wrong person, and the AC total check
    cannot catch two candidates being swapped."""
    for name in ("Completely Different Person", "Mohan Verma", "Xyz Abcd"):
        resolution = resolve_column(0, name, ALIASES, SEEDED)
        assert not resolution.resolved, f"{name!r} matched at {resolution.score}"


def test_sudhir_kumat_does_not_resolve_to_sudivya_kumar():
    """The case that motivated the stricter rule, and the reason it needed a
    third condition rather than two.

    `comparable()` puts the two scripts' transliterations on an equal footing,
    which necessarily raises every score. "Sudhir Kumat" against the seeded
    "Sudivya Kumar" measures:

        whole-string     0.8833   >= 0.88, so the threshold rule passes
        margin over 2nd  0.3368   >= 0.05, so the margin rule passes
        first name-part  0.8444   <  0.88, so this is what rejects it

    The margin rule guards against *ambiguity* - two candidates with similar
    names - and cannot catch a single spurious near-match, which is a different
    failure. The whole-string score here is carried by 'kumat'/'kumar' at 0.92,
    which drags 'sudhir'/'sudivy' over the line.
    """
    resolution = resolve_column(0, "Sudhir Kumat", ALIASES, SEEDED)

    assert not resolution.resolved
    assert resolution.candidate_id is None
    assert resolution.party_abbr is None
    # And it says which condition failed, not just that something did.
    assert any("first name-part" in reason for reason in resolution.reject_reasons)
    # The near-miss is still on the record so an operator can act on it.
    assert resolution.candidates_considered[0][0] == "Sudivya Kumar"
    assert resolution.candidates_considered[0][1] == pytest.approx(0.8833, abs=1e-3)


def test_an_unresolved_column_names_every_condition_it_failed():
    resolution = resolve_column(0, "Mohan Verma", ALIASES, SEEDED)
    assert not resolution.resolved
    assert any("<" in reason for reason in resolution.reject_reasons)


def test_a_near_tie_is_refused_by_the_margin_rule():
    """Two candidates whose names differ by one letter. Picking the higher of
    two near-equal scores is a coin flip dressed up as a decision, and this is
    exactly when a wrong attribution is least visible."""
    twins = [
        Candidate(1, "Ram Kumar Singh", "JMM"),
        Candidate(2, "Ram Kumar Sinha", "BJP"),
    ]
    resolution = resolve_column(0, "Ram Kumar Sinh", ALIASES, twins)

    assert not resolution.resolved
    assert any("ahead of" in reason for reason in resolution.reject_reasons)


def test_a_clear_winner_passes_the_margin_rule():
    resolution = resolve_column(0, "Sudivya Kumar", ALIASES, SEEDED)
    assert resolution.resolved
    assert resolution.candidate_id == 1


def test_a_sole_candidate_needs_no_margin():
    """With one candidate there is no second-best to beat, and requiring a
    margin against nothing would reject every single-candidate contest."""
    only = [Candidate(1, "Sudivya Kumar", "JMM")]
    resolution = resolve_column(0, "Sudivya Kumar", ALIASES, only)
    assert resolution.resolved
    assert resolution.candidate_id == 1


def test_the_abbreviated_middle_name_still_resolves():
    """Why the floor is on the *first* name-part and not on every part.

    'Nirbhay Kr Shahabadi' for 'Nirbhay Kumar Shahabadi' scores 0.5667 on
    'kr'/'kumar'. A floor on every part would reject it, and abbreviating a
    middle name is exactly what a Form 20 does.
    """
    resolution = resolve_column(1, "Nirbhay Kr Shahabadi", ALIASES, SEEDED)
    assert resolution.resolved
    assert resolution.candidate_id == 2


# --------------------------------------------------------------------------
# Bracket-first: a printed party outranks an inferred name
# --------------------------------------------------------------------------


def test_a_bracketed_party_resolves_the_candidate_even_if_the_name_is_unrecognised():
    """A party printed in the header is a statement by the returning officer;
    a name match is our inference. Where both are available the printed one
    decides and the name only narrows - so a header whose spelling we do not
    recognise is still rescued when its party stood exactly one candidate."""
    resolution = resolve_column(0, "S. Kumar Sonu (झामुमो)", ALIASES, SEEDED)

    assert resolution.resolved
    assert resolution.party_abbr == "JMM"
    assert resolution.candidate_id == 1          # the only JMM candidate
    assert resolution.method == "alias+name"


def test_the_name_disambiguates_within_the_bracketed_party():
    """Two candidates for one party - a real case where a party fields a
    substitute or the seed holds both a main and a dummy candidate."""
    two_jmm = [
        Candidate(1, "Sudivya Kumar", "JMM"),
        Candidate(4, "Ramesh Yadav", "JMM"),
        Candidate(2, "Nirbhay Kumar Shahabadi", "BJP"),
    ]
    resolution = resolve_column(0, "Ramesh Yadav (JMM)", ALIASES, two_jmm)

    assert resolution.resolved
    assert resolution.party_abbr == "JMM"
    assert resolution.candidate_id == 4


def test_a_bracketed_party_narrows_the_pool_so_an_outside_name_cannot_win():
    """Without the narrowing, 'Nirbhay Kumar Shahabadi (झामुमो)' would match the
    BJP candidate on name and contradict the printed party."""
    resolution = resolve_column(0, "Nirbhay Kumar Shahabadi (झामुमो)", ALIASES, SEEDED)

    assert resolution.party_abbr == "JMM"
    assert resolution.candidate_id == 1, (
        "the pool must be restricted to the bracketed party, so the BJP "
        "candidate cannot win on name similarity"
    )


def test_a_bracketed_party_with_no_seeded_candidate_still_resolves_the_party():
    """The party is known even though no published candidate matches it. The
    column loads attributed to the party with no candidate_id, which is a
    weaker but honest result - and the AC-total check will notice if it is
    wrong."""
    resolution = resolve_column(0, "Someone Else (बसपा)", ALIASES, SEEDED)
    assert resolution.resolved
    assert resolution.party_abbr == "BSP"
    assert resolution.candidate_id is None


def test_a_tie_at_the_top_refuses_to_guess():
    """Two candidates with similar names is exactly when a wrong attribution is
    most plausible and least visible."""
    twins = [Candidate(1, "Ram Kumar", "JMM"), Candidate(2, "Ram Kumar", "BJP")]
    resolution = resolve_column(0, "Ram Kumar", ALIASES, twins)
    assert resolution.candidate_id is None


def test_unresolved_columns_are_collected():
    resolutions = resolve_columns(
        ["Sudivya Kumar", "Someone Unknown", "नोटा"], ALIASES, SEEDED
    )
    failures = unresolved(resolutions)
    assert len(failures) == 1
    assert failures[0].raw_header == "Someone Unknown"


def test_the_review_payload_names_the_top_three_guesses_with_scores():
    """An operator told only that something failed cannot fix it. The payload
    has to show what it nearly matched and what to edit."""
    resolution = resolve_column(0, "Someone Unknown", ALIASES, SEEDED)
    payload = review_payload(resolution)

    assert payload["raw_header"] == "Someone Unknown"
    assert len(payload["top_candidates"]) == 3
    assert all("score" in c and "name" in c for c in payload["top_candidates"])
    assert payload["threshold"] == NAME_MATCH_THRESHOLD
    # And it must say what to do about it.
    assert "party_alias.csv" in payload["fix"]
    assert "ac_totals.csv" in payload["fix"]
    # Scores descending, so the likeliest fix is first.
    scores = [c["score"] for c in payload["top_candidates"]]
    assert scores == sorted(scores, reverse=True)


# --------------------------------------------------------------------------
# The real header rows the audit ran the parser against
# --------------------------------------------------------------------------


def test_the_english_header_row_resolves_completely():
    header = [
        "Serial No. of Polling Station",
        "Sudivya Kumar",
        "Nirbhay Kumar Shahabadi",
        "Navin Anand",
        "Total of Valid Votes",
        "No. of Rejected Votes",
        "NOTA",
        "Total",
        "No. of Tendered Votes",
    ]
    candidate_cells = [c for c in header if not is_tail_column(c)]
    assert candidate_cells == ["Sudivya Kumar", "Nirbhay Kumar Shahabadi",
                               "Navin Anand", "NOTA"]

    resolutions = resolve_columns(candidate_cells, ALIASES, SEEDED)
    assert not unresolved(resolutions)
    assert [r.party_abbr for r in resolutions] == ["JMM", "BJP", "JLKM", "NOTA"]


def test_the_devanagari_header_row_resolves_completely():
    """The row the audited code failed on completely."""
    header = [
        "क्रम सं",
        "सुदिव्य कुमार",
        "निर्भय कुमार शाहाबादी",
        "नवीन आनंद",
        "कुल वैध मत",
        "अस्वीकृत मत",
        "नोटा",
        "कुल",
        "निविदत्त मत",
    ]
    candidate_cells = [c for c in header if not is_tail_column(c)]
    assert len(candidate_cells) == 4

    resolutions = resolve_columns(candidate_cells, ALIASES, SEEDED)
    assert not unresolved(resolutions), [r.raw_header for r in unresolved(resolutions)]
    assert [r.party_abbr for r in resolutions] == ["JMM", "BJP", "JLKM", "NOTA"]


def test_every_seeded_candidate_is_matched_exactly_once():
    """Two columns resolving to the same candidate would double that
    candidate's votes and leave another with none - and the AC total check
    would still pass if the two were adjacent in size."""
    candidate_cells = ["सुदिव्य कुमार", "निर्भय कुमार शाहाबादी", "नवीन आनंद"]
    resolutions = resolve_columns(candidate_cells, ALIASES, SEEDED)
    ids = [r.candidate_id for r in resolutions]
    assert len(set(ids)) == len(ids)
    assert set(ids) == {1, 2, 3}


# --------------------------------------------------------------------------
# Transliteration-neutral comparison
# --------------------------------------------------------------------------


def test_comparable_puts_the_two_scripts_on_an_equal_footing():
    """Devanagari transliteration reinstates inherent vowels and lengthens
    others, so the same name in each script produces different Latin:
    नवीन आनंद becomes 'naveena aananda' while the ECI's roman spelling is
    'navin anand'. Scored as-is that pair is 0.854, below the 0.88 threshold -
    so the two forms are canonicalised before scoring rather than the threshold
    being weakened, which would let genuinely different names through.
    """
    from ingest.resolve import comparable

    assert comparable("नवीन आनंद") == comparable("Navin Anand")
    assert comparable("सुदिव्य कुमार") == comparable("Sudivya Kumar")
    assert comparable("निर्भय कुमार शाहाबादी") == comparable("Nirbhay Kumar Shahabadi")


def test_comparable_does_not_collapse_genuinely_different_names():
    from ingest.resolve import comparable

    assert comparable("Navin Anand") != comparable("Nirbhay Shahabadi")
    assert comparable("Sudivya Kumar") != comparable("Navin Anand")


def test_comparable_leaves_short_names_alone():
    """Only words long enough that a trailing vowel is plausibly the inherent
    one are shortened; 'Lata' must not become 'Lat'."""
    from ingest.resolve import comparable

    assert comparable("Ram") == "ram"
    assert comparable("Lata") == "lat"  # 4 chars, so it is shortened
    assert comparable("Om") == "om"


def test_the_three_thresholds_are_documented_values():
    """If these move, docs/METRICS.md and the review-queue payload move too."""
    assert NAME_MATCH_THRESHOLD == 0.88
    assert NAME_MATCH_MARGIN == 0.05
    assert FIRST_PART_THRESHOLD == 0.88


def test_the_review_payload_carries_the_thresholds_and_the_reasons():
    resolution = resolve_column(0, "Sudhir Kumat", ALIASES, SEEDED)
    payload = review_payload(resolution)
    assert payload["threshold"] == NAME_MATCH_THRESHOLD
    assert payload["margin_required"] == NAME_MATCH_MARGIN
    assert payload["first_part_threshold"] == FIRST_PART_THRESHOLD
    assert payload["reject_reasons"]
