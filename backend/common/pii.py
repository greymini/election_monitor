"""Patterns for personal data, and the scanner that looks for them.

One module so the same definitions serve the filesystem scan, the database scan
and the free-text screen on every write path. The audit's core finding was that
the aggregate-only guarantee was true in the schema and false on disk, and that
the check meant to prove it inspected `information_schema` column names - so it
could not see file contents, `review_queue.payload`, `ground_report.text` or
`news_item.body`.

The three patterns, and why each is written the way it is:

**EPIC** — `[A-Z]{3}[0-9]{7}`. Three letters then seven digits is the elector's
photo identity card number, printed against every name in a roll. This is the
highest-signal marker that a roll has leaked: nothing else in this system's
vocabulary has that shape.

**Mobile** — `(?<!\\d)[6-9]\\d{9}(?!\\d)`. Indian mobile numbers start 6-9 and
are ten digits. The lookarounds matter more than they look: without them the
pattern fires inside any longer digit run, and this system is full of them -
vote counts, elector totals, sha256 prefixes, LGD codes. A bare `[6-9]\\d{9}`
would match the middle of a 12-digit figure and cry wolf on every Form 20.

**Aadhaar** — `(?<!\\d)\\d{4}\\s?\\d{4}\\s?\\d{4}(?!\\d)`. Twelve digits,
optionally spaced in groups of four as it is printed. Deliberately *not*
Verhoeff-validated: this is a leak detector, and a mistyped Aadhaar is still
Aadhaar. The cost is false positives on any 12-digit run, which is why
`describe()` reports the pattern class rather than asserting certainty.

A match is never echoed back. A privacy report that quotes the personal data it
found has moved the leak into the log, the terminal scrollback and whatever
ticket the operator pastes it into.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

EPIC = re.compile(r"\b[A-Z]{3}[0-9]{7}\b")
MOBILE = re.compile(r"(?<!\d)[6-9]\d{9}(?!\d)")
AADHAAR = re.compile(r"(?<!\d)\d{4}\s?\d{4}\s?\d{4}(?!\d)")

PATTERNS: dict[str, re.Pattern[str]] = {
    "epic": EPIC,
    "mobile": MOBILE,
    "aadhaar": AADHAAR,
}

# Text extensions worth reading. A PDF under raw/ is the audit trail and is
# expected to contain personal data; the point of the scan is that nothing
# *derived* from it does.
TEXT_SUFFIXES = {".json", ".txt", ".csv", ".log", ".md", ".sql", ".tsv", ".xml", ".html"}


@dataclass(frozen=True)
class Finding:
    """One hit. Carries where and what class, never the matched text."""

    where: str
    pattern: str
    count: int

    def describe(self) -> str:
        return (
            f"{self.where}: {self.count} value(s) matching the {self.pattern} pattern. "
            "The matched text is deliberately not shown."
        )


def scan_text(text: str, where: str) -> list[Finding]:
    """Every pattern class present in a string."""
    out: list[Finding] = []
    for name, pattern in PATTERNS.items():
        hits = pattern.findall(text or "")
        if hits:
            out.append(Finding(where=where, pattern=name, count=len(hits)))
    return out


def scan_file(path: Path) -> list[Finding]:
    """Scan one text file. Binary and unreadable files are skipped, not failed."""
    if path.suffix.lower() not in TEXT_SUFFIXES:
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError):  # pragma: no cover
        return []
    return scan_text(text, where=str(path))


def scan_tree(root: Path, limit_bytes: int = 50_000_000) -> list[Finding]:
    """Scan every text file under a directory.

    `limit_bytes` caps the individual file size read. A single roll's extracted
    text can run to hundreds of megabytes, and the first few megabytes are
    enough to establish that it is there - this is a detector, not an inventory.
    """
    findings: list[Finding] = []
    if not root.exists():
        return findings
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        try:
            if path.stat().st_size > limit_bytes:
                # Read only the head of a very large file.
                with open(path, encoding="utf-8", errors="replace") as fh:
                    findings += scan_text(fh.read(limit_bytes), where=f"{path} (first part)")
                continue
        except OSError:  # pragma: no cover
            continue
        findings += scan_file(path)
    return findings


def screen(text: str, field: str = "text") -> None:
    """Raise if a free-text value carries personal data.

    Used on every free-text write path. E5: `POST /ground-reports` accepted 4,000
    characters from any authenticated user, stored them verbatim and embedded
    them for vector search, with no screening - the one unguarded ingress in a
    system whose whole design is aggregate-only. Its docstring asserted that
    reporters "write about places and conditions, not about named individuals",
    which is an assumption, not a control.

    The message names the pattern class and never the matched text, in both
    languages, because the person who needs to understand it is a booth
    in-charge on a phone.
    """
    findings = scan_text(text, where=field)
    if not findings:
        return
    classes = ", ".join(f.pattern for f in findings)
    raise PiiRejected(
        f"This {field} looks like it contains personal data ({classes}) and has not "
        "been saved. Write about places and conditions, not about named individuals: "
        "no EPIC numbers, phone numbers or Aadhaar numbers.\n"
        f"इस {field} में व्यक्तिगत जानकारी ({classes}) प्रतीत होती है, अतः यह सहेजा नहीं गया। "
        "कृपया स्थान और स्थिति के बारे में लिखें, नामित व्यक्तियों के बारे में नहीं: "
        "EPIC संख्या, फ़ोन नंबर या आधार संख्या न लिखें।"
    )


class PiiRejected(ValueError):
    """A free-text value was refused because it matched a personal-data pattern."""
