"""Fetch source documents from CEO Jharkhand (HLD 4, LLD 2).

The portal's layout is not stable and is not documented anywhere, so this is a
generic link discoverer rather than a set of hardcoded URLs: give it a page,
it finds the PDFs, classifies them by filename, and downloads the ones that are
new by content hash.

    python -m ingest.fetch_ceo --discover https://ceo.jharkhand.gov.in/...
    python -m ingest.fetch_ceo --discover <page> --kind form20 --download

VERIFY THE CLASSIFICATION before a bulk download. Run --discover first and read
the table it prints: a mislabelled file loaded as the wrong kind is far more
expensive to undo than to catch here.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path
from urllib.parse import urljoin, urlparse

from common.config import get_settings
from common.jobs import job_context
from common.logging_setup import get_logger

log = get_logger(__name__)

# Filename patterns, most specific first. Extend these as the portal changes.
KIND_PATTERNS: list[tuple[str, str]] = [
    (r"form.?20|फॉर्म.?20|booth.?wise|मतदान.?केन्?द्रवार", "form20"),
    (r"suppl|परिवर्धन|addition|विलोपन|deletion", "roll_supplement"),
    (r"(mother|final|draft).?roll|मूल.?सूची|निर्वाचक.?नामावली", "roll_mother"),
    (r"polling.?station|ps.?list|मतदान.?केन्?द्र.?सूची", "ps_list"),
]

AC_HINTS = [r"\b32\b", r"giridih", r"गिरिडीह"]


def classify(name: str) -> str:
    lowered = name.lower()
    for pattern, kind in KIND_PATTERNS:
        if re.search(pattern, lowered, re.IGNORECASE):
            return kind
    return "other"


def mentions_ac32(text: str) -> bool:
    return any(re.search(p, text, re.IGNORECASE) for p in AC_HINTS)


def list_remote_documents(page_url: str | None = None, kind: str | None = None,
                          ac_only: bool = True) -> list[dict]:
    """Discover PDF links on a page. Returns dicts with url, filename, kind.

    `sha256` is only filled in once a file has actually been downloaded, so
    callers that dedupe on hash must download first.
    """
    import httpx
    from bs4 import BeautifulSoup

    settings = get_settings()
    page_url = page_url or settings.ceo_base
    try:
        response = httpx.get(page_url, timeout=30, follow_redirects=True,
                             headers={"User-Agent": settings.news_user_agent})
        response.raise_for_status()
    except Exception as exc:
        log.error("could not read %s: %s", page_url, exc)
        return []

    soup = BeautifulSoup(response.text, "lxml")
    found: list[dict] = []
    seen: set[str] = set()

    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        if ".pdf" not in href.lower():
            continue
        url = urljoin(page_url, href)
        if url in seen:
            continue
        seen.add(url)

        label = f"{anchor.get_text(' ', strip=True)} {href}"
        filename = Path(urlparse(url).path).name or "document.pdf"
        doc_kind = classify(label)
        if kind and doc_kind != kind:
            continue
        if ac_only and not mentions_ac32(label):
            continue
        found.append({"url": url, "filename": filename, "kind": doc_kind,
                      "label": anchor.get_text(" ", strip=True)[:120]})
    return found


def download(url: str, kind: str, dest_dir: Path | None = None) -> dict:
    """Download one PDF and register it. Skips a file already held, by hash."""
    import httpx

    from common.db import query_one

    settings = get_settings()
    dest_dir = dest_dir or Path(settings.raw_dir) / kind
    dest_dir.mkdir(parents=True, exist_ok=True)

    with httpx.stream("GET", url, timeout=120, follow_redirects=True,
                      headers={"User-Agent": settings.news_user_agent}) as response:
        response.raise_for_status()
        payload = b"".join(response.iter_bytes())

    digest = hashlib.sha256(payload).hexdigest()
    existing = query_one("SELECT doc_id, filename FROM source_doc WHERE sha256 = %s", (digest,))
    if existing:
        return {"url": url, "skipped": True, "reason": "already held",
                "filename": existing["filename"]}

    filename = Path(urlparse(url).path).name or f"{digest[:12]}.pdf"
    path = dest_dir / filename
    path.write_bytes(payload)

    query_one(
        "INSERT INTO source_doc (kind, url, filename, sha256, bytes) "
        "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (sha256) DO NOTHING RETURNING doc_id",
        (kind, url, filename, digest, len(payload)),
    )
    return {"url": url, "skipped": False, "path": str(path),
            "bytes": len(payload), "sha256": digest}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Discover and download CEO Jharkhand documents")
    ap.add_argument("--discover", metavar="URL", help="page to scan for PDF links")
    ap.add_argument("--kind", choices=["form20", "roll_supplement", "roll_mother", "ps_list", "other"])
    ap.add_argument("--download", action="store_true", help="download what was discovered")
    ap.add_argument("--all-ac", action="store_true",
                    help="do not filter to AC-32 / Giridih mentions")
    args = ap.parse_args(argv)

    if not args.discover:
        ap.error("--discover URL is required")

    with job_context("ingest.fetch_ceo", page=args.discover) as job:
        docs = list_remote_documents(args.discover, args.kind, ac_only=not args.all_ac)
        job.log_line(f"found {len(docs)} PDF link(s)")
        for doc in docs:
            log.info("  %-18s %-52s %s", doc["kind"], doc["filename"][:52], doc["label"][:60])

        if not docs:
            log.warning("nothing found. The portal layout may have changed - check the URL, and "
                        "extend KIND_PATTERNS in ingest/fetch_ceo.py if the naming is new.")
            return 1

        if not args.download:
            log.info("discovery only. Check the classification above, then re-run with --download.")
            return 0

        downloaded = skipped = 0
        for doc in docs:
            try:
                result = download(doc["url"], doc["kind"])
            except Exception as exc:
                log.error("failed %s: %s", doc["url"], exc)
                continue
            if result["skipped"]:
                skipped += 1
            else:
                downloaded += 1
                job.log_line(f"downloaded {result['path']} ({result['bytes']:,} bytes)")
        job.set(found=len(docs), downloaded=downloaded, skipped=skipped)
        log.info("%d downloaded, %d already held", downloaded, skipped)
    return 0


if __name__ == "__main__":
    sys.exit(main())
