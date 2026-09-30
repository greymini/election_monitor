"""Extracting one assembly segment from a PC-level Form 20 (master prompt 3.6).

A Lok Sabha Form 20 covers a whole parliamentary constituency. PC-11 Giridih has
six assembly segments - Giridih, Dumri, Gomia, Bermo, Tundi, Baghmara - and each
one's polling stations appear under its own AC header inside the document, with
the PS numbering restarting at 1 in every section. So a naive parse of an LS
Form 20 produces six stations numbered 1, six numbered 2, and so on, all landing
on whichever booth PS 1 happens to map to.

This module finds the section boundaries and returns only the pages belonging to
the target AC.

Why it matters beyond tidiness. The HLD calls the LS-versus-VS split "the single
most important dynamic to model": AJSU led the Giridih assembly segment at the
2024 Lok Sabha poll and JMM held the seat at the assembly election six months
later. Every figure in that comparison - `mv_transfer_ls_vs`, `floating_pct`,
the transfer page - needs the LS *segment*, not the PC. The audit found the LS
load path undocumented and the resulting `floating_pct` uniformly 50.00% because
only one poll type was ever loaded (D4), so this is the missing half of that.

The header patterns are deliberately loose about punctuation and script but
strict about structure: an AC header states a number and a name, and a line that
does not state both is not a section boundary. Guessing loosely here would split
a document in the wrong place and attribute one segment's votes to another,
which is worse than refusing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from common.logging_setup import get_logger
from common.textnorm import normalize_digits, normalize_text

log = get_logger(__name__)

# An AC section header inside a PC-level Form 20. Both scripts, and both
# orderings - "Assembly Segment 32 - Giridih" and "32-गिरिडीह विधान सभा".
_AC_HEADER_PATTERNS = (
    # 'Assembly Segment No. 32 - Giridih' / 'Assembly Constituency 32 Giridih'
    re.compile(
        r"(?:assembly|vidhan)\s*(?:segment|constituency|sabha)?\s*"
        r"(?:no\.?|number)?\s*[-:]?\s*(\d{1,3})\s*[-:.]?\s*([^\d\n]{2,60})",
        re.IGNORECASE,
    ),
    # '32 - Giridih (Assembly Segment)'
    re.compile(
        r"\b(\d{1,3})\s*[-:.]\s*([^\d\n]{2,60}?)\s*"
        r"[(\[]?\s*(?:assembly|vidhan)\s*(?:segment|sabha)",
        re.IGNORECASE,
    ),
    # Devanagari: '32-गिरिडीह विधान सभा' / 'विधान सभा क्षेत्र 32 गिरिडीह'
    re.compile(r"(?:विधान\s*सभा)\s*(?:क्षेत्र)?\s*(?:सं\.?)?\s*[-:]?\s*(\d{1,3})\s*[-:.]?\s*([^\d\n]{2,60})"),
    re.compile(r"\b(\d{1,3})\s*[-:.]\s*([^\d\n]{2,60}?)\s*विधान\s*सभा"),
)


@dataclass
class Section:
    """One assembly segment's slice of a PC-level document."""

    ac_number: int
    name: str
    header: str
    first_page: int
    last_page: int | None = None
    page_numbers: list[int] = field(default_factory=list)

    @property
    def page_range(self) -> str:
        if self.last_page is None or self.last_page == self.first_page:
            return str(self.first_page)
        return f"{self.first_page}-{self.last_page}"


def find_ac_header(text: str) -> tuple[int, str] | None:
    """The AC number and name stated on this page, if it opens a section.

    Only the first few lines are considered: an AC name mentioned in a footnote
    or a running footer is not a section boundary, and treating it as one would
    cut the document in the wrong place.
    """
    # Split first, then normalise each line. `normalize_text` collapses all
    # whitespace including newlines, so normalising the page first turns it into
    # one long line and the "first few lines" guard stops guarding anything -
    # a header in a page footer then reads as a section boundary.
    raw_lines = (text or "").splitlines()[:8]
    head = "\n".join(normalize_digits(normalize_text(line)) for line in raw_lines)

    for pattern in _AC_HEADER_PATTERNS:
        match = pattern.search(head)
        if not match:
            continue
        try:
            number = int(match.group(1))
        except (TypeError, ValueError):
            continue
        name = " ".join(match.group(2).split()).strip(" -:.()[]")
        if not name or number <= 0 or number > 300:
            continue
        return number, name
    return None


def split_sections(pages: list) -> list[Section]:
    """Divide a PC-level document into its assembly segments.

    `pages` is a list of objects with `page_no` and `text`, as
    `extract_pdf.extract_document` returns. Pages before the first header
    belong to no segment - they are the document's own preamble - and are
    dropped rather than attributed to the first AC.
    """
    sections: list[Section] = []
    for page in pages:
        found = find_ac_header(getattr(page, "text", "") or "")
        if found is not None:
            number, name = found
            if sections and sections[-1].ac_number == number:
                # A continuation page repeating the header, not a new section.
                sections[-1].page_numbers.append(page.page_no)
                sections[-1].last_page = page.page_no
                continue
            if sections:
                sections[-1].last_page = page.page_no - 1
            sections.append(Section(
                ac_number=number,
                name=name,
                header=" ".join((getattr(page, "text", "") or "").split())[:200],
                first_page=page.page_no,
                page_numbers=[page.page_no],
            ))
        elif sections:
            sections[-1].page_numbers.append(page.page_no)
            sections[-1].last_page = page.page_no

    return sections


def select_segment(pages: list, ac_number: int) -> tuple[list, Section]:
    """The pages belonging to one AC, and the section that describes them.

    Raises rather than returning an empty list. A PC-level document that does
    not contain the requested segment is either the wrong document or a parse
    failure, and returning nothing would load an empty election - which looks
    identical to "the segment had no votes".
    """
    sections = split_sections(pages)
    if not sections:
        raise ValueError(
            "no assembly-segment headers found in this document. If it is a single-AC "
            "Form 20, parse it without --type LS; if it is a PC-level document whose "
            "headers this does not recognise, add the pattern to "
            "ingest/ls_segment._AC_HEADER_PATTERNS with a fixture."
        )

    wanted = [s for s in sections if s.ac_number == ac_number]
    if not wanted:
        found = ", ".join(f"{s.ac_number} {s.name}" for s in sections)
        raise ValueError(
            f"this document has no segment for AC {ac_number}. It contains: {found}"
        )
    if len(wanted) > 1:
        # Two sections for one AC means the header matcher fired on something
        # that is not a boundary. Refusing is right: loading both would double
        # every vote in the segment.
        ranges = ", ".join(s.page_range for s in wanted)
        raise ValueError(
            f"AC {ac_number} appears in {len(wanted)} separate sections (pages {ranges}). "
            "The header matcher has probably fired on a footer or a contents line; "
            "loading both would double every vote in the segment."
        )

    section = wanted[0]
    keep = set(section.page_numbers)
    selected = [p for p in pages if p.page_no in keep]
    log.info("AC %s (%s): pages %s of %d, %d page(s) selected",
             ac_number, section.name, section.page_range, len(pages), len(selected))
    return selected, section


def summarise(pages: list) -> str:
    """A one-line description of what a PC-level document contains, for a dry
    run. An operator pointing the parser at a 400-page PDF should be able to see
    the segment list before loading anything."""
    sections = split_sections(pages)
    if not sections:
        return "no assembly-segment headers found"
    return "; ".join(f"AC {s.ac_number} {s.name} (pages {s.page_range})" for s in sections)
