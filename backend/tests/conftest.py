"""Test fixtures. These tests never touch a database - everything under test
here is pure parsing, matching and scoring logic."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@dataclass
class FakePage:
    """Stands in for ingest.extract_pdf.PageText without needing pdfplumber."""

    page_no: int
    text: str = ""
    tables: list[list[list[str]]] = field(default_factory=list)
    source: str = "text_layer"
    ocr_confidence: float | None = None
    char_count: int = 0
