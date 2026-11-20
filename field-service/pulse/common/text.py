"""Minimal text normalisation for matching (local stand-in for the core's `common` module).

Interface: `normalise(text) -> str`, `tokens(text) -> list[str]`, `fold_digits(text) -> str`.
Devanagari: nukta dropped, chandrabindu -> anusvara, long i/u folded to short, zero-width removed.
Roman: lowercase, aa->a, ee->i, oo->u, ph->f, w->v, z->j, then doubled letters collapse.
The same function normalises keywords at seed time and user text at match time.
"""
from __future__ import annotations

import re
import unicodedata

_DEVA_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")

_DEVA_FOLD = str.maketrans({
    "़": None,      # nukta
    "‌": None,      # zero-width non-joiner
    "‍": None,      # zero-width joiner
    "ँ": "ं",  # chandrabindu -> anusvara
    "ी": "ि",  # vowel sign II -> I
    "ू": "ु",  # vowel sign UU -> U
    "ई": "इ",  # letter II -> I
    "ऊ": "उ",  # letter UU -> U
})

_ROMAN_PAIRS = (("aa", "a"), ("ee", "i"), ("oo", "u"), ("ph", "f"), ("w", "v"), ("z", "j"))
_REPEAT = re.compile(r"([a-z])\1+")
_ASCII_WORD = re.compile(r"^[a-z0-9]+$")


def fold_digits(text: str) -> str:
    return text.translate(_DEVA_DIGITS)


def _fold_roman(token: str) -> str:
    for a, b in _ROMAN_PAIRS:
        token = token.replace(a, b)
    return _REPEAT.sub(r"\1", token)


def _is_separator(ch: str) -> bool:
    cat = unicodedata.category(ch)
    return cat[0] in ("P", "S", "Z", "C")


def normalise(text: str) -> str:
    s = unicodedata.normalize("NFKC", text or "").lower()
    s = fold_digits(s).translate(_DEVA_FOLD)
    s = "".join(" " if _is_separator(ch) else ch for ch in s)
    return " ".join(_fold_roman(t) if _ASCII_WORD.match(t) else t for t in s.split())


def tokens(text: str) -> list[str]:
    return normalise(text).split()


def script_of(text: str) -> str:
    """'deva' if the text contains Devanagari, else 'latn'."""
    return "deva" if any("ऀ" <= ch <= "ॿ" for ch in text) else "latn"
