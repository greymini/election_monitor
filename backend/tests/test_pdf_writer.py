"""common/pdf_writer.py: does pdfplumber read back what we wrote?

The point of this module is that `ingest/extract_pdf._extract_tables` can read
its output with the `lines` strategy. A PDF that looks fine in a viewer but
yields interleaved cells to pdfplumber is useless as a fixture - and worse than
useless, because the resulting garbage looks exactly like an OCR failure and
sends someone debugging the parser instead of the fixture.
"""

from __future__ import annotations

import pytest

from common.pdf_writer import (
    A4_LANDSCAPE,
    A4_PORTRAIT,
    Page,
    PdfBuilder,
    Table,
    text_width,
    wrap,
)

pdfplumber = pytest.importorskip("pdfplumber")

LATTICE = {"vertical_strategy": "lines", "horizontal_strategy": "lines",
           "intersection_tolerance": 5}


def read_tables(path):
    with pdfplumber.open(str(path)) as doc:
        return [page.extract_tables(LATTICE) for page in doc.pages]


def read_text(path):
    with pdfplumber.open(str(path)) as doc:
        return [page.extract_text() or "" for page in doc.pages]


# ---------------------------------------------------------------------------
# Measurement and wrapping
# ---------------------------------------------------------------------------

def test_text_width_scales_with_font_size():
    assert text_width("AAAA", 10) == pytest.approx(text_width("AAAA", 5) * 2)


def test_text_width_is_per_glyph_not_per_character():
    # 'i' is 222/1000 em in Helvetica and 'W' is 944. A fixed per-character
    # estimate is what made the first mock Form 20 overflow its header cells.
    assert text_width("W", 10) > text_width("i", 10) * 4


def test_wrap_keeps_every_line_within_the_width():
    width = 60.0
    lines = wrap("Nirbhay Kumar Shahabadi Primary School Chatro", width, 7)
    assert len(lines) > 1
    for line in lines:
        assert text_width(line, 7) <= width


def test_wrap_breaks_a_word_too_long_for_the_line():
    lines = wrap("Thiruvananthapuramveryverylongtoken", 20.0, 7)
    assert len(lines) > 1
    for line in lines:
        assert text_width(line, 7) <= 20.0


def test_wrap_of_empty_text_is_one_empty_line():
    assert wrap("   ", 100.0, 8) == [""]
    assert wrap("", 100.0, 8) == [""]


# ---------------------------------------------------------------------------
# Round-tripping a ruled table
# ---------------------------------------------------------------------------

@pytest.fixture
def wide_table_pdf(tmp_path):
    header = ["Serial No. of Polling Station", "Sudivya Kumar",
              "Nirbhay Kumar Shahabadi", "Navin Anand", "Total of Valid Votes",
              "No. of Rejected Votes", "NOTA", "Total"]
    rows = [header]
    for ps in range(1, 13):
        votes = [500 + ps, 480 - ps, 90 + ps]
        nota = 7
        rows.append([str(ps)] + [str(v) for v in votes]
                    + [str(sum(votes) + nota), "0", str(nota), str(sum(votes) + nota)])
    builder = PdfBuilder()
    builder.add(Page(
        size=A4_LANDSCAPE,
        titles=["FORM 20 - FINAL RESULT SHEET", "32-Giridih, VS-2024"],
        table=Table(rows=rows, widths=[55, 72, 72, 62, 62, 62, 40, 48], font_size=7),
    ))
    path = tmp_path / "wide.pdf"
    path.write_bytes(builder.to_bytes())
    return path, rows


def test_every_cell_survives_the_round_trip(wide_table_pdf):
    path, rows = wide_table_pdf
    tables = read_tables(path)
    assert len(tables) == 1 and len(tables[0]) == 1
    got = tables[0][0]
    assert len(got) == len(rows)
    # Header cells may be wrapped onto several lines inside their cell, so they
    # come back with newlines; the data cells must match exactly.
    for got_row, want_row in zip(got[1:], rows[1:], strict=True):
        assert got_row == want_row


def test_a_wrapped_header_cell_does_not_bleed_into_its_neighbour(wide_table_pdf):
    path, rows = wide_table_pdf
    header = read_tables(path)[0][0][0]
    assert len(header) == len(rows[0])
    for got, want in zip(header, rows[0], strict=True):
        # The wrap may insert newlines, but no character of another cell may
        # appear: 'tSatuiodnivya Kumar' is what failure looks like here.
        assert got.replace("\n", " ").split() == want.split()


def test_multiple_pages_are_all_readable(tmp_path):
    builder = PdfBuilder()
    for page_no in range(3):
        rows = [["PS", "A", "B", "Total of Valid Votes"]]
        rows += [[str(page_no * 10 + i), "1", "2", "3"] for i in range(1, 5)]
        builder.add(Page(table=Table(rows=rows, widths=[40, 40, 40, 80])))
    path = tmp_path / "multi.pdf"
    path.write_bytes(builder.to_bytes())
    tables = read_tables(path)
    assert len(tables) == 3
    assert [t[0][1][0] for t in tables] == ["1", "11", "21"]


def test_flowed_text_pages_keep_their_lines_and_order(tmp_path):
    body = [f"{i}. ZZZ100000{i} Name: Ram Mahato House: {i} Age: 3{i} Gender: Male"
            for i in range(1, 9)]
    builder = PdfBuilder()
    builder.add(Page(size=A4_PORTRAIT,
                     titles=["ELECTORAL ROLL", "Polling Station No. 7   Part No. 7"],
                     body=body, body_size=7.5))
    path = tmp_path / "roll.pdf"
    path.write_bytes(builder.to_bytes())
    text = read_text(path)[0]
    assert "Polling Station No. 7" in text
    for line in body:
        assert line in text
    assert text.index(body[0]) < text.index(body[-1])


def test_a_character_outside_winansi_is_substituted_not_fatal(tmp_path):
    builder = PdfBuilder()
    builder.add(Page(body=["सुदिव्य कुमार"], titles=["Devanagari"]))
    path = tmp_path / "hi.pdf"
    # The writer has the base-14 fonts only. The contract is that it produces a
    # readable PDF with substitutions rather than raising, so a fixture never
    # fails to build over one character - ingest/mock_documents stays in Latin
    # script precisely because of this limit.
    path.write_bytes(builder.to_bytes())
    assert read_text(path)[0].startswith("Devanagari")


def test_an_empty_builder_refuses_rather_than_writing_a_broken_file():
    with pytest.raises(ValueError, match="at least one page"):
        PdfBuilder().to_bytes()


def test_parentheses_and_backslashes_in_a_cell_are_escaped(tmp_path):
    # Unescaped, these end the PDF string early and corrupt the content stream.
    label = "Sudivya Kumar (JMM) C:\\path"
    builder = PdfBuilder()
    builder.add(Page(table=Table(
        rows=[["PS", label, "Total of Valid Votes"], ["1", "2", "3"]],
        widths=[40, 220, 90])))
    path = tmp_path / "escape.pdf"
    path.write_bytes(builder.to_bytes())
    header = read_tables(path)[0][0][0]
    assert header[1].replace("\n", " ") == label
