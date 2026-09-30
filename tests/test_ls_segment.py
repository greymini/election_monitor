"""Extracting one assembly segment from a PC-level Form 20 (master prompt 3.6).

Why this has to be right. A Lok Sabha Form 20 covers a whole parliamentary
constituency, and PC-11 Giridih has six assembly segments, each with its own AC
header and its own PS numbering restarting at 1. Parse it naively and you get
six stations numbered 1, six numbered 2, and so on, all landing on whichever
booth PS 1 maps to - a silent, total corruption of the LS leg.

The LS leg is also the one the HLD calls the single most important dynamic to
model: AJSU led the Giridih assembly segment at the 2024 Lok Sabha poll and JMM
held the seat six months later. The audit found the LS load path undocumented
and floating_pct consequently a uniform 50.00% across the constituency, because
only one poll type was ever loaded (D4).
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from ingest.ls_segment import (
    find_ac_header,
    select_segment,
    split_sections,
    summarise,
)


@dataclass
class Page:
    """Stands in for extract_pdf.PageText."""

    page_no: int
    text: str


# --------------------------------------------------------------------------
# Header recognition
# --------------------------------------------------------------------------


@pytest.mark.parametrize(("text", "number", "name"), [
    ("Assembly Segment No. 32 - Giridih\nSerial No.  Candidate", 32, "Giridih"),
    ("ASSEMBLY CONSTITUENCY 33 DUMRI\nPS No", 33, "DUMRI"),
    ("Assembly Segment 42 : Tundi", 42, "Tundi"),
    ("32 - Giridih (Assembly Segment)", 32, "Giridih"),
    ("विधान सभा क्षेत्र सं. 32 गिरिडीह", 32, "गिरिडीह"),
    ("32-गिरिडीह विधान सभा", 32, "गिरिडीह"),
])
def test_ac_headers_are_recognised_in_both_scripts_and_orderings(text, number, name):
    found = find_ac_header(text)
    assert found is not None, f"{text!r} not recognised"
    assert found[0] == number
    assert name.lower() in found[1].lower()


def test_a_page_with_no_header_is_not_a_boundary():
    assert find_ac_header("1  245  189  12  446\n2  301  205  15  521") is None
    assert find_ac_header("") is None


def test_an_ac_name_further_down_the_page_is_not_a_boundary():
    """A name in a footer or a running header is not a section start, and
    treating it as one would cut the document in the wrong place."""
    body = "\n".join(["1  245  189"] * 20 + ["Assembly Segment No. 33 - Dumri"])
    assert find_ac_header(body) is None


def test_an_implausible_number_is_rejected():
    assert find_ac_header("Assembly Segment No. 0 - Nowhere") is None
    assert find_ac_header("Assembly Segment No. 999 - Nowhere") is None


# --------------------------------------------------------------------------
# Splitting a document
# --------------------------------------------------------------------------


def _pc_document() -> list[Page]:
    """A miniature PC-level Form 20: a preamble, then three segments, with the
    PS numbering restarting in each - which is the whole problem."""
    return [
        Page(1, "FORM 20\nFINAL RESULT SHEET\nParliamentary Constituency 11 - Giridih"),
        Page(2, "Assembly Segment No. 31 - Gandey\n1  100  90\n2  110  95"),
        Page(3, "1  120  85\n2  130  80"),
        Page(4, "Assembly Segment No. 32 - Giridih\n1  245  189\n2  301  205"),
        Page(5, "3  198  240\n4  221  219"),
        Page(6, "Assembly Segment No. 33 - Dumri\n1  310  280"),
    ]


def test_sections_are_found_with_their_page_ranges():
    sections = split_sections(_pc_document())
    assert [s.ac_number for s in sections] == [31, 32, 33]
    assert sections[0].page_range == "2-3"
    assert sections[1].page_range == "4-5"
    assert sections[2].page_range == "6"


def test_the_preamble_belongs_to_no_segment():
    """Pages before the first header are the document's own front matter.
    Attributing them to the first AC would put the PC-wide summary inside one
    segment's rows."""
    sections = split_sections(_pc_document())
    assert 1 not in {page for s in sections for page in s.page_numbers}


def test_a_repeated_header_is_a_continuation_not_a_new_section():
    """Many Form 20s repeat the segment header on every page."""
    pages = [
        Page(1, "Assembly Segment No. 32 - Giridih\n1  245  189"),
        Page(2, "Assembly Segment No. 32 - Giridih\n2  301  205"),
        Page(3, "Assembly Segment No. 33 - Dumri\n1  310  280"),
    ]
    sections = split_sections(pages)
    assert [s.ac_number for s in sections] == [32, 33]
    assert sections[0].page_numbers == [1, 2]


# --------------------------------------------------------------------------
# Selecting one segment
# --------------------------------------------------------------------------


def test_only_the_target_segments_pages_are_returned():
    selected, section = select_segment(_pc_document(), 32)
    assert [p.page_no for p in selected] == [4, 5]
    assert section.ac_number == 32
    assert "Giridih" in section.name


def test_the_section_records_the_header_it_was_read_under():
    """So a segment load can be traced back to the page it came from in a
    400-page document."""
    _, section = select_segment(_pc_document(), 33)
    assert "33" in section.header
    assert section.page_range == "6"


def test_a_missing_segment_raises_and_lists_what_is_there():
    """Returning an empty list would load an empty election, which looks
    identical to "the segment had no votes"."""
    with pytest.raises(ValueError) as excinfo:
        select_segment(_pc_document(), 65)
    message = str(excinfo.value)
    assert "no segment for AC 65" in message
    # And says what the document does contain, so the operator can tell whether
    # they have the wrong PDF or the wrong AC.
    assert "31" in message and "32" in message and "33" in message


def test_a_document_with_no_headers_raises_with_a_next_step():
    plain = [Page(1, "1  245  189\n2  301  205")]
    with pytest.raises(ValueError) as excinfo:
        select_segment(plain, 32)
    message = str(excinfo.value)
    assert "no assembly-segment headers" in message
    # Both possibilities are named: wrong flag, or an unrecognised header.
    assert "without --type LS" in message
    assert "_AC_HEADER_PATTERNS" in message


def test_a_duplicated_segment_raises_rather_than_loading_both():
    """Two sections for one AC means the matcher fired on something that is not
    a boundary - a contents line, a footer. Loading both would double every vote
    in the segment, and the AC-total check would then fail in a way that looks
    like a bad document rather than a bad parse."""
    pages = [
        Page(1, "Assembly Segment No. 32 - Giridih\n1  245  189"),
        Page(2, "Assembly Segment No. 33 - Dumri\n1  310  280"),
        Page(3, "Assembly Segment No. 32 - Giridih\n1  245  189"),
    ]
    with pytest.raises(ValueError) as excinfo:
        select_segment(pages, 32)
    assert "2 separate sections" in str(excinfo.value)
    assert "double every vote" in str(excinfo.value)


# --------------------------------------------------------------------------
# Dry-run summary
# --------------------------------------------------------------------------


def test_summarise_lists_every_segment_for_a_dry_run():
    """An operator pointing the parser at a 400-page PDF should see the segment
    list before loading anything."""
    text = summarise(_pc_document())
    assert "AC 31" in text and "AC 32" in text and "AC 33" in text
    assert "pages 4-5" in text


def test_summarise_says_so_when_there_are_no_sections():
    assert "no assembly-segment headers" in summarise([Page(1, "1  245  189")])
