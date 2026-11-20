"""Minimal PII screen for typed fields (local stand-in for the core's screen).

`screen(text)` returns the kinds of identifier found: 'epic', 'aadhaar', 'mobile'. Callers reject
the input when the list is non-empty. Devanagari digits are folded first.
"""
from __future__ import annotations

import re

from pulse.common.text import fold_digits

_PATTERNS = {
    # voter id: three letters + seven digits, or the older state/AC/part/serial form
    "epic": re.compile(r"(?<![A-Za-z0-9])[A-Za-z]{3}\d{7}(?![A-Za-z0-9])|(?<![A-Za-z0-9])[A-Za-z]{2}/\d{2}/\d{3}/\d{6,7}(?!\d)"),
    # 12 digits, first 2-9, optionally grouped 4-4-4
    "aadhaar": re.compile(r"(?<!\d)[2-9]\d{3}[\s-]?\d{4}[\s-]?\d{4}(?!\d)"),
    # Indian mobile: optional +91 / 91 / 0, then 10 digits starting 6-9, optionally split 5-5
    "mobile": re.compile(r"(?<!\d)(?:\+?91[\s-]?|0)?[6-9]\d{4}[\s-]?\d{5}(?!\d)"),
}


def screen(text: str | None) -> list[str]:
    if not text:
        return []
    t = fold_digits(text)
    return [kind for kind, rx in _PATTERNS.items() if rx.search(t)]


MASK = "■■■■"    # four black squares: language-neutral, visibly removed


def mask(text: str | None) -> str | None:
    """Replace every identifier found by `screen` with MASK. For free text that must never be
    refused (report notes, transcripts); typed form fields use `screen` and reject instead."""
    if not text:
        return text
    t = fold_digits(text)
    if t != text:
        # keep the person's own script where nothing is masked; mask on the folded copy otherwise
        if not screen(text):
            return text
        text = t
    for rx in _PATTERNS.values():
        text = rx.sub(MASK, text)
    return text
