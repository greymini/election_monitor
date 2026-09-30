"""C3: no roll page text may reach disk.

The audit's point about the existing privacy test was precise and worth
restating, because it is the reason this file exists: `test_roll_privacy.py`
"tests the parser functions in isolation and never touches the cache", so it
passed the whole time the full electoral roll was sitting in plaintext JSON
under `ocr/`. A test that exercises the pure functions and not the pipeline
proves the design and says nothing about the system.

So these tests drive `scan_pdf` - the actual entry point `parse_roll` uses -
against a fake PDF whose pages contain realistic roll text, and then look at the
filesystem. The audit's own suggestion, item 5 of its ranked list of missing
tests: "extend test_roll_privacy.py to run scan_pdf against a temp directory and
assert that no file written under OCR_DIR matches the EPIC regex or contains a
fixture name".
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass

import pytest

from common.config import get_settings
from common.pii import AADHAAR, EPIC, MOBILE, PiiRejected, scan_text, screen

# Realistic roll text: the shape a mother-roll page actually has, with the
# things that must never be persisted. Names here are invented.
ROLL_PAGE = """
भाग संख्या 12  मतदान केंद्र संख्या 12  प्राथमिक विद्यालय चतरो
क्रम सं  नाम                   पिता/पति का नाम      मकान सं  आयु  लिंग  पहचान पत्र
1        रामप्रसाद महतो         श्यामलाल महतो        14/2     43    पुरुष  ABC1234567
2        सीता देवी             रामप्रसाद महतो        14/2     39    महिला  ABC1234568
3        Imran Ansari           Rashid Ansari         21       27    पुरुष  XYZ9876543
मोबाइल 9812345678
"""


@dataclass
class FakePage:
    page_no: int
    text: str
    source: str = "text_layer"
    ocr_confidence: float | None = None
    char_count: int = 0
    tables: list | None = None

    def to_dict(self) -> dict:
        return {
            "page_no": self.page_no, "source": self.source,
            "ocr_confidence": self.ocr_confidence, "char_count": self.char_count,
            "text": self.text, "tables": self.tables or [],
        }


@pytest.fixture(autouse=True)
def _isolated_dirs(monkeypatch, tmp_path):
    """Point OCR_DIR and RAW_DIR at a temp tree, so a leak is visible and
    contained."""
    monkeypatch.setenv("OCR_DIR", str(tmp_path / "ocr"))
    monkeypatch.setenv("RAW_DIR", str(tmp_path / "raw"))
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


def _fake_extract(monkeypatch, recorder: list[dict]):
    """Replace extract_document, recording how it was called.

    The real one needs pdfplumber and a genuine PDF; what these tests are about
    is whether the roll path asks it to cache, and whether anything lands on
    disk - so the substitute writes the cache exactly as the real one would when
    told to.
    """
    from ingest import extract_pdf

    def fake(pdf_path, force=False, do_ocr=True, max_pages=None, cache=True):
        recorder.append({"path": pdf_path, "cache": cache, "force": force})
        pages = [FakePage(1, ROLL_PAGE), FakePage(2, ROLL_PAGE.replace("12", "13"))]
        if cache:
            # Reproduce the leak faithfully, so a test that expects no files on
            # disk is actually testing something.
            out = extract_pdf.cache_dir(pdf_path, "deadbeefcafe")
            out.mkdir(parents=True, exist_ok=True)
            for page in pages:
                import json

                (out / f"page_{page.page_no:04d}.json").write_text(
                    json.dumps(page.to_dict(), ensure_ascii=False), encoding="utf-8")
        return pages

    monkeypatch.setattr(extract_pdf, "extract_document", fake)
    return recorder


# --------------------------------------------------------------------------
# The fixture itself must contain what we claim it does
# --------------------------------------------------------------------------


def test_the_fixture_page_contains_the_patterns_being_guarded():
    """Otherwise every assertion below passes for the wrong reason."""
    assert EPIC.search(ROLL_PAGE), "the fixture needs an EPIC number to be meaningful"
    assert MOBILE.search(ROLL_PAGE), "the fixture needs a phone number"
    found = {f.pattern for f in scan_text(ROLL_PAGE, "fixture")}
    assert "epic" in found and "mobile" in found


# --------------------------------------------------------------------------
# The leak, and its absence
# --------------------------------------------------------------------------


def test_scan_pdf_asks_for_no_cache(monkeypatch, tmp_path):
    """The mechanism. If this flag is ever dropped, the leak returns."""
    calls: list[dict] = []
    _fake_extract(monkeypatch, calls)
    from ingest.parse_roll import scan_pdf

    pdf = tmp_path / "roll.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    scan_pdf(pdf)

    assert calls, "scan_pdf did not call extract_document"
    assert calls[0]["cache"] is False, (
        "parse_roll must pass cache=False, or every page of the roll - names, "
        "EPIC numbers, relatives' names, house numbers, ages - is written to "
        "OCR_DIR as plaintext JSON and kept indefinitely (C3)"
    )


def test_nothing_lands_under_ocr_dir_when_a_roll_is_scanned(monkeypatch, tmp_path):
    """The outcome, checked on the filesystem rather than inferred from a flag."""
    _fake_extract(monkeypatch, [])
    from ingest.parse_roll import scan_pdf

    pdf = tmp_path / "roll.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    scan_pdf(pdf)

    ocr_dir = pathlib.Path(get_settings().ocr_dir)
    written = [p for p in ocr_dir.rglob("*") if p.is_file()] if ocr_dir.exists() else []
    assert written == [], f"roll parsing wrote {len(written)} file(s) to {ocr_dir}"


def test_no_file_on_disk_matches_a_personal_data_pattern(monkeypatch, tmp_path):
    """The audit's own wording: assert that no file written under OCR_DIR
    matches the EPIC regex or contains a fixture name."""
    _fake_extract(monkeypatch, [])
    from ingest.parse_roll import scan_pdf

    pdf = tmp_path / "roll.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    scan_pdf(pdf)

    from ingest.validate import check_privacy_filesystem

    checks = check_privacy_filesystem()
    assert all(c.passed for c in checks), [c.detail for c in checks]

    # And explicitly, by name as well as by pattern.
    for path in tmp_path.rglob("*.json"):
        text = path.read_text(encoding="utf-8", errors="replace")
        assert "रामप्रसाद" not in text
        assert not EPIC.search(text)


def test_the_scan_would_catch_the_leak_if_it_came_back(monkeypatch, tmp_path):
    """A negative control. Without it, the three tests above could pass because
    the scanner is broken rather than because the disk is clean."""
    ocr_dir = pathlib.Path(get_settings().ocr_dir)
    leaked = ocr_dir / "roll-deadbeefcafe"
    leaked.mkdir(parents=True)
    (leaked / "page_0001.json").write_text(ROLL_PAGE, encoding="utf-8")

    from ingest.validate import check_privacy_filesystem

    checks = check_privacy_filesystem()
    assert not all(c.passed for c in checks), (
        "the filesystem scan did not notice a roll page sitting in OCR_DIR"
    )
    detail = " ".join(c.detail for c in checks)
    assert "purge_roll_cache" in detail, "the failure must name the fix"
    # The report must not quote what it found.
    joined = " ".join(str(r) for c in checks for r in (c.rows or []))
    assert "रामप्रसाद" not in joined
    assert "ABC1234567" not in joined


def test_the_purge_script_finds_and_removes_a_leaked_cache(tmp_path, monkeypatch):
    ocr_dir = pathlib.Path(get_settings().ocr_dir)
    leaked = ocr_dir / "roll-deadbeefcafe"
    leaked.mkdir(parents=True)
    (leaked / "page_0001.json").write_text(ROLL_PAGE, encoding="utf-8")
    # A Form 20 cache, which must survive: it holds vote counts, not electors.
    keep = ocr_dir / "form20-abc123"
    keep.mkdir(parents=True)
    (keep / "page_0001.json").write_text('{"text": "1 245 189 12 446"}', encoding="utf-8")

    from scripts import purge_roll_cache

    # Report-only exits non-zero on a find, so a preflight treats it as failure.
    assert purge_roll_cache.main([]) == 1
    assert leaked.exists(), "a report-only run must not delete anything"

    assert purge_roll_cache.main(["--delete"]) == 0
    assert not leaked.exists()
    assert keep.exists(), "the Form 20 cache must survive; it holds no personal data"


def test_raw_pdfs_are_not_touched_by_the_purge(tmp_path):
    """raw/ is the audit trail. The guarantee is that nothing *derived* from a
    roll is kept, not that the original does not exist."""
    raw = pathlib.Path(get_settings().raw_dir) / "roll_mother" / "32"
    raw.mkdir(parents=True)
    pdf = raw / "roll.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake roll")

    from scripts import purge_roll_cache

    purge_roll_cache.main(["--delete"])
    assert pdf.exists()


# --------------------------------------------------------------------------
# Retention: the other half of C3
# --------------------------------------------------------------------------


def test_the_raw_roll_is_retained_by_default(monkeypatch, tmp_path):
    """C13. The old default deleted the source PDF while the page cache kept its
    full text - destroying the evidence and retaining the personal data."""
    monkeypatch.delenv("RETAIN_RAW_ROLLS", raising=False)
    get_settings.cache_clear()
    assert get_settings().retain_raw_rolls is True

    from ingest.parse_roll import discard_raw

    pdf = tmp_path / "roll.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    discard_raw(pdf)
    assert pdf.exists(), "the auditable original must be kept by default"


def test_an_operator_can_still_delete_it_deliberately(monkeypatch, tmp_path):
    monkeypatch.setenv("RETAIN_RAW_ROLLS", "false")
    get_settings.cache_clear()

    from ingest.parse_roll import discard_raw

    pdf = tmp_path / "roll.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    discard_raw(pdf)
    assert not pdf.exists()


# --------------------------------------------------------------------------
# The free-text screen (E5), which shares the patterns
# --------------------------------------------------------------------------


@pytest.mark.parametrize(("text", "expected"), [
    ("Queue of 30 at the school gate, no water in the tank", None),
    ("Call ABC1234567 about the roll", "epic"),
    ("Reach me on 9812345678", "mobile"),
    ("Aadhaar 1234 5678 9012 attached", "aadhaar"),
])
def test_the_free_text_screen_rejects_each_pattern_class(text, expected):
    if expected is None:
        screen(text, field="report")
        return
    with pytest.raises(PiiRejected) as excinfo:
        screen(text, field="report")
    assert expected in str(excinfo.value)


def test_the_rejection_never_echoes_the_matched_text():
    """A message that quotes the personal data has moved the leak into the log,
    the scrollback and whatever ticket it gets pasted into."""
    with pytest.raises(PiiRejected) as excinfo:
        screen("Call ABC1234567 and 9812345678", field="report")
    message = str(excinfo.value)
    assert "ABC1234567" not in message
    assert "9812345678" not in message
    # And it is in both languages, because the sender is a booth in-charge.
    assert "EPIC" in message and "आधार" in message


def test_a_vote_count_is_not_mistaken_for_a_phone_number():
    """The lookarounds earning their keep. Without them the mobile pattern fires
    inside any longer digit run, and this system is full of them - vote counts,
    elector totals, sha256 prefixes."""
    for benign in ("94042", "304898", "207598", "3f9a2c1e88b4",
                   "Booth 12 recorded 9812345678901 which is 13 digits"):
        assert not scan_text(benign, "x") or all(
            f.pattern != "mobile" for f in scan_text(benign, "x")
        ), f"{benign!r} was flagged as a phone number"


def test_aadhaar_matches_spaced_and_unspaced():
    assert AADHAAR.search("123456789012")
    assert AADHAAR.search("1234 5678 9012")
    # But not a longer run, which is how a vote total would look.
    assert not AADHAAR.search("1234567890123")
