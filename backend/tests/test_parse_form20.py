"""Form 20 parsing and the validation gate (LLD 4.2)."""

from ingest import parse_form20 as f20
from tests.conftest import FakePage

HEADER = [
    "Serial No. of Polling Station",
    "Sudivya Kumar (JMM)",
    "Nirbhay Kumar Shahabadi (BJP)",
    "Navin Anand (JLKM)",
    "Total of Valid Votes",
    "No. of Rejected votes",
    "NOTA",
    "Total",
    "No. of Tendered votes",
]

# candidates | valid | rejected | nota | total | tendered
DATA = [
    ["1", "250", "300", "40", "600", "0", "10", "600", "0"],
    ["2", "410", "395", "55", "870", "1", "10", "871", "0"],
    ["3", "180", "220", "30", "436", "0", "6", "436", "0"],
]


def _page(rows):
    return FakePage(page_no=1, tables=[[HEADER, *rows]])


def test_header_classification():
    cands, tail, ps_idx = f20.classify_header(HEADER)
    assert ps_idx == 0
    assert cands == [
        "Sudivya Kumar (JMM)",
        "Nirbhay Kumar Shahabadi (BJP)",
        "Navin Anand (JLKM)",
    ]
    assert tail == ["total_valid", "rejected", "nota", "total", "tendered"]


def test_hindi_header_classification():
    header = ["क्रम सं0", "सुदिव्य कुमार", "निर्भय कुमार", "कुल वैध मत", "अस्वीकृत मत", "नोटा", "कुल"]
    cands, tail, ps_idx = f20.classify_header(header)
    assert cands == ["सुदिव्य कुमार", "निर्भय कुमार"]
    assert tail == ["total_valid", "rejected", "nota", "total"]


def test_parse_from_tables():
    doc = f20.parse_from_tables([_page(DATA)], "form20_vs2024.pdf")
    assert doc is not None
    assert doc.method == "table"
    assert len(doc.rows) == 3
    r0 = doc.rows[0]
    assert r0.ps_number == 1
    assert r0.votes == [250, 300, 40]
    assert r0.total_valid == 600 and r0.nota == 10 and r0.rejected == 0
    assert doc.candidate_totals() == [840, 915, 125]
    assert doc.nota_total() == 26


def test_validation_passes_on_consistent_rows():
    doc = f20.parse_from_tables([_page(DATA)], "form20_vs2024.pdf")
    assert f20.validate(doc) == []


def test_validation_catches_row_arithmetic():
    bad = [row[:] for row in DATA]
    bad[1][4] = "999"                       # printed valid total no longer matches
    doc = f20.parse_from_tables([_page(bad)], "form20_vs2024.pdf")
    errors = f20.validate(doc)
    assert len(errors) == 1
    assert "PS 2" in errors[0].detail
    assert errors[0].payload["computed"] == 870
    assert errors[0].payload["printed"] == 999


def test_validation_catches_duplicate_and_missing_ps():
    rows = [DATA[0], DATA[0], DATA[2]]      # PS 1 twice, PS 2 never
    doc = f20.parse_from_tables([_page(rows)], "form20_vs2024.pdf")
    details = " ".join(e.detail for e in f20.validate(doc))
    assert "appears twice" in details
    assert "missing" in details


def test_validation_catches_ac_total_mismatch():
    doc = f20.parse_from_tables([_page(DATA)], "form20_vs2024.pdf")
    published = {"Sudivya Kumar (JMM)": 841, "NOTA": 26}
    errors = f20.validate(doc, published)
    assert len(errors) == 1
    assert "booth sum 840" in errors[0].detail
    assert "-1" in errors[0].detail


def test_regex_path_for_ocr_pages():
    text = "\n".join([
        "1 250 300 40 600 0 10 600",
        "2 410 395 55 870 1 10 871",
        "some heading that is not a row",
        "३ १८० २२० ३० ४३६ ० ६ ४३६",      # Devanagari digits
    ])
    doc = f20.parse_from_text(
        [FakePage(page_no=7, text=text)],
        "form20_scanned.pdf",
        ["Sudivya Kumar (JMM)", "Nirbhay Kumar Shahabadi (BJP)", "Navin Anand (JLKM)"],
    )
    assert [r.ps_number for r in doc.rows] == [1, 2, 3]
    assert doc.rows[2].votes == [180, 220, 30]
    assert doc.rows[2].nota == 6
    assert all(r.page_no == 7 for r in doc.rows)


def test_candidate_label_split():
    assert f20.split_candidate_label("Sudivya Kumar (JMM)") == ("Sudivya Kumar", "JMM")
    assert f20.split_candidate_label("Some Independent") == ("Some Independent", None)
