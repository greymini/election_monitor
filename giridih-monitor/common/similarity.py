"""Jaro-Winkler similarity for the booth crosswalk (LLD 4.4).

Uses jellyfish when it is installed (the worker image has it) and falls back to
a pure-Python implementation otherwise, so crosswalk scoring is testable without
native dependencies and gives the same answers either way.
"""

from __future__ import annotations

try:
    from jellyfish import jaro_winkler_similarity as _jw

    _HAVE_JELLYFISH = True
except ImportError:  # pragma: no cover - exercised only where jellyfish is absent
    _jw = None
    _HAVE_JELLYFISH = False


def _jaro(s1: str, s2: str) -> float:
    if s1 == s2:
        return 1.0
    len1, len2 = len(s1), len(s2)
    if len1 == 0 or len2 == 0:
        return 0.0

    window = max(len1, len2) // 2 - 1
    if window < 0:
        window = 0

    s1_matches = [False] * len1
    s2_matches = [False] * len2
    matches = 0

    for i in range(len1):
        start = max(0, i - window)
        end = min(i + window + 1, len2)
        for j in range(start, end):
            if s2_matches[j] or s1[i] != s2[j]:
                continue
            s1_matches[i] = s2_matches[j] = True
            matches += 1
            break

    if matches == 0:
        return 0.0

    transpositions = 0
    k = 0
    for i in range(len1):
        if not s1_matches[i]:
            continue
        while not s2_matches[k]:
            k += 1
        if s1[i] != s2[k]:
            transpositions += 1
        k += 1
    transpositions //= 2

    return (matches / len1 + matches / len2 + (matches - transpositions) / matches) / 3.0


def jaro_winkler(s1: str, s2: str, prefix_weight: float = 0.1) -> float:
    """Jaro-Winkler similarity in [0, 1]."""
    if s1 is None or s2 is None:
        return 0.0
    if s1 == s2:
        return 1.0
    if not s1 or not s2:
        return 0.0
    if _HAVE_JELLYFISH:
        return float(_jw(s1, s2))

    jaro = _jaro(s1, s2)
    prefix = 0
    for a, b in zip(s1[:4], s2[:4], strict=False):
        if a != b:
            break
        prefix += 1
    return jaro + prefix * prefix_weight * (1 - jaro)


def token_overlap(s1: str, s2: str) -> float:
    """Jaccard overlap of whitespace tokens. Used as a tie-breaker where one
    name carries an extra village qualifier the other does not."""
    a = {t for t in (s1 or "").split() if t}
    b = {t for t in (s2 or "").split() if t}
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)
