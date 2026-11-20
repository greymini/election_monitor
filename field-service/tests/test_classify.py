from decimal import Decimal

import pytest

from pulse.common.text import normalise
from pulse.ledger import buckets
from pulse.ledger.classify import MARGIN, THRESHOLD, decide, score, suggest

D = Decimal


def test_threshold_must_be_cleared():
    assert decide({1: D("0.5")}) is None                # below threshold
    assert decide({1: THRESHOLD}).bucket_id == 1        # exactly at threshold


def test_margin_over_second_place():
    assert decide({1: D("1.0"), 2: D("1.0")}) is None   # tie
    assert decide({1: D("1.4"), 2: D("1.0")}) is None   # lead 0.4 < margin
    s = decide({1: D("1.5"), 2: D("1.0")})              # lead exactly the margin
    assert s.bucket_id == 1 and MARGIN == D("0.5")


def test_confidence_is_share_of_total():
    s = decide({1: D("2"), 2: D("1")})
    assert s.conf == D("0.667")
    assert decide({7: D("1")}).conf == D("1.000")


def test_no_scores_no_suggestion():
    assert decide({}) is None


def test_phrase_matches_whole_sequence_only():
    kws = [(normalise("street light"), 1, D("2")), (normalise("light"), 2, D("0.5"))]   # stored normalised
    assert score("street light kharab", kws) == {1: D("2"), 2: D("0.5")}
    assert score("light street", kws) == {2: D("0.5")}


def test_each_keyword_counts_once():
    assert score("chai chai chai", [("chai", 1, D("1"))]) == {1: D("1")}


@pytest.mark.parametrize("text,code", [
    ("chai nashta baithak", "FOOD"),
    ("चाय नाश्ता", "FOOD"),
    ("petrol bolero", "TRAVEL"),
    ("पेट्रोल गाड़ी", "TRAVEL"),
    ("tent shamiana kursi", "EVENT"),
    ("banner flex chhapai", "PUBLICITY"),
    ("street light", "WORKS"),
    ("chapakal marammat", "WORKS"),
    ("mandir puja chanda", "DONATE"),
    ("ilaj dawai hospital", "ASSIST"),
    ("party fund", "PARTY"),
    ("mobile recharge", "TEAM"),
    ("shadi nevta", "SOCIAL"),
])
def test_seeded_keywords_suggest(conn, text, code):
    s = suggest(conn, "ac-32", text)
    assert s is not None, text
    assert buckets.get_bucket(conn, s.bucket_id, "ac-32")["code"] == code


@pytest.mark.parametrize("text", ["kuch saman", "", "karyakarta", "chai banner"])
def test_seeded_keywords_no_suggestion(conn, text):
    assert suggest(conn, "ac-32", text) is None
