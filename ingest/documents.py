"""Resolving a document a parser has been asked to read.

Every parser CLI took a positional local path, which is fine on the box that
downloaded the PDF and useless anywhere else. Now that published documents may
live in object storage (common/storage.py), a parser needs three ways in:

    python -m ingest.parse_form20 raw/form20/x.pdf --election VS-2024
    python -m ingest.parse_form20 --doc 3f9a2c1e... --election VS-2024
    python -m ingest.parse_form20 --key form20/2024-VS/32/x.pdf --election VS-2024

The first is unchanged. The second looks the document up in `source_doc` by
sha256 - the only stable identity a document has - and reads whichever backend
that row records, which is what makes ingestion runnable from a laptop against a
remote DATABASE_URL: the database says where the bytes are, so an analyst does
not need a copy of `raw/`. The third names a key directly, for a document that
is in storage but not yet registered.

All three yield a real local path, because `pdfplumber.open()` and the sha256
digest both need a file. For a local backend that is the file itself; for a
remote one it is a temporary download that is deleted on the way out, including
when the parser raises.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from common.logging_setup import get_logger
from common.storage import LOCAL, StorageError, materialise, sha256_path

log = get_logger(__name__)


class DocumentNotFound(StorageError):
    """The requested document is not in the database or not in storage."""


def add_document_arguments(parser) -> None:
    """The three mutually compatible ways to name a document.

    `pdf` stays positional and optional so every command in RUN.md keeps
    working.
    """
    parser.add_argument("pdf", nargs="?", help="path to a local PDF")
    parser.add_argument("--doc", metavar="SHA256",
                        help="sha256 (or unambiguous prefix) of a registered source_doc; "
                             "the row says which backend holds it")
    parser.add_argument("--key", metavar="STORAGE_KEY",
                        help="storage key, e.g. form20/2024-VS/32/giridih.pdf")
    parser.add_argument("--backend", choices=("local", "s3"),
                        help="backend for --key (default: the one configured for the kind)")


def lookup_by_digest(digest: str) -> dict:
    """Find a source_doc row by sha256 or an unambiguous prefix."""
    from common.db import query

    digest = digest.strip().lower()
    rows = query(
        "SELECT doc_id, kind, filename, sha256, storage_backend, storage_key, parse_status "
        "FROM source_doc WHERE sha256 LIKE %s ORDER BY doc_id",
        (f"{digest}%",),
    )
    if not rows:
        raise DocumentNotFound(
            f"no source_doc with sha256 starting {digest!r}. "
            "Run `python -m ingest.fetch_ceo --discover <url> --download` to fetch it, or "
            "`python -m ingest.extract_pdf <path> --register` to register a file you already have."
        )
    if len(rows) > 1:
        found = ", ".join(r["sha256"][:12] for r in rows[:5])
        raise DocumentNotFound(f"{digest!r} matches {len(rows)} documents ({found}); be more specific")
    return rows[0]


@contextmanager
def open_document(
    pdf: str | None = None,
    doc: str | None = None,
    key: str | None = None,
    backend: str | None = None,
    kind: str | None = None,
    verify: bool = True,
) -> Iterator[tuple[Path, dict]]:
    """Yield `(local_path, provenance)` for whichever way the document was named.

    `provenance` carries what is known about the document - doc_id, kind,
    filename, sha256, backend, key - so the caller can record where a row came
    from without looking it up again.

    With `verify`, a document fetched by `--doc` has its digest checked against
    the one recorded. That is not paranoia: object storage is eventually
    consistent, keys can be overwritten, and loading a Form 20 that is not the
    document the AC-total reconciliation was performed against would put
    unreconciled numbers into the serving tables under a source_doc reference
    that looks audited.
    """
    named = [name for name, value in (("pdf", pdf), ("--doc", doc), ("--key", key)) if value]
    if len(named) != 1:
        raise DocumentNotFound(
            "name exactly one document: a path, --doc SHA256, or --key STORAGE_KEY"
            + (f" (got {', '.join(named)})" if named else "")
        )

    if pdf:
        path = Path(pdf)
        if not path.exists():
            raise DocumentNotFound(f"no such file: {path}")
        yield path, {
            "doc_id": None,
            "kind": kind,
            "filename": path.name,
            "sha256": sha256_path(path),
            "storage_backend": LOCAL,
            "storage_key": None,
        }
        return

    if doc:
        row = lookup_by_digest(doc)
        with materialise(row["storage_backend"], row["storage_key"]) as path:
            if verify:
                actual = sha256_path(path)
                if actual != row["sha256"]:
                    raise DocumentNotFound(
                        f"{row['storage_backend']}://{row['storage_key']} has digest "
                        f"{actual[:12]} but source_doc {row['doc_id']} records "
                        f"{row['sha256'][:12]}. The stored object is not the document that was "
                        "registered; do not load it. Re-fetch, or register the new bytes "
                        "separately."
                    )
            yield path, row
        return

    # --key: not necessarily registered, so nothing to verify against.
    from common.storage import backend_name_for_kind

    resolved = backend or (backend_name_for_kind(kind) if kind else LOCAL)
    with materialise(resolved, key) as path:
        yield path, {
            "doc_id": None,
            "kind": kind,
            "filename": Path(key).name,
            "sha256": sha256_path(path),
            "storage_backend": resolved,
            "storage_key": key,
        }


def advance_status(sha256: str, status: str, actor: str) -> None:
    """Move a document through the parse_status lifecycle (spec 5.7, audit B10).

    The audit found nothing ever advanced parse_status past 'extracted' and
    parsed_at was never set, so /admin/sources could not answer "has this PDF
    been loaded?". Statuses only move forward; a document already 'loaded' is
    not dragged back to 'parsed' by a later dry run.
    """
    from common.db import execute

    order = ["new", "extracted", "parsed", "validated", "loaded"]
    if status in ("failed", "drifted"):
        execute(
            "UPDATE source_doc SET parse_status = %s, status_changed_at = now(), "
            "status_changed_by = %s WHERE sha256 = %s",
            (status, actor, sha256),
        )
        return
    if status not in order:
        raise ValueError(f"unknown parse_status {status!r}")

    execute(
        "UPDATE source_doc SET parse_status = %s, status_changed_at = now(), "
        "status_changed_by = %s, "
        "parsed_at = CASE WHEN %s IN ('parsed', 'validated', 'loaded') "
        "                 THEN COALESCE(parsed_at, now()) ELSE parsed_at END "
        "WHERE sha256 = %s "
        # Never regress, and never overwrite a terminal failure silently.
        "  AND (parse_status IS NULL OR array_position(%s::text[], parse_status) IS NULL "
        "       OR array_position(%s::text[], parse_status) < array_position(%s::text[], %s))",
        (status, actor, status, sha256, order, order, order, status),
    )
