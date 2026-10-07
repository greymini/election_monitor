#!/usr/bin/env python
"""Convert a CEO Jharkhand Form 20 PDF into the workbook `ingest.form20_tables` reads.

    python scripts/form20_pdf_to_xlsx.py <form20.pdf> <out.xlsx>
    python scripts/form20_pdf_to_xlsx.py <pc_form20.pdf> <out.xlsx> --pc-ac 32 --names <columns.csv>

CEO Jharkhand publishes Form 20 (booth-wise results) as text PDFs, one AC per
file, for example:

    VS-2024  https://ceo.jharkhand.gov.in/Form20_GLVS2024/Form20/<ac>.pdf
    LS-2024  https://ceo.jharkhand.gov.in/AllForm20LS2024/<ac>.pdf

Each page's table is written to its own worksheet, unchanged: the "Serial ..."
header row, the candidate-name row, one row per polling station and, on the
last page, the "Total EVM Votes" / "Total Postal Ballot Votes" / "Total Votes
Polled" footers. Line breaks inside a cell (candidate names wrap) become spaces.

Two printing artefacts are repaired, and `form20_tables.problems()` then checks
the arithmetic of every row, so a wrong repair cannot load:

* The "Page N" stamp sometimes lands on the last station of a page: serial
  "P2a9g", PS "e2 91" is station 29 on page 1 (see `_repair_stamp`).
* A Lok Sabha Form 20 prints "(To be filled in the case of election from an
  assembly constituency.)" in its postal row: postal ballots are counted for
  the whole PC, not per segment, and its "Total Votes Polled" row equals the
  EVM row. That row is written as zeros, which is what the sheet's own totals
  say.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


def _cell(value):
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def _repair_stamp(row: list, page: int, n_candidates: int | None) -> list:
    """Undo the "Page N" stamp printed over a station's first cells.

    The letters of "Page" land in the serial and PS cells and the page number
    is split between the end of the PS cell and the start of the first
    candidate's votes ("P20a0", "g20e0", "526" is station 200, 26 votes, on
    page 5). Every split of the page number is tried; the one kept is the only
    one under which the candidates add up to the printed valid votes.
    """
    first = row[0] or ""
    if "P" not in first or not re.fullmatch(r"[Page\d ]+", first):
        return row
    serial = re.sub(r"\D", "", first)
    ps = re.sub(r"\D", "", row[1] or "")
    vote = re.sub(r"\D", "", row[2] or "")
    if not serial or n_candidates is None:
        return row
    try:
        rest = [int(c) for c in row[3:2 + n_candidates]]
        valid = int(row[2 + n_candidates])
    except (TypeError, ValueError, IndexError):
        return row
    page_no = str(page)
    fits = []
    for cut in range(len(page_no) + 1):
        head, tail = page_no[:cut], page_no[cut:]
        if ps == serial + head and vote.startswith(tail) and vote[len(tail):]:
            v = int(vote[len(tail):])
            if v + sum(rest) == valid:
                fits.append(str(v))
    if len(fits) == 1:
        return [serial, serial, fits[0], *row[3:]]
    return row


def _candidate_count(rows: list) -> int | None:
    """Candidates = columns of the name row, minus serial/PS and the five tail columns."""
    for i, row in enumerate(rows):
        if (row[0] or "").lower().startswith("serial") and i + 1 < len(rows):
            return sum(1 for c in rows[i + 1][2:] if c is not None)
    return None


def _postal_note(row: list) -> list:
    label = (row[0] or "").lower()
    rest = [c for c in row[1:] if c is not None]
    if label.startswith("total postal") and not any(re.fullmatch(r"\d+", c) for c in rest):
        return None
    return row


def convert_pc(pdf: Path, out: Path, ac: int, names_csv: Path) -> int:
    """One assembly segment out of a PC-wide Form 20 (the LS-2019 layout).

    That layout prints one row per station for the whole PC: AC number, AC
    name, station, the candidates, NOTA and total - no rejected or tendered
    columns, no per-segment footer, and candidate names printed vertically
    (garbled by extraction). The names come from `names_csv`, in column order.
    Rejected and tendered are written as 0 (not printed). The footers are the
    column sums, so the check that remains is each row's own arithmetic:
    candidates + NOTA = total.
    """
    import csv

    import pdfplumber
    from openpyxl import Workbook

    with names_csv.open(encoding="utf-8") as fh:
        names = [r["candidate"] for r in sorted(csv.DictReader(fh), key=lambda r: int(r["column"]))]
    n = len(names)
    stations = {}
    with pdfplumber.open(pdf) as doc:
        for page in doc.pages:
            for table in page.extract_tables():
                for row in table:
                    if row and (row[0] or "").strip() == str(ac):
                        cells = [int(c) for c in row[2:]]
                        if len(cells) != n + 3:
                            raise ValueError(f"PS row {row[:3]}: {len(cells)} cells, expected {n + 3}")
                        stations[cells[0]] = cells[1:]
    wb = Workbook()
    ws = wb.active
    ws.title = "Page_1"
    ws.append(["Serial No. Of Polling Station", None, "No of Valid Votes Cast in favour of"])
    ws.append([None, None, *names])
    sums = [0] * (n + 5)
    for ps in sorted(stations):
        votes, nota, total = stations[ps][:n], stations[ps][n], stations[ps][n + 1]
        tail = [sum(votes), 0, nota, total, 0]
        ws.append([ps, ps, *votes, *tail])
        sums = [a + b for a, b in zip(sums, [*votes, *tail], strict=True)]
    ws.append(["Total EVM Votes", None, *sums])
    ws.append(["Total Postal Ballot Votes", None, *[0] * len(sums)])
    ws.append(["Total Votes Polled", None, *sums])
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    return len(stations)


def convert(pdf: Path, out: Path) -> int:
    import pdfplumber
    from openpyxl import Workbook

    wb = Workbook()
    wb.remove(wb.active)
    pages = 0
    n_candidates = None
    with pdfplumber.open(pdf) as doc:
        for i, page in enumerate(doc.pages, 1):
            rows = [[_cell(c) for c in row]
                    for table in page.extract_tables() for row in table]
            rows = [r for r in rows if any(c is not None for c in r)]
            n_candidates = _candidate_count(rows) or n_candidates
            rows = [_repair_stamp(r, i, n_candidates) for r in rows]
            if not rows:
                continue
            ws = wb.create_sheet(f"Page_{i}")
            for row in rows:
                if _postal_note(row) is None:
                    polled = next((r for r in rows if (r[0] or "").lower() == "total votes polled"), None)
                    width = sum(1 for c in (polled or [])[1:] if c is not None)
                    row = [row[0], None, *["0"] * width]
                ws.append(row)
            pages += 1
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    return pages


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Form 20 PDF -> workbook for ingest.form20_tables")
    ap.add_argument("pdf", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--pc-ac", type=int, help="take this assembly segment out of a PC-wide Form 20")
    ap.add_argument("--names", type=Path, help="candidate names in column order (with --pc-ac)")
    args = ap.parse_args(argv)
    if args.pc_ac:
        if not args.names:
            ap.error("--pc-ac needs --names")
        print(f"{convert_pc(args.pdf, args.out, args.pc_ac, args.names)} stations -> {args.out}")
    else:
        print(f"{convert(args.pdf, args.out)} pages -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
