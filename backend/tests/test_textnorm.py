from common import textnorm as t


def test_devanagari_digits():
    assert t.normalize_digits("१२३४५६७८९०") == "1234567890"
    assert t.parse_int("२,००४") == 2004
    assert t.parse_int("no digits here") is None
    assert t.parse_int(None) is None


def test_building_canonicalisation_across_year_variants():
    """The same polling station, written five ways across revisions, must
    canonicalise identically enough to match."""
    variants = [
        "प्रा०वि० चतरो",
        "प्रा. वि. चतरो",
        "प्राथमिक विद्यालय चतरो",
        "प्रा०वि० चतरो (उत्तरी भाग)",
    ]
    canon = {t.building_type(v) for v in variants}
    assert canon == {"PRIMARY_SCHOOL"}


def test_building_types():
    assert t.building_type("उत्क्रमित म० वि० पीरटांड") == "MIDDLE_SCHOOL"
    assert t.building_type("U.M.S. Khukhra") == "MIDDLE_SCHOOL"
    assert t.building_type("+2 उच्च विद्यालय गिरिडीह") == "HIGH_SCHOOL"
    assert t.building_type("आंगनबाड़ी केंद्र मधुबन") == "ANGANWADI"
    assert t.building_type("पंचायत भवन डूमरी") == "PANCHAYAT_BHAWAN"
    assert t.building_type("Primary School Chatro Part-2") == "PRIMARY_SCHOOL"


def test_room_and_part_qualifiers_are_dropped():
    assert t.canonical_building("प्रा०वि० चतरो (उत्तरी भाग)") == \
           t.canonical_building("प्रा०वि० चतरो")


def test_transliteration_keeps_inherent_vowel():
    """'महतो' must not collapse to 'mhto' - crosswalk scoring depends on it."""
    assert t.to_latin("महतो") == "mahato"
    assert "ansar" in t.to_latin("अंसारी")
    assert t.to_latin("Already Latin") == "already latin"


def test_last_token_for_surname_counting():
    assert t.last_token("राम प्रसाद महतो") == "महतो"
    assert t.last_token("  ") == ""


def test_anusvara_is_the_nasal_of_the_next_consonant():
    """ं is 'm' before a labial and 'n' otherwise (it was always 'm')."""
    assert "champ" in t.to_latin("चंपा")
    assert "kamb" in t.to_latin("कंबल")
    assert "mand" in t.to_latin("मंडल")
    assert "sanj" in t.to_latin("संजय")
