"""Router refusal patterns (LLD 8, 12). These run with no API key: the local
blocklist must catch an individual-voter question before any model call."""

import pytest

from chatbot.router import REFUSAL_EN, REFUSAL_HI, detect_language, is_blocked, refusal_text


@pytest.mark.parametrize("question", [
    "Is Ram Prasad Mahato on the voter roll?",
    "what caste is Sudivya Kumar",
    "give me the phone number of the booth agent",
    "Show me EPIC ABC1234567",
    "list all voters in booth 42",
    "what is the home address for this elector",
    "मतदाता सूची दो",
    "किस व्यक्ति की जाति क्या है",
    "मकान संख्या 45 में कौन रहता है",
])
def test_individual_questions_are_blocked_locally(question):
    assert is_blocked(question), f"not blocked: {question!r}"


@pytest.mark.parametrize("question", [
    "2024 में BJP ने किन 20 बूथों पर सबसे ज़्यादा लीड ली?",
    "Pirtand block में 2019 से 2024 के बीच कितने नए वोटर जुड़े?",
    "Which wards voted AJSU in LS 2024 but JMM in VS 2024?",
    "What were the top three local issues in the news last month?",
    "If 60% of JLKM's 2024 votes shift to BJP, what is the projected margin?",
    "How many electors are there in booth B0042?",
    "what is the caste composition of ward 12",
])
def test_legitimate_aggregate_questions_pass(question):
    assert not is_blocked(question), f"wrongly blocked: {question!r}"


def test_language_detection():
    assert detect_language("2024 में कितने वोटर थे?") == "hi"
    assert detect_language("How many electors?") == "en"
    assert detect_language("Pirtand block में kitne new voters jude?") == "hinglish"


def test_refusal_is_in_the_users_language_and_offers_an_alternative():
    assert refusal_text("hi") == REFUSAL_HI
    assert refusal_text("en") == REFUSAL_EN
    for text in (REFUSAL_HI, REFUSAL_EN):
        assert "booth" in text.lower() or "बूथ" in text
