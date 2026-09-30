"""Booth crosswalk scoring (LLD 4.4). The bands matter more than the numbers:
a wrong auto-accept silently corrupts every multi-year swing."""

from ingest.crosswalk import (
    AUTO_ACCEPT,
    REVIEW_FLOOR,
    Station,
    detect_splits,
    match_stations,
    score_pair,
)


def S(ps, building, place="", part=None, uid=None):
    return Station(ps_number=ps, building=building, place=place, roll_part=part, booth_uid=uid)


def test_same_station_written_differently_auto_accepts():
    old = S(12, "प्रा०वि० चतरो", "चतरो", 12)
    new = S(15, "प्राथमिक विद्यालय चतरो", "चतरो", 12, uid="B0015")
    score, comp = score_pair(old, new)
    assert score >= AUTO_ACCEPT, (score, comp)
    assert comp["roll_part"] == 1.0


def test_part_qualifier_does_not_break_the_match():
    old = S(12, "प्रा०वि० चतरो (उत्तरी भाग)", "चतरो")
    new = S(12, "प्रा०वि० चतरो", "चतरो", uid="B0012")
    score, _ = score_pair(old, new)
    assert score >= AUTO_ACCEPT


def test_different_building_type_cannot_auto_accept():
    """A middle school at the same village is a different station and must never
    be auto-matched onto the primary school."""
    old = S(12, "प्रा०वि० चतरो", "चतरो", 12)
    new = S(12, "म० वि० चतरो", "चतरो", 12, uid="B0012")
    score, comp = score_pair(old, new)
    assert comp["type_mismatch_capped"] is True
    assert score < AUTO_ACCEPT


def test_unrelated_station_scores_below_review_floor():
    old = S(3, "प्रा०वि० चतरो", "चतरो")
    new = S(80, "पंचायत भवन मधुबन", "मधुबन", uid="B0080")
    score, _ = score_pair(old, new)
    assert score < REVIEW_FLOOR, score


def test_match_stations_assigns_bands():
    anchor = [
        S(1, "प्राथमिक विद्यालय चतरो", "चतरो", uid="B0001"),
        S(2, "मध्य विद्यालय पीरटांड", "पीरटांड", uid="B0002"),
        S(3, "पंचायत भवन मधुबन", "मधुबन", uid="B0003"),
    ]
    old = [
        S(1, "प्रा०वि० चतरो", "चतरो"),                  # clear match
        S(2, "उत्क्रमित म०वि० पीरटांड", "पीरटांड"),      # clear match
        S(9, "आंगनबाड़ी केंद्र नवाडीह", "नवाडीह"),        # nothing like it -> new
    ]
    matches = {m.ps_number: m for m in match_stations(old, anchor)}
    assert matches[1].method in {"exact", "fuzzy"} and matches[1].booth_uid == "B0001"
    assert matches[2].method in {"exact", "fuzzy"} and matches[2].booth_uid == "B0002"
    assert matches[9].method == "new" and matches[9].booth_uid is None


def test_split_detection():
    anchor = [S(1, "प्राथमिक विद्यालय चतरो", "चतरो", uid="B0001")]
    old = [
        S(1, "प्रा०वि० चतरो", "चतरो"),
        S(2, "प्रा०वि० चतरो", "चतरो"),   # same building appears twice -> split
    ]
    matches = match_stations(old, anchor)
    assert detect_splits(matches) == {"B0001"}


def test_runner_up_is_recorded_for_review():
    anchor = [
        S(1, "प्राथमिक विद्यालय चतरो", "चतरो", uid="B0001"),
        S(2, "प्राथमिक विद्यालय चतरा", "चतरा", uid="B0002"),
    ]
    m = match_stations([S(1, "प्रा०वि० चतरो", "चतरो")], anchor)[0]
    assert m.runner_up is not None
    assert m.runner_up[0] == "B0002"
