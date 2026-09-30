"""Deduplication before anything reaches the LLM (LLD 6.2).

Local aggregators republish the same story verbatim, so a naive crawler would
pay to label the same article five times. Two checks:

  * url_hash - exact, catches re-crawls
  * simhash over the title - near-duplicate, Hamming distance <= 3

Pure functions, no dependency on the `simhash` package: a 64-bit simhash over
character shingles is a dozen lines and keeps this testable.
"""

from __future__ import annotations

import hashlib
import re

from common.textnorm import fold

HAMMING_THRESHOLD = 3
SHINGLE = 3
BITS = 64
_MASK = (1 << BITS) - 1


def url_hash(url: str) -> str:
    """Hash of the URL with tracking parameters and fragments stripped."""
    clean = re.sub(r"[?#].*$", "", (url or "").strip().lower())
    clean = clean.rstrip("/")
    return hashlib.sha256(clean.encode()).hexdigest()


def title_hash(title: str) -> str:
    return hashlib.sha256(fold(title).encode()).hexdigest()


def _shingles(text: str, size: int = SHINGLE) -> list[str]:
    t = fold(text).replace(" ", "")
    if len(t) <= size:
        return [t] if t else []
    return [t[i: i + size] for i in range(len(t) - size + 1)]


def simhash(text: str) -> int:
    """64-bit simhash over character shingles. Works on Devanagari as-is."""
    vector = [0] * BITS
    shingles = _shingles(text)
    if not shingles:
        return 0
    for shingle in shingles:
        h = int.from_bytes(hashlib.md5(shingle.encode()).digest()[:8], "big")
        for bit in range(BITS):
            vector[bit] += 1 if (h >> bit) & 1 else -1
    out = 0
    for bit in range(BITS):
        if vector[bit] > 0:
            out |= 1 << bit
    return out


def hamming(a: int, b: int) -> int:
    return bin((a ^ b) & _MASK).count("1")


def is_near_duplicate(a: int, b: int, threshold: int = HAMMING_THRESHOLD) -> bool:
    return hamming(a, b) <= threshold


def to_signed_64(value: int) -> int:
    """Postgres BIGINT is signed; a 64-bit simhash is not."""
    value &= _MASK
    return value - (1 << BITS) if value >= (1 << (BITS - 1)) else value


def from_signed_64(value: int) -> int:
    return value + (1 << BITS) if value < 0 else value


def find_duplicate(candidate_title: str, existing: list[tuple[int, int]],
                   threshold: int = HAMMING_THRESHOLD) -> int | None:
    """Return the news_id of a near-duplicate, if any. `existing` is
    [(news_id, signed_simhash), ...]."""
    h = simhash(candidate_title)
    for news_id, stored in existing:
        if is_near_duplicate(h, from_signed_64(stored), threshold):
            return news_id
    return None
