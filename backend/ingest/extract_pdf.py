"""Stage 1 of the PDF pipeline (LLD 4.1): pdfplumber text layer, OCR fallback.

Recent CEO Jharkhand PDFs carry a real text layer, so fewer than about 10% of
pages should ever reach Tesseract. Pages are cached as text files next to the
source so a re-parse never re-OCRs.

    python -m ingest.extract_pdf raw/form20_vs2024.pdf
    python -m ingest.extract_pdf --all --kind form20
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

from common.config import get_settings
from common.jobs import job_context, sha256_file
from common.logging_setup import get_logger
from common.textnorm import normalize_block, normalize_text

log = get_logger(__name__)


@dataclass
class PageText:
    page_no: int                      # 1-based
    text: str
    source: str                       # 'text_layer' | 'ocr' | 'empty'
    ocr_confidence: float | None = None
    char_count: int = 0
    tables: list[list[list[str]]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "page_no": self.page_no,
            "source": self.source,
            "ocr_confidence": self.ocr_confidence,
            "char_count": self.char_count,
            "text": self.text,
            "tables": self.tables,
        }


def cache_dir(pdf_path: Path, digest: str | None = None) -> Path:
    s = get_settings()
    digest = digest or sha256_file(pdf_path)
    return Path(s.ocr_dir) / f"{pdf_path.stem}-{digest[:12]}"


def extract_document(
    pdf_path: Path,
    force: bool = False,
    do_ocr: bool = True,
    max_pages: int | None = None,
    cache: bool = True,
    tables: bool = True,
) -> list[PageText]:
    """Extract every page. Uses the on-disk cache unless force=True.

    `cache=False` keeps every page in memory and writes nothing. Roll parsing
    passes it, and audit finding C3 is why.

    `tables=False` skips the lattice table pass. An electoral roll is flowed
    text with no ruling lines, so the pass finds nothing and costs about as much
    as the text extraction itself - roughly half the runtime of a roll load, on
    a document that can run to several hundred pages. `parse_roll` reads
    `page.text` only, so there is nothing to lose. Form 20 and the PS list leave
    it on: for them the ruled table is the preferred extraction path.

    This function wrote each page's complete extracted text to
    `OCR_DIR/<stem>-<sha12>/page_NNNN.json` before any parser saw it, and
    `parse_roll.scan_pdf` routed roll PDFs through it like any other document.
    So the full electoral roll - every elector's name, EPIC number, father's or
    husband's name, house number and age - was persisted as plaintext JSON and
    kept indefinitely. `discard_raw()` then deleted the source PDF, because
    RETAIN_RAW_ROLLS defaulted false: the system destroyed the auditable
    original and kept the personal data.

    That contradicted README's "the names, EPIC numbers and addresses are
    discarded in memory", LLD 12's "no individual voter records anywhere in DB,
    logs, or LLM prompts", and the DPDP-Act reasoning the whole design rests on.
    The existing privacy test passed throughout, because it exercised the parser
    functions in isolation and never touched the cache.

    The cache is a performance convenience for re-parsing a Form 20. It is not
    worth holding a roll on disk for, so the roll path does without it.
    """
    import pdfplumber

    settings = get_settings()
    digest = sha256_file(pdf_path)
    out_dir = cache_dir(pdf_path, digest)
    if cache:
        out_dir.mkdir(parents=True, exist_ok=True)

    pages: list[PageText] = []
    ocr_needed: list[int] = []

    with pdfplumber.open(str(pdf_path)) as pdf:
        total = len(pdf.pages)
        limit = min(total, max_pages) if max_pages else total
        for idx in range(limit):
            page_no = idx + 1
            cached = out_dir / f"page_{page_no:04d}.json"
            if cache and cached.exists() and not force:
                data = json.loads(cached.read_text(encoding="utf-8"))
                pages.append(PageText(
                    page_no=data["page_no"], text=data["text"], source=data["source"],
                    ocr_confidence=data.get("ocr_confidence"),
                    char_count=data.get("char_count", 0),
                    tables=data.get("tables", []),
                ))
                continue

            page = pdf.pages[idx]
            raw = page.extract_text() or ""
            text = normalize_block(raw)
            if len(text.strip()) >= settings.pdf_text_min_chars:
                page_tables = _extract_tables(page) if tables else []
                pt = PageText(page_no=page_no, text=text, source="text_layer",
                              char_count=len(text), tables=page_tables)
            else:
                pt = PageText(page_no=page_no, text=text, source="empty", char_count=len(text))
                ocr_needed.append(page_no)
            pages.append(pt)
            if cache:
                cached.write_text(json.dumps(pt.to_dict(), ensure_ascii=False),
                                  encoding="utf-8")

    if ocr_needed and do_ocr:
        log.info("%s: %d/%d page(s) have no text layer - sending to OCR",
                 pdf_path.name, len(ocr_needed), len(pages))
        from ingest.ocr_tesseract import ocr_pages

        results = ocr_pages(pdf_path, ocr_needed)
        by_page = {r.page_no: r for r in results}
        for i, pt in enumerate(pages):
            r = by_page.get(pt.page_no)
            if r is not None:
                pages[i] = r
                if cache:
                    (out_dir / f"page_{r.page_no:04d}.json").write_text(
                        json.dumps(r.to_dict(), ensure_ascii=False), encoding="utf-8")

    return pages


def _extract_tables(page) -> list[list[list[str]]]:
    """Form 20 pages are ruled tables; pdfplumber's lattice strategy reads them
    far more reliably than row regex on flowed text. Regex remains the fallback
    for OCR pages, which have no ruling lines."""
    try:
        tables = page.extract_tables({
            "vertical_strategy": "lines",
            "horizontal_strategy": "lines",
            "intersection_tolerance": 5,
        }) or []
    except Exception as exc:
        log.debug("table extraction failed on page %s: %s", page.page_number, exc)
        return []
    cleaned: list[list[list[str]]] = []
    for table in tables:
        rows = [[normalize_text(cell) if cell else "" for cell in row] for row in table]
        rows = [r for r in rows if any(c for c in r)]
        if rows:
            cleaned.append(rows)
    return cleaned


def full_text(pages: list[PageText]) -> str:
    return "\n".join(f"<<<PAGE {p.page_no}>>>\n{p.text}" for p in pages)


def register_source_doc(pdf_path: Path, kind: str, pages: list[PageText],
                        url: str | None = None) -> int | None:
    """Record the file in source_doc for audit and idempotency (LLD 12).

    The file is on local disk, so the row says `storage_backend='local'` and a
    `storage_key` - required since 0013. The key is the path relative to
    RAW_DIR when the file lives there (how LocalStorage resolves keys), and
    otherwise `kind/filename`, the convention 0013's backfill used. Without it
    every `extract_pdf --register` failed with a NOT NULL violation.
    """
    from common.config import get_settings
    from common.db import query_one

    raw_dir = Path(get_settings().raw_dir).resolve()
    resolved = pdf_path.resolve()
    try:
        key = resolved.relative_to(raw_dir).as_posix()
    except ValueError:
        key = f"{kind}/{pdf_path.name}"

    digest = sha256_file(pdf_path)
    ocr_pages_n = sum(1 for p in pages if p.source == "ocr")
    row = query_one(
        "INSERT INTO source_doc (kind, url, filename, sha256, bytes, pages, ocr_pages, "
        "parse_status, storage_backend, storage_key) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, 'extracted', %s, %s) "
        "ON CONFLICT (sha256) DO UPDATE SET pages = EXCLUDED.pages, ocr_pages = EXCLUDED.ocr_pages, "
        "parse_status = CASE WHEN source_doc.parse_status = 'new' THEN 'extracted' "
        "ELSE source_doc.parse_status END "
        "RETURNING doc_id",
        (kind, url, pdf_path.name, digest, pdf_path.stat().st_size, len(pages), ocr_pages_n,
         "local", key),
    )
    return row["doc_id"] if row else None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Extract text from a source PDF")
    ap.add_argument("pdf", nargs="?", help="path to a PDF")
    ap.add_argument("--all", action="store_true", help="every PDF under RAW_DIR")
    ap.add_argument("--kind", default="other", help="source_doc.kind for registration")
    ap.add_argument("--force", action="store_true", help="ignore the page cache")
    ap.add_argument("--no-ocr", action="store_true", help="skip the OCR fallback")
    ap.add_argument("--register", action="store_true", help="write a source_doc row (needs the DB)")
    args = ap.parse_args(argv)

    settings = get_settings()
    if args.all:
        targets = sorted(Path(settings.raw_dir).rglob("*.pdf"))
    elif args.pdf:
        targets = [Path(args.pdf)]
    else:
        ap.error("give a PDF path or --all")

    if not targets:
        log.warning("no PDFs found")
        return 1

    with job_context("ingest.extract_pdf", files=len(targets)) as job:
        for path in targets:
            if not path.exists():
                log.error("missing: %s", path)
                continue
            pages = extract_document(path, force=args.force, do_ocr=not args.no_ocr)
            ocr_n = sum(1 for p in pages if p.source == "ocr")
            low = [p.page_no for p in pages
                   if p.ocr_confidence is not None and p.ocr_confidence < settings.ocr_min_confidence]
            job.log_line(
                f"{path.name}: {len(pages)} page(s), {ocr_n} OCR'd, "
                f"{len(low)} below confidence {settings.ocr_min_confidence}"
            )
            if args.register:
                register_source_doc(path, args.kind, pages)
    return 0


if __name__ == "__main__":
    sys.exit(main())
