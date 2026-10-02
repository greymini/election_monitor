"""The compliance test the LLD requires (LLD 12): parse_roll must not persist,
return or log any individual voter data.

If this test fails, the aggregate-only design has been broken and the system is
creating a sensitive personal-data asset it is not allowed to hold.
"""

import json
from dataclasses import asdict

from ingest.parse_roll import RollCounts, scan_mother_roll, scan_supplement

# Realistic mother-roll text. The names, EPIC numbers and house numbers here are
# invented for the test and must never survive parsing.
MOTHER_ROLL = """
मतदान केन्द्र संख्या : 12    भाग संख्या : 12
प्रा०वि० चतरो, ग्राम चतरो

क्रम सं 1   नाम : राम प्रसाद महतो    पिता का नाम : श्याम लाल यादव    मकान संख्या : 45    आयु : 42    लिंग : पुरुष    ABC1234567
क्रम सं 2   नाम : सीता देवी    पति का नाम : राम प्रसाद महतो    मकान संख्या : 45    आयु : 38    लिंग : महिला    ABC1234568
क्रम सं 3   नाम : मोहम्मद इरशाद अंसारी    पिता का नाम : अब्दुल अंसारी    मकान संख्या : 46    आयु : 19    लिंग : पुरुष    ABC1234569
क्रम सं 4   नाम : बुधनी मुर्मू    पति का नाम : सोमरा मुर्मू    मकान संख्या : 47    आयु : 64    लिंग : महिला    ABC1234570
"""

SECRETS = [
    "राम प्रसाद महतो", "सीता देवी", "मोहम्मद इरशाद अंसारी", "बुधनी मुर्मू",
    "श्याम लाल यादव", "अब्दुल अंसारी", "सोमरा मुर्मू",
    "ABC1234567", "ABC1234568", "ABC1234569", "ABC1234570",
    "मकान संख्या", "45", "46", "47",
]


def _serialise(counts: RollCounts) -> str:
    """Everything the object could possibly carry, as text."""
    payload = asdict(counts)
    payload["surnames"] = dict(counts.surnames)
    return repr(counts) + json.dumps(payload, ensure_ascii=False, default=str)


def test_counts_are_correct():
    c = scan_mother_roll(MOTHER_ROLL)
    assert c.ps_number == 12
    assert c.roll_part == 12
    assert c.electors == 4
    assert c.male == 2
    assert c.female == 2
    assert c.age_18_19 == 1        # the 19-year-old
    assert c.age_30_39 == 1
    assert c.age_40_49 == 1
    assert c.age_60p == 1


def test_no_full_name_or_epic_survives_parsing():
    c = scan_mother_roll(MOTHER_ROLL)
    blob = _serialise(c)
    for secret in SECRETS:
        assert secret not in blob, f"{secret!r} leaked out of parse_roll"


def test_only_surnames_are_aggregated_and_only_as_counts():
    c = scan_mother_roll(MOTHER_ROLL)
    # Surnames are the one thing kept, as a histogram, to drive booth-level
    # caste estimates (HLD 5). Given names must not be there.
    assert set(c.surnames) <= {"महतो", "देवी", "अंसारी", "मुर्मू"}
    assert all(isinstance(v, int) for v in c.surnames.values())
    for given_name in ("राम", "सीता", "मोहम्मद", "बुधनी", "इरशाद", "प्रसाद"):
        assert given_name not in c.surnames


def test_relatives_surnames_are_not_counted_as_electors():
    """The father of elector 1 is a Yadav; elector 1 is a Mahato. Counting the
    relative would silently skew the booth's community estimate."""
    c = scan_mother_roll(MOTHER_ROLL)
    assert "यादव" not in c.surnames
    assert sum(c.surnames.values()) <= c.electors


SUPPLEMENT = """
मतदान केन्द्र संख्या : 12    भाग संख्या : 12

परिवर्धन सूची
क्रम सं 1   नाम : अनिल कुमार महतो    आयु : 18    लिंग : पुरुष    ABC9000001
क्रम सं 2   नाम : पूजा कुमारी    आयु : 19    लिंग : महिला    ABC9000002

विलोपन सूची
क्रम सं 1   नाम : रामधन महतो    आयु : 82    लिंग : पुरुष    ABC8000001    कारण : मृत्यु

संशोधन सूची
क्रम सं 1   नाम : सुनीता देवी    आयु : 45    लिंग : महिला    ABC7000001
"""


def test_supplement_sections():
    c = scan_supplement(SUPPLEMENT)
    assert c.ps_number == 12
    assert c.additions == 2
    assert c.deletions == 1
    assert c.modifications == 1
    assert c.add_18_19 == 2
    assert c.add_female == 1
    assert c.add_male == 1
    assert c.del_death == 1


def test_supplement_keeps_nothing_personal():
    c = scan_supplement(SUPPLEMENT)
    blob = _serialise(c)
    for secret in ("अनिल कुमार महतो", "पूजा कुमारी", "रामधन महतो", "सुनीता देवी",
                   "ABC9000001", "ABC9000002", "ABC8000001", "ABC7000001"):
        assert secret not in blob, f"{secret!r} leaked out of the supplement parser"
