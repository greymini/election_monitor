"""Keyword-rule labels on headlines shaped like the real feeds.

No database: the rules are built here the way build_rules builds them from the
party_alias, candidate and area tables.
"""

from __future__ import annotations

from common.textnorm import fold
from news.crawl_rss import POLITICAL_TERMS
from news.label_batch import ISSUE_ENUM
from news.label_rules import (
    CANDIDATE_HI,
    ELECTION_TERMS,
    ISSUE_TERMS,
    PARTY_TERMS,
    Rules,
    _usable_area_name,
    label,
)

GIRIDIH = 1
PIRTAND_GP = 501


def terms(values):
    return sorted({fold(v) for v in values})


R = Rules(
    parties={p: terms(t) for p, t in PARTY_TERMS.items()},
    issues={k: terms(v) for k, v in ISSUE_TERMS.items()},
    election=terms(ELECTION_TERMS),
    political=terms(t for t in POLITICAL_TERMS if t not in ELECTION_TERMS),
    persons={"Sudivya Kumar": terms(["SUDIVYA KUMAR"] + CANDIDATE_HI["SUDIVYA KUMAR"]),
             "Nirbhay Kumar Shahabadi": terms(["NIRBHAY KUMAR SHAHABADI"]
                                              + CANDIDATE_HI["NIRBHAY KUMAR SHAHABADI"])},
    person_ac={"Sudivya Kumar": [GIRIDIH], "Nirbhay Kumar Shahabadi": [GIRIDIH]},
    areas={PIRTAND_GP: terms(["Harladih", "हरलाडीह"])},
    area_ac={PIRTAND_GP: GIRIDIH},
)


def test_every_issue_in_the_llm_enum_has_keywords():
    assert set(ISSUE_TERMS) | {"other"} == set(ISSUE_ENUM)


def test_the_api_lists_the_same_issues_as_the_labellers():
    from api.routers.news import ISSUES

    assert ISSUES == ISSUE_ENUM


def test_sir_stories_have_an_issue_of_their_own():
    lab = label("एसआईआर के विरोध में झामुमो का प्रदर्शन, वोट चोरी का आरोप", "", R)
    assert "electoral-roll/SIR" in lab.issues


def test_a_bypoll_story_gets_parties_candidate_and_high_relevance():
    lab = label("गिरिडीह उपचुनाव: झामुमो ने सुदिव्य कुमार सोनू को फिर उतारा, भाजपा में मंथन",
                "", R, ac_ids=[GIRIDIH])
    assert lab.parties == ["BJP", "JMM"]  # one mention each; ties go alphabetically
    assert lab.persons == ["Sudivya Kumar"]
    assert lab.scope == "ac"
    assert lab.relevance >= 0.8


def test_parties_are_ordered_by_mentions():
    lab = label("BJP attacks JMM", "BJP says the JMM government failed. BJP will protest.", R)
    assert lab.parties == ["BJP", "JMM"]
    assert lab.party_mentions == {"BJP": 3, "JMM": 2}


def test_issues_from_hindi_with_suffixes():
    lab = label("पीरटांड़ में पेयजल संकट, ग्रामीणों ने सड़क जाम की", "बिजली भी नहीं", R)
    assert {"water", "roads", "electricity"} <= set(lab.issues)


def test_police_is_not_a_bridge():
    # पुल (bridge) is deliberately not a roads term: it would match पुलिस.
    lab = label("गिरिडीह पुलिस ने दो को गिरफ्तार किया", "", R, ac_ids=[GIRIDIH])
    assert "roads" not in lab.issues
    assert "law-and-order" in lab.issues


def test_no_keyword_means_other():
    lab = label("झारखंड में भाजपा की बैठक", "", R)
    assert lab.issues == ["other"]


def test_parasnath_issue():
    lab = label("Parasnath hill: tribal groups assert Marang Buru claim", "", R)
    assert "Parasnath/Marang Buru" in lab.issues


def test_an_area_name_tags_the_area_and_its_constituency():
    lab = label("हरलाडीह पंचायत में मनरेगा घोटाला, विधायक ने जांच की मांग की", "", R)
    assert lab.area_ids == [PIRTAND_GP]
    assert lab.ac_ids == [GIRIDIH] and lab.scope == "ac"
    assert {"welfare-schemes", "corruption"} <= set(lab.issues)


def test_local_but_not_political_is_low_relevance():
    lab = label("गिरिडीह में सड़क हादसा, दो घायल", "", R, ac_ids=[GIRIDIH])
    assert lab.relevance <= 0.25
    assert lab.parties == [] and lab.persons == []


def test_state_politics_without_a_place_ranks_below_local_election_news():
    state = label("Jharkhand BJP meets in Ranchi", "", R)
    local = label("Giridih bypoll: BJP names candidate", "", R, ac_ids=[GIRIDIH])
    assert state.scope == "state"
    assert state.relevance < local.relevance


def test_sentiment_is_never_guessed():
    lab = label("JMM wins big, BJP routed", "", R)
    assert not hasattr(lab, "sentiment")


def test_placeholder_and_ward_names_are_not_matched():
    assert not _usable_area_name(fold("Unassigned (PS list pending)"))
    assert not _usable_area_name(fold("Ward 7"))
    assert _usable_area_name(fold("Harladih"))


def test_a_candidate_brings_their_constituency():
    lab = label("Babulal Marandi condoles demise of Minister Sudivya Kumar", "", R)
    assert lab.persons == ["Sudivya Kumar"]
    assert lab.ac_ids == [GIRIDIH] and lab.scope == "ac"


def test_a_candidate_name_outside_politics_is_not_tagged():
    lab = label("सुदिव्य कुमार नाम के युवक की बाइक चोरी", "", R)
    assert lab.persons == [] and lab.ac_ids == []


def test_a_residence_certificate_is_not_a_housing_scheme():
    lab = label("झारखंड में SIR के लिए जाति अथवा आवासीय प्रमाण पत्र की आवश्यकता नहीं", "", R)
    assert "welfare-schemes" not in lab.issues
    assert "welfare-schemes" in label("अबुआ आवास योजना की किस्त जारी", "", R).issues
