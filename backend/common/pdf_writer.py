"""A minimal PDF writer, for generating test and development source documents.

Why this exists. The ingestion pipeline's entry point is a PDF: Form 20, the
polling-station list and the electoral roll all arrive as one. Until now nothing
in this repo could produce one, so every test of the pipeline either stopped at
a parsed-dictionary boundary or inserted rows directly - which is the shortcut
that let C1 (columns never resolved to a party) survive a green test suite.
`scripts/dev_stack.py` loads its dataset through the real loaders, and that
needs real PDFs.

Scope, deliberately small:

  * Helvetica only, WinAnsi, so **no Devanagari**. Every mock document is in
    Latin script. `ingest/resolve.py` and `ingest/parse_roll.py` both accept
    either script, so the Latin path is a real path - but a mock document can
    never exercise the Devanagari-specific branches, and `ingest/mock_documents`
    says so where it matters.
  * Ruled tables (what `extract_pdf._extract_tables` reads with pdfplumber's
    `lines` strategy) and flowed text pages (what `parse_roll` reads).
  * No compression, no encryption, no images, no outlines.

It is not a general PDF library and must not grow into one. If a fixture needs
something this cannot do, the fixture is probably too clever.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field

# Helvetica advance widths, 1/1000 em, for the printable ASCII range - the Adobe
# AFM metrics that pdfminer ships. Inlined rather than imported so this module
# stays dependency-free: common/ is imported by the API image, which does not
# install pdfminer.
HELVETICA_WIDTHS: dict[str, int] = {
    ' ': 278, '!': 278, '"': 355, '#': 556, '$': 556, '%': 889, '&': 667, "'": 191,
    '(': 333, ')': 333, '*': 389, '+': 584, ',': 278, '-': 333, '.': 278, '/': 278,
    '0': 556, '1': 556, '2': 556, '3': 556, '4': 556, '5': 556, '6': 556, '7': 556,
    '8': 556, '9': 556, ':': 278, ';': 278, '<': 584, '=': 584, '>': 584, '?': 556,
    '@': 1015, 'A': 667, 'B': 667, 'C': 722, 'D': 722, 'E': 667, 'F': 611, 'G': 778,
    'H': 722, 'I': 278, 'J': 500, 'K': 667, 'L': 556, 'M': 833, 'N': 722, 'O': 778,
    'P': 667, 'Q': 778, 'R': 722, 'S': 667, 'T': 611, 'U': 722, 'V': 667, 'W': 944,
    'X': 667, 'Y': 667, 'Z': 611, '[': 278, '\\': 278, ']': 278, '^': 469, '_': 556,
    '`': 333, 'a': 556, 'b': 556, 'c': 500, 'd': 556, 'e': 556, 'f': 278, 'g': 556,
    'h': 556, 'i': 222, 'j': 222, 'k': 500, 'l': 222, 'm': 833, 'n': 556, 'o': 556,
    'p': 556, 'q': 556, 'r': 333, 's': 500, 't': 278, 'u': 556, 'v': 500, 'w': 722,
    'x': 500, 'y': 500, 'z': 500, '{': 334, '|': 260, '}': 334, '~': 584,
}
FALLBACK_WIDTH = 556

A4_LANDSCAPE = (842.0, 595.0)
A4_PORTRAIT = (595.0, 842.0)


def text_width(text: str, font_size: float) -> float:
    """Advance width of `text` in points at `font_size`."""
    total = sum(HELVETICA_WIDTHS.get(ch, FALLBACK_WIDTH) for ch in text)
    return total * font_size / 1000.0


def wrap(text: str, max_width: float, font_size: float) -> list[str]:
    """Greedy word wrap to `max_width` points.

    A single word longer than the line is broken by character rather than left
    to overflow. An overflowing header cell is how the first draft of the mock
    Form 20 produced 'tSatuiodnivya Kumar' out of pdfplumber - two header cells
    interleaved, which looks exactly like an OCR failure and would send someone
    hunting in the wrong place.
    """
    words = str(text).split()
    if not words:
        return [""]
    lines: list[str] = []
    current = ""
    for word in words:
        trial = f"{current} {word}".strip()
        if current and text_width(trial, font_size) > max_width:
            lines.append(current)
            current = word
        else:
            current = trial
        while text_width(current, font_size) > max_width and len(current) > 1:
            cut = len(current)
            while cut > 1 and text_width(current[:cut], font_size) > max_width:
                cut -= 1
            lines.append(current[:cut])
            current = current[cut:]
    if current:
        lines.append(current)
    return lines


def _escape(text: str) -> str:
    return str(text).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _encode(text: str) -> bytes:
    """To WinAnsi bytes, substituting anything outside it rather than failing.

    A mock document is not the place to discover that a name has a character the
    base-14 fonts cannot show; the substitution is visible in the output.
    """
    return _escape(text).encode("cp1252", errors="replace")


@dataclass
class Table:
    """A ruled table: `rows[r][c]` strings, `widths` in points per column."""

    rows: list[list[str]]
    widths: list[float]
    font_size: float = 7.0
    line_height: float = 1.25
    cell_pad: float = 2.0

    def row_heights(self) -> list[float]:
        heights = []
        for row in self.rows:
            lines = 1
            for cell, width in zip(row, self.widths, strict=False):
                lines = max(lines, len(wrap(cell, width - 2 * self.cell_pad, self.font_size)))
            heights.append(lines * self.font_size * self.line_height + 2 * self.cell_pad)
        return heights

    def height(self) -> float:
        return sum(self.row_heights())


@dataclass
class Page:
    size: tuple[float, float] = A4_LANDSCAPE
    margin: float = 28.0
    titles: list[str] = field(default_factory=list)
    title_size: float = 9.0
    table: Table | None = None
    body: list[str] = field(default_factory=list)
    body_size: float = 8.0


class PdfBuilder:
    """Accumulate pages, then `to_bytes()`."""

    def __init__(self) -> None:
        self.pages: list[Page] = []

    def add(self, page: Page) -> None:
        self.pages.append(page)

    # -- content streams ---------------------------------------------------

    def _stream(self, page: Page) -> bytes:
        width, height = page.size
        ops: list[bytes] = []
        y = height - page.margin

        if page.titles:
            ops.append(b"BT")
            ops.append(f"/F1 {page.title_size:.2f} Tf".encode())
            for title in page.titles:
                y -= page.title_size * 1.4
                ops.append(b"1 0 0 1 " + f"{page.margin:.2f} {y:.2f} Tm".encode()
                           + b" (" + _encode(title) + b") Tj")
            ops.append(b"ET")
            y -= page.title_size

        if page.table is not None:
            ops += self._table_ops(page, y)
            return b"\n".join(ops)

        if page.body:
            ops.append(b"BT")
            ops.append(f"/F1 {page.body_size:.2f} Tf".encode())
            usable = width - 2 * page.margin
            for raw_line in page.body:
                pieces = wrap(raw_line, usable, page.body_size) if raw_line.strip() else [""]
                for line in pieces:
                    y -= page.body_size * 1.35
                    if line:
                        ops.append(b"1 0 0 1 " + f"{page.margin:.2f} {y:.2f} Tm".encode()
                                   + b" (" + _encode(line) + b") Tj")
            ops.append(b"ET")
        return b"\n".join(ops)

    def _table_ops(self, page: Page, top: float) -> list[bytes]:
        table = page.table
        assert table is not None
        xs = [page.margin]
        for w in table.widths:
            xs.append(xs[-1] + w)
        heights = table.row_heights()
        bottom = top - sum(heights)

        ops: list[bytes] = [b"0.5 w", b"0 G"]
        # Vertical rules, then horizontal ones. pdfplumber's `lines` strategy
        # needs both to find cell boundaries.
        for x in xs:
            ops.append(f"{x:.2f} {bottom:.2f} m {x:.2f} {top:.2f} l S".encode())
        y = top
        ops.append(f"{xs[0]:.2f} {y:.2f} m {xs[-1]:.2f} {y:.2f} l S".encode())
        for h in heights:
            y -= h
            ops.append(f"{xs[0]:.2f} {y:.2f} m {xs[-1]:.2f} {y:.2f} l S".encode())

        ops.append(b"BT")
        ops.append(f"/F1 {table.font_size:.2f} Tf".encode())
        y = top
        for row, height in zip(table.rows, heights, strict=True):
            for index, cell in enumerate(row):
                if index >= len(table.widths):
                    break
                cell_width = table.widths[index] - 2 * table.cell_pad
                ty = y - table.cell_pad - table.font_size
                for line in wrap(cell, cell_width, table.font_size):
                    if line:
                        tx = xs[index] + table.cell_pad
                        ops.append(b"1 0 0 1 " + f"{tx:.2f} {ty:.2f} Tm".encode()
                                   + b" (" + _encode(line) + b") Tj")
                    ty -= table.font_size * table.line_height
            y -= height
        ops.append(b"ET")
        return ops

    # -- file assembly -----------------------------------------------------

    def to_bytes(self) -> bytes:
        if not self.pages:
            raise ValueError("a PDF needs at least one page")

        streams = [self._stream(p) for p in self.pages]
        n_pages = len(self.pages)
        # 1 catalog, 2 pages, 3 font, then (page, content) per page.
        first_page_obj = 4
        kids = " ".join(f"{first_page_obj + 2 * i} 0 R" for i in range(n_pages))

        objects: list[bytes] = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>".encode(),
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
            b"/Encoding /WinAnsiEncoding >>",
        ]
        for i, (page, stream) in enumerate(zip(self.pages, streams, strict=True)):
            w, h = page.size
            objects.append(
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {w:.0f} {h:.0f}] "
                f"/Resources << /Font << /F1 3 0 R >> >> "
                f"/Contents {first_page_obj + 2 * i + 1} 0 R >>".encode()
            )
            objects.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n"
                           + stream + b"\nendstream")

        buf = io.BytesIO()
        buf.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets: list[int] = []
        for number, body in enumerate(objects, start=1):
            offsets.append(buf.tell())
            buf.write(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
        xref = buf.tell()
        buf.write(f"xref\n0 {len(objects) + 1}\n".encode())
        buf.write(b"0000000000 65535 f \n")
        for offset in offsets:
            buf.write(f"{offset:010d} 00000 n \n".encode())
        buf.write(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
                  f"startxref\n{xref}\n%%EOF\n".encode())
        return buf.getvalue()
