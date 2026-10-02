"""The crawler's keep/tag decision, on headlines shaped like the real feeds.

No database: the matchers are built here the way build_matchers builds them
from the ac, block and area tables.
"""

from __future__ import annotations

from common.textnorm import fold
from news.crawl_rss import (AC_EXTRA_TERMS, PARTY_TERMS, POLITICAL_TERMS, STATE_TERMS,
                            Matchers, classify, clean_title, has_term)

GIRIDIH, DUMRI = 1, 3


def matchers() -> Matchers:
    def terms(values):
        return sorted({fold(v) for v in values})

    ac_terms = {
        GIRIDIH: terms(["Giridih", "गिरिडीह", "Pirtand", "पीरटांड़"] + AC_EXTRA_TERMS[32]),
        DUMRI: terms(["Dumri", "डुमरी", "Nawadih", "नावाडीह"]),
    }
    places = {t for v in ac_terms.values() for t in v}
    return Matchers(
        state=terms(STATE_TERMS) + sorted(places),
        political=terms(POLITICAL_TERMS),
        parties={p: terms(t) for p, t in PARTY_TERMS.items()},
        ac_terms=ac_terms,
    )


M = matchers()


def test_a_giridih_bypoll_story_is_kept_and_tagged_to_giridih():
    v = classify("गिरिडीह उपचुनाव से पहले JMM कार्यकर्ताओं की बैठक", "", M)
    assert v.keep and v.ac_ids == [GIRIDIH] and v.scope == "ac"
    assert v.parties == ["JMM"]
    assert v.relevance >= 0.8


def test_state_political_news_is_kept_without_a_constituency():
    v = classify("झारखंड: एसआईआर के विरोध में रांची में झामुमो का प्रदर्शन", "", M)
    assert v.keep and v.ac_ids == [] and v.scope == "state"
    assert "JMM" in v.parties


def test_english_headlines_work_too():
    v = classify("Jharkhand officials rush to resolve 11.8 lakh electoral roll claims", "", M)
    assert v.keep and v.scope == "state"


def test_non_political_local_news_is_dropped():
    assert not classify("गिरिडीह में सड़क हादसे में दो घायल", "", M).keep
    assert not classify("Giridih weather: heavy rain expected", "", M).keep


def test_elections_of_non_political_bodies_are_dropped():
    """The first false positives the live crawl kept."""
    assert not classify("झारखंड चैंबर चुनाव : मतदान को लेकर व्यवसायियों में उत्साह", "", M).keep
    assert not classify("हॉकी झारखंड के चुनाव को रद्द करने की मांग", "", M).keep
    # ...unless a party is in the story, which makes it political again.
    assert classify("चैंबर चुनाव में भाजपा नेताओं की सक्रियता पर सवाल, झारखंड", "", M).keep


def test_political_news_from_elsewhere_is_dropped():
    assert not classify("बिहार चुनाव: भाजपा ने उम्मीदवारों की सूची जारी की", "", M).keep


def test_a_story_naming_two_constituencies_is_tagged_to_both():
    v = classify("गिरिडीह और डुमरी में मतदाता सूची पर विवाद", "", M)
    assert v.ac_ids == [GIRIDIH, DUMRI]


def test_hindi_terms_may_take_a_suffix_but_latin_must_be_whole_words():
    assert has_term(fold("गिरिडीहवासी नाराज"), fold("गिरिडीह"))
    assert not has_term(fold("ever since the vote"), "inc")
    assert has_term(fold("INC and BJP"), "inc")
    assert not has_term(fold("pollution in the city"), "poll")


def test_landmarks_tag_the_constituency():
    v = classify("Parasnath: tribal groups demand Marang Buru status ahead of polls", "", M)
    assert v.ac_ids == [GIRIDIH]


def test_google_news_title_suffix_is_removed():
    assert clean_title("गिरिडीह में बदलेगा गणित - Amar Ujala", "Amar Ujala") == "गिरिडीह में बदलेगा गणित"
    assert clean_title("A headline - with a dash", "Jagran") == "A headline - with a dash"
