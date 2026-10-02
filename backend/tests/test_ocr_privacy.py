"""A low-confidence OCR page of an electoral roll must not reach the database.

`ocr_pages` queues a low-confidence page for review with an `excerpt` of its
text. For a roll that text is names, relations, EPIC numbers and addresses -
and parse_roll called extract_document(cache=False), which kept roll text off
disk but still sent scanned pages to OCR, so the excerpt was written to
review_queue.payload and served by the admin API (LLD 12).
"""

from __future__ import annotations

import json

import pytest

from ingest import ocr_tesseract

ROLL_TEXT = "Name : Ramesh Kumar  Father's Name : Suresh  ABC1234567  House No 12"


@pytest.fixture
def queued(monkeypatch, tmp_path):
    writes: list = []
    import common.db

    monkeypatch.setattr(common.db, "execute", lambda sql, params=(): writes.append((sql, params)))
    monkeypatch.setattr(ocr_tesseract, "rasterise",
                        lambda pdf, page, out: (out / f"p{page}.png").touch() or out / f"p{page}.png")
    monkeypatch.setattr(ocr_tesseract, "ocr_image", lambda png, *a, **k: (ROLL_TEXT, 12.0))
    return writes


def test_a_personal_document_queues_no_excerpt(queued, tmp_path):
    pdf = tmp_path / "roll.pdf"
    pdf.write_bytes(b"%PDF")
    ocr_tesseract.ocr_pages(pdf, [1], personal=True)

    assert queued, "the page is still queued for review - just without its text"
    payload = json.loads(queued[0][1][-1] if isinstance(queued[0][1][-1], str) else queued[0][1][1])
    assert "excerpt" not in payload
    assert "ABC1234567" not in json.dumps(queued)
    assert "Ramesh" not in json.dumps(queued)


def test_a_public_document_keeps_its_excerpt_for_the_reviewer(queued, tmp_path):
    pdf = tmp_path / "form20.pdf"
    pdf.write_bytes(b"%PDF")
    ocr_tesseract.ocr_pages(pdf, [1])
    assert "excerpt" in json.dumps(queued)


@pytest.mark.parametrize("cache, personal", [(False, True), (True, False)])
def test_extract_document_passes_cache_false_on_as_personal(monkeypatch, tmp_path, cache, personal):
    """The roll loader's existing signal, cache=False, is what selects it."""
    from pypdf import PdfWriter

    from ingest import extract_pdf

    pdf = tmp_path / "blank.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)   # no text layer -> OCR
    with pdf.open("wb") as fh:
        writer.write(fh)

    seen = {}

    def fake_ocr_pages(path, pages, personal=False):
        seen["personal"] = personal
        return []

    monkeypatch.setattr(ocr_tesseract, "ocr_pages", fake_ocr_pages)
    monkeypatch.setenv("OCR_DIR", str(tmp_path / "ocr"))
    from common.config import get_settings
    get_settings.cache_clear()
    extract_pdf.extract_document(pdf, cache=cache, tables=False, force=True)
    get_settings.cache_clear()
    assert seen == {"personal": personal}
