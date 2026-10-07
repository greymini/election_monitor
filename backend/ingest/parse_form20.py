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
from ingest import resolve as resolve_mod
from ingest.acscope import AmbiguousScope, add_ac_argument, resolve_election
from ingest.documents import (
    DocumentNotFound,
    add_document_arguments,
    advance_status,
    open_document,
)

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


def queue_errors(errors: list[ValidationError], ac_id: int) -> int:
    """Queue the rows that failed validation, for one constituency.

    Two fixes, both finding N19. `ac_id` was never set, and
    `GET /acs/{ac}/admin/review-queue` filters on it - so every row the LLD 4.2
    arithmetic gate rejected landed in a queue the Admin page could not show.
    An operator saw "NOT LOADING" and an empty queue.

    And the insert had no conflict target against the `(kind, ref)` unique index
    0014 added for C16, so a second run of the same failing document raised
    instead of updating; `_run` catches that and logs "could not write to
    review_queue", which reads like a database problem rather than a re-run.
    """
    from common.db import execute

    for err in errors:
        execute(
            "INSERT INTO review_queue (kind, ref, ac_id, payload, note) "
            "VALUES (%s, %s, %s, %s, %s) "
            "ON CONFLICT (kind, ref) DO UPDATE SET payload = EXCLUDED.payload, "
            "note = EXCLUDED.note, ac_id = EXCLUDED.ac_id",
            (err.kind, err.ref, ac_id,
             json.dumps(err.payload, ensure_ascii=False, default=str), err.detail),
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


class UnresolvedColumns(Exception):
    """At least one header column could not be resolved, so nothing loads.

    Master prompt 3.1 step 6, and the reason `ingest/resolve.py` exists. The
    behaviour it replaces was to log a warning and load the column with a NULL
    party, which is how a 100% margin reached every booth on the dashboard (C1).
    """

    def __init__(self, failures: list[resolve_mod.Resolution]) -> None:
        self.failures = failures
        super().__init__(f"{len(failures)} Form 20 column(s) could not be resolved")


def resolve_columns_for(doc: Form20Document, election_id: int,
                        cur) -> list[resolve_mod.Resolution]:
    """Master prompt 3.1, applied to this document's candidate columns.

    This is the call that was missing. `ingest/resolve.py` implements all six
    steps and `tests/test_resolve.py` pins them with seventy-odd cases, but
    nothing in the loader ever imported the module: `resolve_candidates` still
    split on a trailing bracket and looked the result up against `abbr` and
    `name_en`, which is the code the audit describes under C1. The fix was
    written and never wired in - the same shape of mistake as G2.
    """
    party_by_alias = resolve_mod.load_party_aliases(cur)
    seeded = resolve_mod.load_seeded_candidates(cur, election_id)
    if not seeded:
        log.warning(
            "no seeded candidates for election %s, so a column can only resolve by "
            "bracketed party or as NOTA. Add the published AC totals to "
            "db/seed/ac_totals.csv so the names have something to match against.",
            election_id,
        )
    resolutions = resolve_mod.resolve_columns(doc.candidate_columns, party_by_alias, seeded)
    for r in resolutions:
        if r.resolved:
            log.info("  col %2d  %-38s -> %-24s %-6s (%s%s)",
                     r.column_index, r.raw_header[:38], r.name, r.party_abbr or "?",
                     r.method, f" {r.score:.3f}" if r.score is not None else "")
    failures = resolve_mod.unresolved(resolutions)
    if failures:
        for r in failures:
            log.error("  col %2d  %r UNRESOLVED: %s", r.column_index, r.raw_header,
                      "; ".join(r.reject_reasons) or "nothing came close")
        raise UnresolvedColumns(failures)
    return resolutions


def queue_unresolved(doc: Form20Document, failures: list[resolve_mod.Resolution],
                     ac_id: int) -> int:
    """One review-queue item per unresolved column, with the top three guesses."""
    from common.db import execute

    for r in failures:
        execute(
            "INSERT INTO review_queue (kind, ref, ac_id, payload, note) "
            "VALUES (%s, %s, %s, %s, %s) "
            "ON CONFLICT (kind, ref) DO UPDATE SET payload = EXCLUDED.payload, "
            "note = EXCLUDED.note, ac_id = EXCLUDED.ac_id, status = 'open'",
            ("form20_column", f"{doc.source_doc}#col{r.column_index}", ac_id,
             json.dumps(resolve_mod.review_payload(r), ensure_ascii=False, default=str),
             f"Form 20 column {r.column_index} ({r.raw_header!r}) resolved to no "
             f"candidate and no party"),
        )
    return len(failures)


NOTA_CANDIDATE_NAME = "NOTA"


def nota_candidate_id(election_id: int, ac_id: int, cur) -> int:
    """The candidate row NOTA's votes are loaded against.

    Finding N7, and it is D1 in a new place. The master prompt is explicit that
    there is one denominator everywhere - valid votes **including** NOTA - and
    `ingest/resolve.py` step 4 says so too: "NOTA in either script resolves to
    the NOTA party, and loads as a candidate row - it has to, or it cannot enter
    the denominator (D1)".

    That branch is unreachable. `TAIL_LABELS` matches `\bnota\b`, and
    `classify_header` ends the candidate run at the first recognised tail
    column, so a NOTA header is classified as a tail value before the resolver
    ever sees it. The loader then wrote it to `result_booth_meta.nota` only -
    and no denominator reads that column. `mv_booth_totals` computes
    `valid_votes` as `SUM(votes)` over `mv_result_booth_party`, and `nota` as
    the NOTA-party slice of the same sum, both of which need a row in
    `result_booth`.

    So with NOTA loaded only to the meta table: `valid_votes` excluded NOTA,
    `nota` came out NULL for every booth, `printed_valid` disagreed with
    `valid_votes` by exactly the NOTA count, and every `share_pct`, `margin_pct`
    and turnout figure in the product was computed over a denominator about one
    percent too small. For Giridih 2024 that is 2,004 votes.

    The printed value stays on `result_booth_meta.nota` as well. It is the
    figure on the page, `validate()` checks the row arithmetic against it, and
    having both lets a divergence be seen rather than averaged away.
    """
    cur.execute("SELECT party_id FROM party WHERE abbr = 'NOTA'")
    row = cur.fetchone()
    if row is None:
        raise ValueError(
            "the party table has no NOTA row, so NOTA votes cannot enter the "
            "denominator. Add it to db/seed/parties.csv and re-seed."
        )
    cur.execute(
        "INSERT INTO candidate (election_id, ac_id, name_en, party_id) "
        "VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (election_id, name_en, party_id) DO UPDATE SET ac_id = EXCLUDED.ac_id "
        "RETURNING candidate_id",
        (election_id, ac_id, NOTA_CANDIDATE_NAME, row["party_id"]),
    )
    return cur.fetchone()["candidate_id"]


def candidate_ids_for(resolutions: list[resolve_mod.Resolution], election_id: int,
                      ac_id: int, cur) -> list[int]:
    """A candidate_id per column, in column order.

    A resolution that matched a seeded candidate reuses that row, so the booth
    votes land against the same candidate the published AC total hangs off -
    which is what makes the reconciliation check mean anything (C2). NOTA and
    independents get their own rows, because both have to rank individually: in
    the denominator for NOTA (D1), and separately from one another for
    independents (D3).
    """
    cur.execute("SELECT party_id, abbr FROM party")
    party_ids = {row["abbr"]: row["party_id"] for row in cur.fetchall()}

    ids: list[int] = []
    for r in resolutions:
        if r.candidate_id is not None:
            cur.execute("UPDATE candidate SET column_index = %s WHERE candidate_id = %s",
                        (r.column_index, r.candidate_id))
            ids.append(r.candidate_id)
            continue
        party_id = party_ids.get(r.party_abbr) if r.party_abbr else None
        if r.party_abbr and party_id is None:
            # resolve.py only emits abbreviations it read out of the party table,
            # so this cannot happen through that path - but a missing row would
            # load votes against a NULL party, so it is an error, not a warning.
            raise ValueError(
                f"column {r.column_index} resolved to party {r.party_abbr!r}, which is "
                f"not in the party table. Add it to db/seed/parties.csv and re-seed."
            )
        cur.execute(
            "INSERT INTO candidate (election_id, ac_id, name_en, party_id, column_index) "
            "VALUES (%s, %s, %s, %s, %s) "
            "ON CONFLICT (election_id, name_en, party_id) DO UPDATE SET "
            "column_index = EXCLUDED.column_index, ac_id = EXCLUDED.ac_id "
            "RETURNING candidate_id",
            (election_id, ac_id, r.name, party_id, r.column_index),
        )
        ids.append(cur.fetchone()["candidate_id"])
    return ids


def load(doc: Form20Document, election_id: int, ac_id: int,
         candidate_ids: list[int], nota_id: int | None = None,
         replace: bool = False, cur=None) -> int:
    """Load a validated document. The whole thing in one transaction (C5).

    It was three before: one connection for the election lookup, a second inside
    `resolve_candidates`, and a third for the rows. With `--replace` the DELETE
    therefore committed on its own, so a failure while writing rows left the
    election with no results at all and a half-filled table. One `connection()`
    block now covers every write; psycopg commits it on a clean exit and rolls
    all of it back on any exception.

    `ac_id` is written explicitly on both tables. 0014 made it NOT NULL on
    `candidate`, `result_booth` and `result_booth_meta`, and this function never
    supplied it - so the loader could not have inserted a single row against the
    current schema. Nothing caught that, because until now no test or command in
    this repo had ever reached a database.
    """
    if cur is not None:
        # The caller owns the transaction (ingest/load_form20_tables.py writes
        # booths, candidates and published totals in the same one).
        return _write_rows(cur, doc, election_id, ac_id, candidate_ids, nota_id, replace)

    from common.db import connection

    with connection() as conn, conn.cursor() as cursor:
        return _write_rows(cursor, doc, election_id, ac_id, candidate_ids, nota_id, replace)


def _write_rows(cur, doc: Form20Document, election_id: int, ac_id: int,
                candidate_ids: list[int], nota_id: int | None, replace: bool) -> int:
    if replace:
        cur.execute("DELETE FROM result_booth WHERE election_id = %s", (election_id,))
        cur.execute("DELETE FROM result_booth_meta WHERE election_id = %s", (election_id,))

    # Collected first and written with executemany: psycopg pipelines it, so a
    # remote database (Supabase through its pooler) takes one round trip per
    # batch instead of one per cell - ~6,000 rows a Form 20.
    votes_rows, meta_rows = [], []
    for r in doc.rows:
        # strict=True: every candidate column must get a value. Rows are
        # padded at parse time, so a mismatch here means a real defect.
        for cid, votes in zip(candidate_ids, r.votes, strict=True):
            votes_rows.append((election_id, ac_id, r.ps_number, cid, votes))
        # NOTA as a candidate row, so it is inside valid_votes (D1/N7).
        if nota_id is not None and r.nota is not None:
            votes_rows.append((election_id, ac_id, r.ps_number, nota_id, r.nota))
        meta_rows.append((election_id, ac_id, r.ps_number, r.total_valid, r.nota, r.rejected,
                          r.tendered, doc.source_doc, r.page_no))
    cur.executemany(
        "INSERT INTO result_booth (election_id, ac_id, ps_number, candidate_id, votes) "
        "VALUES (%s, %s, %s, %s, %s) "
        "ON CONFLICT (election_id, ps_number, candidate_id) DO UPDATE "
        "SET votes = EXCLUDED.votes",
        votes_rows,
    )
    cur.executemany(
        "INSERT INTO result_booth_meta (election_id, ac_id, ps_number, total_valid, "
        "nota, rejected, tendered, source_doc, source_page) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (election_id, ps_number) DO UPDATE SET "
        "total_valid = EXCLUDED.total_valid, nota = EXCLUDED.nota, "
        "rejected = EXCLUDED.rejected, tendered = EXCLUDED.tendered, "
        "source_doc = EXCLUDED.source_doc, source_page = EXCLUDED.source_page, "
        "loaded_at = now()",
        meta_rows,
    )
    return len(doc.rows)


def ac_totals_by_column(doc: Form20Document, resolutions: list[resolve_mod.Resolution],
                        election_id: int, cur) -> dict[str, int]:
    """Published AC totals, keyed by the header cell each one should match.

    This is C2. The old `published_ac_totals` built keys of the form
    "Sudivya Kumar (JMM)" and `validate()` compared them against the raw header
    cells - which on a real Form 20 read "Sudivya Kumar", or the same name in
    Devanagari. Nothing ever matched, every published total was reported as "no
    such column in the document", and the only way to load anything was
    `--skip-ac-check`: the one check the README calls the gate to trust,
    routinely disabled.

    Keying off the resolution instead compares the column the resolver chose
    against the total that column's candidate published, whatever script the
    header was printed in.
    """
    # Booth rows are EVM-only; a published total includes postal ballots. Where
    # postal votes are recorded (metric 'postal', per candidate and for NOTA,
    # 0023) they are subtracted, so what is compared is the EVM figure the
    # booth columns must add up to - still at zero tolerance.
    cur.execute(
        "SELECT t.candidate_id, t.metric, t.value, p.abbr FROM result_ac_total t "
        "LEFT JOIN candidate c ON c.candidate_id = t.candidate_id "
        "LEFT JOIN party p ON p.party_id = c.party_id "
        "WHERE t.election_id = %s AND t.metric IN ('votes', 'nota', 'postal')",
        (election_id,),
    )
    by_candidate: dict[int, int] = {}
    postal: dict[int, int] = {}
    nota_total: int | None = None
    nota_postal = 0
    for row in cur.fetchall():
        if row["metric"] == "nota" and row["candidate_id"] is None:
            nota_total = row["value"]
        elif row["metric"] == "votes" and row["candidate_id"] is not None:
            by_candidate[row["candidate_id"]] = row["value"]
        elif row["metric"] == "postal" and row["candidate_id"] is not None:
            if row["abbr"] == "NOTA":
                nota_postal = row["value"]
            else:
                postal[row["candidate_id"]] = row["value"]
    by_candidate = {cid: v - postal.get(cid, 0) for cid, v in by_candidate.items()}
    if nota_total is not None:
        nota_total -= nota_postal

    totals: dict[str, int] = {}
    for r in resolutions:
        header = doc.candidate_columns[r.column_index]
        if r.candidate_id is not None and r.candidate_id in by_candidate:
            totals[header] = by_candidate[r.candidate_id]

    unchecked = [doc.candidate_columns[r.column_index] for r in resolutions
                 if doc.candidate_columns[r.column_index] not in totals]

    # NOTA is a tail column, not a candidate column: `TAIL_LABELS` matches it
    # and `classify_header` stops the candidate run at the first tail column, so
    # a NOTA header never reaches the resolver. `validate()` exposes the summed
    # tail under the literal key "NOTA", which is the key the published total
    # has to be filed under for the comparison to happen at all.
    if nota_total is not None:
        if "nota" in doc.tail_order:
            totals["NOTA"] = nota_total
        else:
            log.warning("a published NOTA total of %d exists for this election but the "
                        "document has no NOTA column, so it cannot be cross-checked",
                        nota_total)
    if unchecked:
        log.warning(
            "%d of %d column(s) have no published AC total, so their booth sums are "
            "not cross-checked: %s", len(unchecked), len(resolutions),
            ", ".join(repr(u) for u in unchecked[:8]),
        )
    return totals


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Parse and load a Form 20 PDF")
    # A path, --doc SHA256 or --key STORAGE_KEY. --doc is what makes this
    # runnable from a laptop against a remote DATABASE_URL: the source_doc row
    # says which backend holds the bytes, so no local raw/ tree is needed.
    add_document_arguments(ap)
    ap.add_argument("--election", required=True, help="election label, e.g. VS-2024")
    add_ac_argument(ap)
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
    from common.db import cursor

    doc = parse_pdf(pdf_path, force=args.force)
    log.info("%s: %d row(s), %d candidate column(s), method=%s",
             pdf_path.name, len(doc.rows), len(doc.candidate_columns), doc.method)
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

    # Resolution comes before validation, and both come before any write. The
    # AC-total check cannot be keyed on anything until the columns are known
    # (C2), and master prompt 3.1 step 6 says an unresolved column aborts the
    # load - so there is no point validating arithmetic for a document we do not
    # understand the columns of.
    try:
        with cursor() as cur:
            scope = resolve_election(cur, args.election, ac_number=args.ac)
            log.info("loading into %s", scope)
            resolutions = resolve_columns_for(doc, scope.election_id, cur)
            ac_totals = (None if args.skip_ac_check
                         else ac_totals_by_column(doc, resolutions, scope.election_id, cur)
                         or None)
    except UnresolvedColumns as exc:
        try:
            queue_unresolved(doc, exc.failures, scope.ac_id)
            log.error("queued %d column(s) for manual review "
                      "(review_queue kind='form20_column')", len(exc.failures))
        except Exception as queue_exc:
            log.error("could not write to review_queue: %s", queue_exc)
        log.error("NOT LOADING %s - every column must resolve to a candidate or a "
                  "party first (master prompt 3.1)", pdf_path.name)
        _advance(provenance, "failed")
        return 1
    except (AmbiguousScope, ValueError) as exc:
        log.error("%s", exc)
        return 2

    if ac_totals:
        log.info("checking booth sums against %d published AC total(s)", len(ac_totals))
    elif not args.skip_ac_check:
        log.warning("no published AC totals for %s - loading without the cross-check. "
                    "Add them to db/seed/ac_totals.csv.", args.election)

    errors = validate(doc, ac_totals)
    if errors:
        log.error("%d validation error(s):", len(errors))
        for e in errors[:20]:
            log.error("  %s", e.detail)
        if len(errors) > 20:
            log.error("  ... and %d more", len(errors) - 20)
        try:
            queue_errors(errors, scope.ac_id)
            log.error("queued for manual review (review_queue kind='form20_row', "
                      "AC %s) - see /acs/%s/admin/review-queue",
                      scope.ac_number, scope.ac_number)
        except Exception as exc:
            log.error("could not write to review_queue: %s", exc)
        log.error("NOT LOADING %s - fix the rows above and re-run", pdf_path.name)
        _advance(provenance, "failed")
        return 1

    log.info("validation passed")
    _advance(provenance, "validated")
    if args.load and not args.dry_run:
        with cursor() as cur:
            candidate_ids = candidate_ids_for(resolutions, scope.election_id,
                                              scope.ac_id, cur)
            # Only when the document actually prints a NOTA column. A Form 20
            # from before 2013 has none, and inventing a zero row would put a
            # contestant that did not exist on the ballot into the denominator.
            nota_id = (nota_candidate_id(scope.election_id, scope.ac_id, cur)
                       if "nota" in doc.tail_order else None)
        if nota_id is None:
            log.warning("%s prints no NOTA column, so no NOTA row is loaded. Check the "
                        "document: every election from 2013 onwards has one.",
                        doc.source_doc)
        n = load(doc, scope.election_id, scope.ac_id, candidate_ids, nota_id=nota_id,
                 replace=args.replace)
        log.info("loaded %d booth row(s) for %s", n, scope)
        _advance(provenance, "loaded")
        log.info("now run: python -m ingest.crosswalk --ac %s --election %s",
                 scope.ac_number, args.election)
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
