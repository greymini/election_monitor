"""Form 20 (booth-wise result) parser and loader.

Two extraction paths, tried in order:

  1. **Ruled table** - pdfplumber's lattice tables. Recent CEO PDFs are ruled,
     and column positions are unambiguous, so this is by far the safer path.
  2. **Row regex** - for OCR'd or flowed pages with no ruling lines:
         ^(\d{1,3})\s+((?:\d+\s+)+)(\d+)$
     giving ps_number, per-candidate votes, and the trailing total.

Validation is a gate, not a report (LLD 4.2). Per row, the candidate votes plus
NOTA must equal the printed valid total; across rows, the sum must equal the
AC-level total published by the ECI, tolerance zero. A file that fails does not
load - the failing rows go to review_queue(kind='form20_row').

    python -m ingest.parse_form20 raw/form20_vs2024.pdf --election VS-2024 --dry-run
    python -m ingest.parse_form20 raw/form20_vs2024.pdf --election VS-2024 --load
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from common.logging_setup import get_logger
from common.textnorm import normalize_digits, normalize_text, parse_int
from ingest.documents import DocumentNotFound, add_document_arguments, advance_status, open_document

log = get_logger(__name__)

# Trailing (non-candidate) columns, in the order Jharkhand Form 20 prints them.
TAIL_LABELS = {
    "total_valid": [r"total\s*(?:number\s*)?of\s*valid\s*votes", r"कुल\s*वैध\s*मत", r"वैध\s*मतों?\s*की"],
    "rejected":    [r"(?:no|number)\.?\s*of\s*rejected\s*votes", r"अस्वीकृत\s*मत", r"रद्द\s*मत"],
    "nota":        [r"\bnota\b", r"none\s*of\s*the\s*above", r"नोटा", r"इनमें\s*से\s*कोई\s*नहीं"],
    "total":       [r"^total$", r"^कुल$", r"कुल\s*योग"],
    "tendered":    [r"tendered\s*votes", r"निविदत्त\s*मत"],
}

PS_HEADER = [r"serial\s*(?:no|number)", r"polling\s*station", r"मतदान\s*केन्?द्र", r"क्रम\s*सं"]

ROW_RE = re.compile(r"^\s*(\d{1,3})\s+((?:\d{1,6}\s+)+)(\d{1,7})\s*$")


@dataclass
class Form20Row:
    ps_number: int
    votes: list[int]
    total_valid: int | None = None
    rejected: int | None = None
    nota: int | None = None
    total: int | None = None
    tendered: int | None = None
    page_no: int = 0
    raw: str = ""

    def sum_candidates(self) -> int:
        return sum(self.votes)


@dataclass
class Form20Document:
    source_doc: str
    candidate_columns: list[str] = field(default_factory=list)
    rows: list[Form20Row] = field(default_factory=list)
    tail_order: list[str] = field(default_factory=list)
    method: str = "table"
    warnings: list[str] = field(default_factory=list)

    def candidate_totals(self) -> list[int]:
        totals = [0] * len(self.candidate_columns)
        for row in self.rows:
            for i, v in enumerate(row.votes[: len(totals)]):
                totals[i] += v
        return totals

    def nota_total(self) -> int:
        return sum(r.nota or 0 for r in self.rows)


def _match_any(text: str, patterns: list[str]) -> bool:
    t = normalize_text(text).lower()
    return any(re.search(p, t, flags=re.IGNORECASE) for p in patterns)


def classify_header(cells: list[str]) -> tuple[list[str], list[str], int]:
    """Split a header row into (candidate labels, tail labels, ps column index).

    Everything between the polling-station column and the first recognised tail
    column is treated as a candidate column, which is how Form 20 is laid out.
    """
    ps_idx = 0
    for i, cell in enumerate(cells):
        if _match_any(cell, PS_HEADER):
            ps_idx = i
            break

    tail_idx: dict[int, str] = {}
    for i, cell in enumerate(cells):
        if i <= ps_idx:
            continue
        for label, patterns in TAIL_LABELS.items():
            if label not in tail_idx.values() and _match_any(cell, patterns):
                tail_idx[i] = label
                break

    first_tail = min(tail_idx) if tail_idx else len(cells)
    candidates = [normalize_text(c) for c in cells[ps_idx + 1: first_tail]]
    tail_order = [tail_idx[i] for i in sorted(tail_idx)]
    return candidates, tail_order, ps_idx


def _is_data_row(cells: list[str], ps_idx: int) -> bool:
    if len(cells) <= ps_idx + 1:
        return False
    first = parse_int(cells[ps_idx])
    if first is None or not (0 < first < 1000):
        return False
    numeric = sum(1 for c in cells[ps_idx + 1:] if parse_int(c) is not None)
    return numeric >= 2


def parse_from_tables(pages, source_doc: str) -> Form20Document | None:
    """Preferred path: ruled tables from pdfplumber."""
    doc = Form20Document(source_doc=source_doc, method="table")
    ps_idx = 0
    seen_header = False

    for page in pages:
        for table in page.tables:
            for row in table:
                cells = [normalize_text(c) for c in row]
                if not seen_header:
                    cands, tail, idx = classify_header(cells)
                    # A header is only credible if it names at least two
                    # candidate columns and at least one tail column.
                    if len(cands) >= 2 and tail:
                        doc.candidate_columns = cands
                        doc.tail_order = tail
                        ps_idx = idx
                        seen_header = True
                        continue
                if seen_header and _is_data_row(cells, ps_idx):
                    parsed = _row_from_cells(cells, ps_idx, doc, page.page_no)
                    if parsed:
                        doc.rows.append(parsed)

    if not seen_header or not doc.rows:
        return None
    return doc


def _row_from_cells(cells: list[str], ps_idx: int, doc: Form20Document, page_no: int) -> Form20Row | None:
    ps_number = parse_int(cells[ps_idx])
    if ps_number is None:
        return None
    n_cand = len(doc.candidate_columns)
    body = cells[ps_idx + 1:]
    votes = [parse_int(c) or 0 for c in body[:n_cand]]
    # A short row must not silently drop trailing candidates: pad it and let
    # validate() catch the arithmetic mismatch that the padding creates.
    votes += [0] * (n_cand - len(votes))
    tail_values = [parse_int(c) for c in body[n_cand:]]

    row = Form20Row(ps_number=ps_number, votes=votes, page_no=page_no,
                    raw=" | ".join(cells))
    for label, value in zip(doc.tail_order, tail_values, strict=False):
        setattr(row, label, value)
    return row


def parse_from_text(pages, source_doc: str, candidate_columns: list[str],
                    tail_order: list[str] | None = None) -> Form20Document:
    """Fallback path for OCR'd pages: one data row per line.

    Column labels cannot be recovered from flowed text, so they must be supplied
    (from the ruled pages of the same document, or from a sidecar mapping).
    """
    tail_order = tail_order or ["total_valid", "rejected", "nota", "total"]
    doc = Form20Document(source_doc=source_doc, method="regex",
                         candidate_columns=list(candidate_columns), tail_order=list(tail_order))
    n_cand = len(candidate_columns)

    for page in pages:
        for line in page.text.splitlines():
            m = ROW_RE.match(normalize_digits(normalize_text(line)))
            if not m:
                continue
            ps_number = int(m.group(1))
            middle = [int(x) for x in m.group(2).split()]
            last = int(m.group(3))
            numbers = middle + [last]
            if len(numbers) < n_cand:
                doc.warnings.append(f"p{page.page_no} PS {ps_number}: only {len(numbers)} numbers "
                                    f"for {n_cand} candidate columns - skipped")
                continue
            row = Form20Row(ps_number=ps_number, votes=numbers[:n_cand],
                            page_no=page.page_no, raw=line.strip())
            for label, value in zip(tail_order, numbers[n_cand:], strict=False):
                setattr(row, label, value)
            doc.rows.append(row)
    return doc


def parse_pdf(pdf_path: Path, force: bool = False) -> Form20Document:
    """Extract and parse a Form 20 PDF, tables first, regex for the rest."""
    from ingest.extract_pdf import extract_document

    pages = extract_document(pdf_path, force=force)
    doc = parse_from_tables(pages, pdf_path.name)
    if doc is not None:
        # Pages that produced no ruled table (typically OCR'd) are re-read with
        # the regex path, using the column labels the ruled pages established.
        covered = {r.page_no for r in doc.rows}
        leftovers = [p for p in pages if p.page_no not in covered and p.text.strip()]
        if leftovers and doc.candidate_columns:
            extra = parse_from_text(leftovers, pdf_path.name, doc.candidate_columns, doc.tail_order)
            known = {r.ps_number for r in doc.rows}
            doc.rows.extend(r for r in extra.rows if r.ps_number not in known)
            doc.warnings.extend(extra.warnings)
            if extra.rows:
                doc.method = "table+regex"
        return doc

    raise ValueError(
        f"{pdf_path.name}: no Form 20 header row found. Supply a column mapping "
        f"with --columns and re-run, or send the file to manual review."
    )


# --------------------------------------------------------------------------
# Validation (LLD 4.2) - a gate, not a report
# --------------------------------------------------------------------------

@dataclass
class ValidationError:
    kind: str
    ref: str
    detail: str
    payload: dict = field(default_factory=dict)


def validate(doc: Form20Document, ac_totals: dict[str, int] | None = None) -> list[ValidationError]:
    errors: list[ValidationError] = []

    seen: dict[int, int] = {}
    for row in doc.rows:
        if row.ps_number in seen:
            errors.append(ValidationError(
                "form20_row", f"{doc.source_doc}#PS{row.ps_number}",
                f"PS {row.ps_number} appears twice (pages {seen[row.ps_number]} and {row.page_no})",
                {"ps_number": row.ps_number, "raw": row.raw},
            ))
        seen[row.ps_number] = row.page_no

        if row.total_valid is not None:
            expected = row.sum_candidates() + (row.nota or 0)
            if expected != row.total_valid:
                errors.append(ValidationError(
                    "form20_row", f"{doc.source_doc}#PS{row.ps_number}",
                    f"PS {row.ps_number}: candidate votes + NOTA = {expected}, "
                    f"printed valid total = {row.total_valid}",
                    {"ps_number": row.ps_number, "page": row.page_no, "raw": row.raw,
                     "computed": expected, "printed": row.total_valid},
                ))
        if any(v < 0 for v in row.votes):
            errors.append(ValidationError(
                "form20_row", f"{doc.source_doc}#PS{row.ps_number}",
                f"PS {row.ps_number}: negative vote count", {"raw": row.raw},
            ))

    if doc.rows:
        numbers = sorted(seen)
        gaps = [n for n in range(numbers[0], numbers[-1] + 1) if n not in seen]
        if gaps:
            errors.append(ValidationError(
                "form20_row", doc.source_doc,
                f"{len(gaps)} polling station number(s) missing between "
                f"{numbers[0]} and {numbers[-1]}: {gaps[:25]}{' ...' if len(gaps) > 25 else ''}",
                {"missing": gaps},
            ))

    if ac_totals:
        totals = dict(zip(doc.candidate_columns, doc.candidate_totals(), strict=True))
        totals["NOTA"] = doc.nota_total()
        for name, published in ac_totals.items():
            got = totals.get(name)
            if got is None:
                errors.append(ValidationError(
                    "form20_row", doc.source_doc,
                    f"AC total given for {name!r} but no such column in the document "
                    f"(columns: {doc.candidate_columns})", {"column": name},
                ))
            elif got != published:
                errors.append(ValidationError(
                    "form20_row", doc.source_doc,
                    f"{name}: booth sum {got} != published AC total {published} "
                    f"(difference {got - published:+d})",
                    {"column": name, "booth_sum": got, "published": published},
                ))
    return errors


def queue_errors(errors: list[ValidationError]) -> int:
    from common.db import execute

    for err in errors:
        execute(
            "INSERT INTO review_queue (kind, ref, payload, note) VALUES (%s, %s, %s, %s)",
            (err.kind, err.ref, json.dumps(err.payload, ensure_ascii=False, default=str), err.detail),
        )
    return len(errors)


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------

PARTY_IN_HEADER = re.compile(r"\(([^)]{2,40})\)\s*$")


def split_candidate_label(label: str) -> tuple[str, str | None]:
    """'Sudivya Kumar (JMM)' -> ('Sudivya Kumar', 'JMM'). Party may be absent."""
    label = normalize_text(label).replace("\n", " ")
    m = PARTY_IN_HEADER.search(label)
    if m:
        return normalize_text(label[: m.start()]), normalize_text(m.group(1))
    return label, None


def resolve_candidates(doc: Form20Document, election_id: int) -> list[int]:
    """Create or find a candidate row per column; return candidate_ids in order."""
    from common.db import cursor

    ids: list[int] = []
    with cursor() as cur:
        cur.execute("SELECT party_id, abbr, name_en FROM party")
        parties = cur.fetchall()
        by_abbr = {p["abbr"].lower(): p["party_id"] for p in parties}
        by_name = {p["name_en"].lower(): p["party_id"] for p in parties}

        for index, label in enumerate(doc.candidate_columns):
            name, party_text = split_candidate_label(label)
            party_id = None
            if party_text:
                key = party_text.lower()
                party_id = by_abbr.get(key) or by_name.get(key)
                if party_id is None:
                    log.warning("column %r: party %r not in the party table - left unattributed",
                                label, party_text)
            cur.execute(
                "INSERT INTO candidate (election_id, name_en, party_id, column_index) "
                "VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (election_id, name_en, party_id) DO UPDATE SET "
                "column_index = EXCLUDED.column_index RETURNING candidate_id",
                (election_id, name or label, party_id, index),
            )
            ids.append(cur.fetchone()["candidate_id"])
    return ids


def load(doc: Form20Document, election_label: str, replace: bool = False) -> int:
    """Load a validated document. Whole thing in one transaction."""
    from common.db import connection

    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT election_id FROM election WHERE label = %s", (election_label,))
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"unknown election label {election_label!r} - add it to db/seed/elections.csv")
        election_id = row["election_id"]

        if replace:
            cur.execute("DELETE FROM result_booth WHERE election_id = %s", (election_id,))
            cur.execute("DELETE FROM result_booth_meta WHERE election_id = %s", (election_id,))

    candidate_ids = resolve_candidates(doc, election_id)

    with connection() as conn, conn.cursor() as cur:
        for r in doc.rows:
            # strict=True: every candidate column must get a value. Rows are
            # padded at parse time, so a mismatch here means a real defect.
            for cid, votes in zip(candidate_ids, r.votes, strict=True):
                cur.execute(
                    "INSERT INTO result_booth (election_id, ps_number, candidate_id, votes) "
                    "VALUES (%s, %s, %s, %s) "
                    "ON CONFLICT (election_id, ps_number, candidate_id) DO UPDATE SET votes = EXCLUDED.votes",
                    (election_id, r.ps_number, cid, votes),
                )
            cur.execute(
                "INSERT INTO result_booth_meta (election_id, ps_number, total_valid, nota, rejected, "
                "tendered, source_doc, source_page) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (election_id, ps_number) DO UPDATE SET total_valid = EXCLUDED.total_valid, "
                "nota = EXCLUDED.nota, rejected = EXCLUDED.rejected, tendered = EXCLUDED.tendered, "
                "source_doc = EXCLUDED.source_doc, source_page = EXCLUDED.source_page, loaded_at = now()",
                (election_id, r.ps_number, r.total_valid, r.nota, r.rejected, r.tendered,
                 doc.source_doc, r.page_no),
            )
    return len(doc.rows)


def published_ac_totals(election_label: str) -> dict[str, int]:
    """AC totals from result_ac_total, keyed by the label the Form 20 column uses."""
    from common.db import query

    rows = query(
        "SELECT c.name_en, p.abbr, t.metric, t.value FROM result_ac_total t "
        "JOIN election e ON e.election_id = t.election_id "
        "LEFT JOIN candidate c ON c.candidate_id = t.candidate_id "
        "LEFT JOIN party p ON p.party_id = c.party_id "
        "WHERE e.label = %s AND t.metric IN ('votes', 'nota')",
        (election_label,),
    )
    out: dict[str, int] = {}
    for r in rows:
        if r["metric"] == "nota":
            out["NOTA"] = r["value"]
        elif r["name_en"]:
            key = f"{r['name_en']} ({r['abbr']})" if r["abbr"] else r["name_en"]
            out[key] = r["value"]
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Parse and load a Form 20 PDF")
    # A path, --doc SHA256 or --key STORAGE_KEY. --doc is what makes this
    # runnable from a laptop against a remote DATABASE_URL: the source_doc row
    # says which backend holds the bytes, so no local raw/ tree is needed.
    add_document_arguments(ap)
    ap.add_argument("--election", required=True, help="election label, e.g. VS-2024")
    ap.add_argument("--load", action="store_true", help="write to the database")
    ap.add_argument("--replace", action="store_true", help="delete existing rows for this election first")
    ap.add_argument("--dry-run", action="store_true", help="parse and validate only (default)")
    ap.add_argument("--force", action="store_true", help="ignore the page cache")
    ap.add_argument("--skip-ac-check", action="store_true",
                    help="skip the AC-total comparison (only when no published total exists yet)")
    ap.add_argument("--json", help="write the parsed document to this path")
    args = ap.parse_args(argv)

    try:
        with open_document(pdf=args.pdf, doc=args.doc, key=args.key,
                           backend=args.backend, kind="form20") as (pdf_path, provenance):
            return _run(args, pdf_path, provenance)
    except DocumentNotFound as exc:
        log.error("%s", exc)
        return 2


def _run(args, pdf_path: Path, provenance: dict) -> int:
    doc = parse_pdf(pdf_path, force=args.force)
    log.info("%s: %d row(s), %d candidate column(s), method=%s",
             pdf_path.name, len(doc.rows), len(doc.candidate_columns), doc.method)
    for i, col in enumerate(doc.candidate_columns):
        log.info("  col %2d  %s", i, col)
    for w in doc.warnings:
        log.warning("  %s", w)

    if args.json:
        Path(args.json).write_text(json.dumps({
            "source_doc": doc.source_doc,
            "method": doc.method,
            "candidate_columns": doc.candidate_columns,
            "tail_order": doc.tail_order,
            "rows": [vars(r) for r in doc.rows],
        }, ensure_ascii=False, indent=2), encoding="utf-8")

    ac_totals = None
    if not args.skip_ac_check:
        try:
            ac_totals = published_ac_totals(args.election) or None
            if ac_totals:
                log.info("checking against %d published AC total(s)", len(ac_totals))
            else:
                log.warning("no published AC totals for %s - loading without the cross-check. "
                            "Add them to db/seed/ac_totals.csv.", args.election)
        except Exception as exc:
            log.warning("could not read published AC totals (%s); continuing without them", exc)

    errors = validate(doc, ac_totals)
    if errors:
        log.error("%d validation error(s):", len(errors))
        for e in errors[:20]:
            log.error("  %s", e.detail)
        if len(errors) > 20:
            log.error("  ... and %d more", len(errors) - 20)
        try:
            queue_errors(errors)
            log.error("queued for manual review (review_queue kind='form20_row')")
        except Exception as exc:
            log.error("could not write to review_queue: %s", exc)
        log.error("NOT LOADING %s - fix the rows above and re-run", pdf_path.name)
        _advance(provenance, "failed")
        return 1

    log.info("validation passed")
    _advance(provenance, "validated")
    if args.load and not args.dry_run:
        n = load(doc, args.election, replace=args.replace)
        log.info("loaded %d booth row(s) for %s", n, args.election)
        _advance(provenance, "loaded")
        log.info("now run: python -m ingest.crosswalk --election %s", args.election)
    else:
        log.info("dry run - nothing written. Re-run with --load to write.")
    return 0


def _advance(provenance: dict, status: str) -> None:
    """B10: move source_doc.parse_status so /admin/sources can answer "has this
    PDF been loaded?". Best effort - a bookkeeping failure must not fail a load
    that otherwise succeeded, but it is logged rather than swallowed."""
    digest = provenance.get("sha256")
    if not digest:
        return
    try:
        advance_status(digest, status, actor="ingest.parse_form20")
    except Exception as exc:
        log.warning("could not set parse_status=%s for %s: %s", status, digest[:12], exc)


if __name__ == "__main__":
    sys.exit(main())
