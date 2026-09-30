"""Stage 2 of the PDF pipeline (LLD 4.1): Tesseract hin+eng for pages with no
text layer.

    pdftoppm -r 300 -f N -l N -png source.pdf out   # rasterise
    tesseract out-N.png - -l hin+eng --psm 6        # recognise

Pages that come back under OCR_MIN_CONFIDENCE go to review_queue(kind='ocr_page')
rather than into the results tables. Nothing loads on a low-confidence page.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from common.config import get_settings
from common.logging_setup import get_logger
from common.textnorm import normalize_block

log = get_logger(__name__)

DPI = 300


def _have(binary: str) -> bool:
    return shutil.which(binary) is not None


def rasterise(pdf_path: Path, page_no: int, out_dir: Path, dpi: int = DPI) -> Path | None:
    """One page -> PNG via poppler's pdftoppm."""
    if not _have("pdftoppm"):
        log.error("pdftoppm not found - install poppler-utils (it is in the worker image)")
        return None
    prefix = out_dir / f"p{page_no:04d}"
    cmd = ["pdftoppm", "-r", str(dpi), "-f", str(page_no), "-l", str(page_no),
           "-png", str(pdf_path), str(prefix)]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=300)
    except subprocess.CalledProcessError as exc:
        log.error("pdftoppm failed on page %d: %s", page_no, exc.stderr.decode(errors="replace")[:400])
        return None
    except subprocess.TimeoutExpired:
        log.error("pdftoppm timed out on page %d", page_no)
        return None
    matches = sorted(out_dir.glob(f"p{page_no:04d}*.png"))
    return matches[0] if matches else None


def ocr_image(png_path: Path, langs: str | None = None, psm: int = 6) -> tuple[str, float]:
    """Return (text, mean word confidence 0-100)."""
    settings = get_settings()
    langs = langs or settings.tesseract_langs
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        return _ocr_via_cli(png_path, langs, psm)

    with Image.open(png_path) as img:
        config = f"--psm {psm}"
        text = normalize_block(pytesseract.image_to_string(img, lang=langs, config=config))
        try:
            data = pytesseract.image_to_data(img, lang=langs, config=config,
                                             output_type=pytesseract.Output.DICT)
            confs = [float(c) for c in data.get("conf", []) if str(c).strip() not in ("", "-1")]
            mean_conf = sum(confs) / len(confs) if confs else 0.0
        except Exception:
            mean_conf = 0.0
    return text, round(mean_conf, 1)


def _ocr_via_cli(png_path: Path, langs: str, psm: int) -> tuple[str, float]:
    if not _have("tesseract"):
        log.error("tesseract not found - install tesseract-ocr with the hin and eng data")
        return "", 0.0
    cmd = ["tesseract", str(png_path), "-", "-l", langs, "--psm", str(psm)]
    try:
        out = subprocess.run(cmd, check=True, capture_output=True, timeout=300)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        log.error("tesseract failed on %s: %s", png_path.name, exc)
        return "", 0.0
    # The CLI gives no confidence without TSV output; treat it as unknown-but-usable.
    return normalize_block(out.stdout.decode("utf-8", errors="replace")), 0.0


def ocr_pages(pdf_path: Path, page_numbers: list[int]):
    """OCR the given 1-based pages. Returns PageText objects."""
    from ingest.extract_pdf import PageText

    settings = get_settings()
    results: list[PageText] = []
    with tempfile.TemporaryDirectory(prefix="giridih-ocr-") as tmp:
        tmp_dir = Path(tmp)
        for page_no in page_numbers:
            png = rasterise(pdf_path, page_no, tmp_dir)
            if png is None:
                results.append(PageText(page_no=page_no, text="", source="empty"))
                continue
            text, conf = ocr_image(png)
            results.append(PageText(page_no=page_no, text=text, source="ocr",
                                    ocr_confidence=conf, char_count=len(text)))
            if conf and conf < settings.ocr_min_confidence:
                _queue_review(pdf_path, page_no, conf, text)
            png.unlink(missing_ok=True)
    return results


def _queue_review(pdf_path: Path, page_no: int, conf: float, text: str) -> None:
    """Low-confidence page -> manual review. Never silently loaded."""
    try:
        from common.db import execute

        execute(
            "INSERT INTO review_queue (kind, ref, payload) VALUES ('ocr_page', %s, %s)",
            (f"{pdf_path.name}#p{page_no}",
             json.dumps({"confidence": conf, "excerpt": text[:1500], "file": pdf_path.name},
                        ensure_ascii=False)),
        )
    except Exception as exc:
        log.warning("could not queue OCR review for %s p%d: %s", pdf_path.name, page_no, exc)
