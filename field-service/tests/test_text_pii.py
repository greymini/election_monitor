import pytest

from pulse.common.pii import screen
from pulse.common.text import normalise, script_of, tokens


def test_roman_folding():
    assert normalise("Chaai  NASHTA!") == normalise("chai nashta") == "chai nashta"
    assert normalise("darri") == normalise("dari")
    assert normalise("chhapai") == normalise("chapai")
    assert normalise("deewar") == normalise("divar")


def test_devanagari_folding():
    assert normalise("गाड़ी") == normalise("गाडी") == normalise("गाडि")   # nukta and long i
    assert normalise("पूजा") == normalise("पुजा")
    assert normalise("चाँद") == normalise("चांद")                          # chandrabindu
    assert normalise("चाय‍") == normalise("चाय")                      # zero-width joiner


def test_punctuation_splits_tokens_and_matras_survive():
    assert tokens("चाय-नाश्ता, बैठक") == [normalise("चाय"), normalise("नाश्ता"), normalise("बैठक")]


def test_script_of():
    assert script_of("चाय") == "deva"
    assert script_of("chai") == "latn"


@pytest.mark.parametrize("text,kinds", [
    ("call 9876543210", ["mobile"]),
    ("+91 98765 43210 se baat", ["mobile"]),
    ("09876543210", ["mobile"]),
    ("९८७६५४३२१०", ["mobile"]),
    ("aadhaar 2345 6789 0123", ["aadhaar"]),
    ("234567890123", ["aadhaar"]),
    ("voter id ABC1234567", ["epic"]),
    ("JH/01/123/456789", ["epic"]),
])
def test_identifiers_are_found(text, kinds):
    assert screen(text) == kinds


@pytest.mark.parametrize("text", [
    "1500 chai nashta",
    "tent house 25000",
    "bill no 12345",
    "5123456789 0",          # 10 digits starting 5: not a mobile
    "12345678901234",        # 14 digits: none of the patterns
    None,
    "",
])
def test_ordinary_text_passes(text):
    assert screen(text) == []
