"""Devanagari / Hindi text handling shared by every parser.

Jharkhand CEO documents mix Devanagari and Latin, use Devanagari digits, and
abbreviate polling-station building names heavily and inconsistently
(प्रा०वि०, प्रा.वि., प्रावि, Prathmik Vidyalaya, P.S., Primary School ...).
Crosswalk quality (LLD 4.4) depends almost entirely on normalising these well,
so the rules live in one place with tests.
"""

from __future__ import annotations

import re
import unicodedata

# --------------------------------------------------------------------------
# Digits
# --------------------------------------------------------------------------

DEVANAGARI_DIGITS = "०१२३४५६७८९"
_DIGIT_MAP = {ord(d): str(i) for i, d in enumerate(DEVANAGARI_DIGITS)}


def normalize_digits(text: str) -> str:
    """Devanagari digits -> ASCII digits. '१२३' -> '123'."""
    return text.translate(_DIGIT_MAP)


def parse_int(text: str | None) -> int | None:
    """First integer in a cell, tolerating Devanagari digits, thousands
    separators and stray OCR punctuation. None if there is no number."""
    if text is None:
        return None
    cleaned = normalize_digits(str(text)).replace(",", "").replace(" ", "")
    m = re.search(r"-?\d+", cleaned)
    return int(m.group()) if m else None


# --------------------------------------------------------------------------
# General normalisation
# --------------------------------------------------------------------------

# Abbreviation marks used in Hindi government documents. NOTE: the
# Devanagari zero U+0966 doubles as an abbreviation mark (प्रा०वि०); it is
# stripped only in canonical_building(), where it is never a real digit.
_ABBREV_MARKS = "॰·∘•*"
_ABBREV_DANDA = "०॰"
# Includes the Devanagari danda U+0964 and double danda U+0965 - they are
# sentence punctuation, and leaving them in makes an otherwise identical
# headline or place name look different.
_PUNCT_RE = re.compile(r"[.,;:!?\"'`~^\/|_\-‐-―‘-‟।॥()\[\]{}]+")
_WS_RE = re.compile(r"\s+")


def normalize_text(text: str | None) -> str:
    """NFC-normalise, strip zero-width marks, collapse whitespace."""
    if not text:
        return ""
    t = unicodedata.normalize("NFC", str(text))
    for junk in ("​", "‌", "‍", "﻿"):
        t = t.replace(junk, "")
    t = t.replace(" ", " ")
    return _WS_RE.sub(" ", t).strip()


def normalize_block(text: str | None) -> str:
    """normalize_text() per line, keeping line structure intact.

    normalize_text collapses ALL whitespace, newlines included, which is right
    for a single field but destroys every line-oriented parser downstream (the
    Form 20 row regex, roll entries, supplement sections). Multi-line documents
    must use this instead.
    """
    if not text:
        return ""
    return "\n".join(normalize_text(line) for line in str(text).splitlines())


def strip_punct(text: str) -> str:
    return _WS_RE.sub(" ", _PUNCT_RE.sub(" ", text)).strip()


def fold(text: str | None) -> str:
    """Aggressive fold for matching: normalised, ASCII digits, no punctuation,
    no abbreviation marks, lowercase."""
    t = strip_punct(normalize_digits(normalize_text(text)))
    for ch in _ABBREV_MARKS:
        t = t.replace(ch, " ")
    return _WS_RE.sub(" ", t).strip().lower()


# --------------------------------------------------------------------------
# Polling-station building names
# --------------------------------------------------------------------------

# Order matters: longer / more specific patterns first.
# Python's  is useless after a Devanagari matra: a vowel sign is not a \w
# character, so "म वि " has no boundary after "वि". These lookarounds do the
# job instead - the token must not run into another Devanagari letter.
_DV = "ऀ-ॿ"
_DV_START = f"(?<![{_DV}])"
_DV_END = f"(?![{_DV}])"

_BUILDING_RULES: list[tuple[str, str]] = [
    (r"उत्क्रमित\s*म(?:ध्य)?\s*वि(?:द्यालय)?", "MIDDLE_SCHOOL"),
    (r"upgraded\s+middle\s+school", "MIDDLE_SCHOOL"),
    (r"\bu\s?m\s?s\b", "MIDDLE_SCHOOL"),
    (r"मध्य\s*विद्यालय", "MIDDLE_SCHOOL"),
    (_DV_START + r"म\s*वि(?:द्यालय)?" + _DV_END, "MIDDLE_SCHOOL"),
    (r"middle\s+school", "MIDDLE_SCHOOL"),
    (r"\bm\s?s\b", "MIDDLE_SCHOOL"),
    (r"नव\s*प्रा(?:थमिक)?\s*वि(?:द्यालय)?", "PRIMARY_SCHOOL"),
    (_DV_START + r"प्रा(?:थमिक)?\s*वि(?:द्यालय)?" + _DV_END, "PRIMARY_SCHOOL"),
    (r"new\s+primary\s+school", "PRIMARY_SCHOOL"),
    (r"primary\s+school", "PRIMARY_SCHOOL"),
    (r"\bn\s?p\s?s\b", "PRIMARY_SCHOOL"),
    (r"\bp\s?s\b", "PRIMARY_SCHOOL"),
    (r"\+\s*2\s*(?:उच्च|high)", "HIGH_SCHOOL"),
    (r"उच्च\s*वि(?:द्यालय)?", "HIGH_SCHOOL"),
    (_DV_START + r"उ\s*वि(?:द्यालय)?" + _DV_END, "HIGH_SCHOOL"),
    (r"high\s+school", "HIGH_SCHOOL"),
    (r"\bh\s?s\b", "HIGH_SCHOOL"),
    (r"आंगनबाड़ी\s*(?:केंद्र|केन्द्र)?", "ANGANWADI"),
    (r"anganwadi(?:\s+kendra)?", "ANGANWADI"),
    (r"पंचायत\s*भवन", "PANCHAYAT_BHAWAN"),
    (r"panchayat\s+bhawan", "PANCHAYAT_BHAWAN"),
    (r"सामुदायिक\s*भवन", "COMMUNITY_HALL"),
    (r"community\s+(?:hall|centre|center)", "COMMUNITY_HALL"),
    (r"महाविद्यालय", "COLLEGE"),
    (r"college", "COLLEGE"),
    (r"कन्या\s*विद्यालय", "GIRLS_SCHOOL"),
    (r"girls?\s+school", "GIRLS_SCHOOL"),
    (r"स्वास्थ्य\s*(?:केंद्र|केन्द्र)", "HEALTH_CENTRE"),
    (r"health\s+(?:centre|center)", "HEALTH_CENTRE"),
    (r"विद्यालय", "SCHOOL"),
    (r"school", "SCHOOL"),
]

# Qualifiers that appear in some years and not others; they carry no matching
# signal, so they are dropped before comparison.
_DROP_TOKENS = {
    "रा", "राजकीय", "राज", "कृत", "govt", "government", "sarkari",
    "उत्तरी", "दक्षिणी", "पूर्वी", "पश्चिमी", "उत्तर", "दक्षिण", "पूर्व", "पश्चिम",
    "भाग", "part", "कक्ष", "room", "no", "sankhya",
    "का", "के", "की", "एवं", "और", "and", "at", "the",
}

_PLUS_TWO_RE = re.compile(r"\+\s*2\s*")

_ROOM_RE = re.compile(
    r"(?:उत्तरी?|दक्षिणी?|पूर्वी?|पश्चिमी?)?\s*(?:भाग|कक्ष|part|room)\s*[-–]?\s*\d*",
    re.IGNORECASE,
)


def canonical_building(text: str | None) -> str:
    """Reduce a polling-station building string to a comparable canonical form.

    'प्रा०वि० चतरो (उत्तरी भाग)'   -> 'PRIMARY_SCHOOL चतरो'
    'Primary School Chatro Part-2' -> 'PRIMARY_SCHOOL chatro'
    """
    t = normalize_text(text)
    for ch in _ABBREV_DANDA:            # प्रा०वि० -> प्रा वि  (before digits)
        t = t.replace(ch, " ")
    t = normalize_digits(t)
    t = _PLUS_TWO_RE.sub(" ", t)        # "+2 उच्च विद्यालय" -> "उच्च विद्यालय"
    t = _ROOM_RE.sub(" ", t).lower()
    for ch in _ABBREV_MARKS:
        t = t.replace(ch, " ")
    t = _WS_RE.sub(" ", _PUNCT_RE.sub(" ", t)).strip()

    for pattern, token in _BUILDING_RULES:
        new_t, n = re.subn(pattern, f" {token} ", t, flags=re.IGNORECASE)
        if n:
            t = new_t
            break

    tokens = [tok for tok in _WS_RE.split(t) if tok and tok not in _DROP_TOKENS]
    types = [tok for tok in tokens if tok.isupper()]
    rest = [tok for tok in tokens if not tok.isupper()]
    return " ".join(types + rest).strip()


def building_type(text: str | None) -> str | None:
    """Just the canonical building-type token, if one is recognised."""
    for tok in canonical_building(text).split():
        if tok.isupper():
            return tok
    return None


# --------------------------------------------------------------------------
# Transliteration
# --------------------------------------------------------------------------

# Minimal Devanagari -> Latin fallback for when indic-transliteration is not
# installed (API container, unit tests). Good enough for fuzzy matching, not
# for display.
_TRANSLIT_MAP = {
    "क": "k", "ख": "kh", "ग": "g", "घ": "gh", "ङ": "n",
    "च": "ch", "छ": "chh", "ज": "j", "झ": "jh", "ञ": "n",
    "ट": "t", "ठ": "th", "ड": "d", "ढ": "dh", "ण": "n",
    "त": "t", "थ": "th", "द": "d", "ध": "dh", "न": "n",
    "प": "p", "फ": "ph", "ब": "b", "भ": "bh", "म": "m",
    "य": "y", "र": "r", "ल": "l", "व": "v", "श": "sh",
    "ष": "sh", "स": "s", "ह": "h", "ळ": "l",
    "क़": "q", "ख़": "kh", "ग़": "g", "ज़": "z", "ड़": "r", "ढ़": "rh", "फ़": "f",
    "अ": "a", "आ": "aa", "इ": "i", "ई": "ee", "उ": "u", "ऊ": "oo",
    "ऋ": "ri", "ए": "e", "ऐ": "ai", "ओ": "o", "औ": "au",
    "ा": "a", "ि": "i", "ी": "ee", "ु": "u", "ू": "oo", "ृ": "ri",
    "े": "e", "ै": "ai", "ो": "o", "ौ": "au",
    "ं": "n", "ँ": "n", "ः": "h", "्": "", "़": "",
}


_CONSONANTS = set("कखगघङचछजझञटठडढणतथदधनपफबभमयरलवशषसहळ") | {
    "क़", "ख़", "ग़", "ज़", "ड़", "ढ़", "फ़"
}
_MATRAS = set("ािीुूृेैोौ")
_VIRAMA = "्"


def _fallback_translit(text: str) -> str:
    """Devanagari -> Latin with the implicit 'a' restored.

    A consonant carries an inherent 'a' unless the next character is a vowel
    sign or a virama, so 'महतो' must give 'mahato', not 'mhto'.
    """
    out: list[str] = []
    chars = list(text)
    for i, ch in enumerate(chars):
        mapped = _TRANSLIT_MAP.get(ch)
        if mapped is None:
            out.append(ch if ch.isascii() else "")
            continue
        out.append(mapped)
        if ch in _CONSONANTS:
            nxt = chars[i + 1] if i + 1 < len(chars) else ""
            if nxt not in _MATRAS and nxt != _VIRAMA and nxt != "़":
                out.append("a")
    return _WS_RE.sub(" ", "".join(out)).strip()


def to_latin(text: str | None) -> str:
    """Transliterate Devanagari to lowercase Latin for fuzzy matching."""
    t = normalize_text(text)
    if not t:
        return ""
    if not any("ऀ" <= ch <= "ॿ" for ch in t):
        return t.lower()
    try:
        from indic_transliteration import sanscript
        from indic_transliteration.sanscript import transliterate

        latin = transliterate(t, sanscript.DEVANAGARI, sanscript.ITRANS)
        # ITRANS writes the anusvara (ं) as "M", which lowercasing turned into
        # "m" everywhere: आनंद -> "anamd", अंसारी -> "amsari", so a Devanagari
        # name never matched its Latin spelling. It is pronounced as the nasal
        # of the consonant that follows - "m" before p/b/m, "n" otherwise.
        latin = re.sub(r"M(?=[pbm])", "m", latin)
        latin = latin.replace("M", "n")
        latin = re.sub(r"[~^\.]", "", latin)
        return _WS_RE.sub(" ", latin).strip().lower()
    except Exception:
        return _fallback_translit(t).lower()


def match_key(building: str | None, place: str | None = None) -> str:
    """Latin, whitespace-free key used for crosswalk scoring."""
    parts = [canonical_building(building)]
    if place:
        parts.append(normalize_text(place))
    return to_latin(" ".join(p for p in parts if p)).replace(" ", "")


def alias_key(text: str | None) -> str:
    """Key for the area_alias table - keeps the original script, folds case."""
    return fold(text)


def last_token(name: str | None) -> str:
    """Last whitespace-separated token of a name field. Used ONLY to count
    surnames into per-booth community aggregates (HLD 5); the name itself is
    never persisted."""
    t = normalize_text(name)
    if not t:
        return ""
    return _WS_RE.split(t)[-1]
