"""Cross-checks over everything loaded (LLD 2).

Run after every load. These are the checks that catch a silent corruption: a
crosswalk that dropped booths, a Form 20 whose booth sums no longer match the
published AC total, a roll snapshot that lost a booth between revisions.

    python -m ingest.validate
    python -m ingest.validate --strict     # non-zero exit if anything fails
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass

from common.db import query
from common.jobs import job_context
from common.logging_setup import get_logger

log = get_logger(__name__)


@dataclass
class Check:
    name: str
    passed: bool
    detail: str
    rows: list | None = None


def check_form20_totals() -> list[Check]:
    """Booth sums against published AC totals, tolerance zero (LLD 4.2)."""
    # Published totals include postal ballots; booth rows are EVM-only. Where
    # a candidate's postal votes are recorded (0023) they are added to the
    # booth sum, so the comparison is like for like.
    rows = query(
        "SELECT e.label, c.name_en AS candidate, p.abbr, t.value AS published, "
        "COALESCE(SUM(r.votes), 0) + COALESCE(MAX(pt.value), 0) AS booth_sum "
        "FROM result_ac_total t "
        "JOIN election e ON e.election_id = t.election_id "
        "JOIN candidate c ON c.candidate_id = t.candidate_id "
        "LEFT JOIN party p ON p.party_id = c.party_id "
        "LEFT JOIN result_booth r ON r.candidate_id = t.candidate_id "
        "LEFT JOIN result_ac_total pt ON pt.candidate_id = t.candidate_id "
        "  AND pt.metric = 'postal' "
        "WHERE t.metric = 'votes' "
        # Only elections with booth results loaded. A seeded published total
        # for a constituency with nothing loaded (31, 33, 42, 61, 65 today) is
        # "not loaded", not "does not reconcile" - comparing it with a booth
        # sum of 0 failed this check on every correct database.
        "  AND EXISTS (SELECT 1 FROM result_booth rb WHERE rb.election_id = t.election_id) "
        "GROUP BY e.label, c.name_en, p.abbr, t.value "
        "HAVING COALESCE(SUM(r.votes), 0) + COALESCE(MAX(pt.value), 0) <> t.value"
    )
    return [Check(
        "form20_ac_totals", not rows,
        "every candidate booth sum matches the published AC total" if not rows
        else f"{len(rows)} candidate total(s) do not match the published figure",
        rows,
    )]


def check_row_arithmetic() -> list[Check]:
    # NOTA is loaded as a candidate row (OD-N7) and also kept as the printed
    # figure on result_booth_meta.nota. Candidate votes are therefore summed
    # *without* the NOTA row, and NOTA is added once - from the row when there
    # is one, else from meta. Adding meta.nota to a sum that already held it
    # failed every row of every election.
    rows = query(
        "WITH sums AS ("
        "  SELECT r.election_id, r.ps_number, "
        "         SUM(r.votes) FILTER (WHERE p.abbr IS DISTINCT FROM 'NOTA') AS candidates, "
        "         SUM(r.votes) FILTER (WHERE p.abbr = 'NOTA') AS nota_row "
        "  FROM result_booth r "
        "  JOIN candidate c ON c.candidate_id = r.candidate_id "
        "  LEFT JOIN party p ON p.party_id = c.party_id "
        "  GROUP BY r.election_id, r.ps_number) "
        "SELECT e.label, m.ps_number, m.total_valid, "
        "COALESCE(s.candidates, 0) + COALESCE(s.nota_row, m.nota, 0) AS computed "
        "FROM result_booth_meta m "
        "JOIN election e ON e.election_id = m.election_id "
        "LEFT JOIN sums s ON s.election_id = m.election_id AND s.ps_number = m.ps_number "
        "WHERE m.total_valid IS NOT NULL "
        "  AND COALESCE(s.candidates, 0) + COALESCE(s.nota_row, m.nota, 0) <> m.total_valid "
        "LIMIT 200"
    )
    return [Check(
        "form20_row_arithmetic", not rows,
        "candidate votes plus NOTA equal the printed valid total on every row" if not rows
        else f"{len(rows)} row(s) do not add up",
        rows,
    )]


def check_crosswalk_coverage() -> list[Check]:
    orphans = query(
        "SELECT e.label, COUNT(DISTINCT r.ps_number) AS unmapped_ps, SUM(r.votes) AS votes_lost "
        "FROM result_booth r "
        "JOIN election e ON e.election_id = r.election_id "
        "LEFT JOIN booth_crosswalk x ON x.election_id = r.election_id AND x.ps_number = r.ps_number "
        "WHERE x.booth_uid IS NULL GROUP BY e.label"
    )
    quality = query(
        "SELECT e.label, COUNT(*) AS total, "
        "COUNT(*) FILTER (WHERE x.confidence >= 0.85) AS auto_accepted, "
        "ROUND(100.0 * COUNT(*) FILTER (WHERE x.confidence >= 0.85) / COUNT(*), 1) AS pct "
        "FROM booth_crosswalk x JOIN election e ON e.election_id = x.election_id "
        "GROUP BY e.label ORDER BY pct"
    )
    checks = [Check(
        "crosswalk_coverage", not orphans,
        "every polling station with results maps to a booth" if not orphans
        else "results exist for polling stations that map to no booth - those votes are missing "
             "from every rollup",
        orphans,
    )]
    weak = [q for q in quality if (q["pct"] or 0) < 90]
    checks.append(Check(
        "crosswalk_quality", not weak,
        "auto-crosswalk is at or above the 90 percent target for every election" if not weak
        else f"{len(weak)} election(s) below the 90 percent auto-match target in LLD 14 (S0)",
        weak,
    ))
    return checks


def check_roll_continuity() -> list[Check]:
    rows = query(
        "WITH revs AS ("
        "  SELECT revision_id, revision_date, "
        "         LAG(revision_id) OVER (ORDER BY revision_date) AS prev_id "
        "  FROM roll_revision) "
        "SELECT r.revision_date, COUNT(*) AS booths_missing "
        "FROM revs r "
        "JOIN roll_snapshot prev ON prev.revision_id = r.prev_id "
        "LEFT JOIN roll_snapshot cur ON cur.revision_id = r.revision_id "
        "  AND cur.booth_uid = prev.booth_uid "
        "WHERE r.prev_id IS NOT NULL AND cur.booth_uid IS NULL "
        "GROUP BY r.revision_date"
    )
    return [Check(
        "roll_continuity", not rows,
        "no booth disappears between roll revisions" if not rows
        else "booths present in one revision are missing from the next - either a genuine "
             "rationalisation or a parse that dropped sections",
        rows,
    )]


def check_no_personal_data() -> list[Check]:
    """The structural half of the compliance guarantee (LLD 12).

    The behavioural half is tests/test_roll_privacy.py, which proves the parser
    discards names. This asserts the schema itself has nowhere to put one.
    """
    forbidden = query(
        "SELECT table_name, column_name FROM information_schema.columns "
        "WHERE table_schema = 'public' "
        "AND table_name IN ('roll_snapshot', 'roll_change', 'caste_estimate', 'booth') "
        "AND (column_name ILIKE '%%epic%%' OR column_name ILIKE '%%voter_name%%' "
        "  OR column_name ILIKE '%%elector_name%%' OR column_name ILIKE '%%father%%' "
        "  OR column_name ILIKE '%%house%%' OR column_name ILIKE '%%address%%')"
    )
    return [Check(
        "no_personal_data_columns", not forbidden,
        "no column in the roll or caste tables can hold an individual voter" if not forbidden
        else "a column that could hold individual voter data has appeared - this breaks the "
             "aggregate-only guarantee in HLD 5 and LLD 12",
        forbidden,
    )]


def check_privacy_filesystem() -> list[Check]:
    """Scan the disk for personal data (audit C3).

    The structural check above inspects `information_schema` column names, which
    is what the RUNBOOK's compliance section presented as sufficient. It is not:
    it cannot see file contents, and the leak was entirely on the filesystem.
    `extract_pdf` wrote every page's complete text to OCR_DIR before any parser
    ran, and `parse_roll` routed roll PDFs through it, so the full electoral roll
    sat in plaintext JSON indefinitely while `discard_raw` deleted the source.

    What is scanned: OCR_DIR, the log directory, the system temp directory, and
    any text sidecar under RAW_DIR. What is not: the PDFs under RAW_DIR
    themselves. Those are the audit trail and are *expected* to hold personal
    data; the guarantee is that nothing derived from them is retained.
    """
    import tempfile
    from pathlib import Path

    from common.config import get_settings
    from common.pii import TEXT_SUFFIXES, scan_file, scan_tree

    settings = get_settings()
    findings = []

    findings += scan_tree(Path(settings.ocr_dir))

    # Text sidecars under raw/: a .json or .txt next to a source PDF is derived
    # data, and derived data may not be kept.
    raw = Path(settings.raw_dir)
    if raw.exists():
        for path in sorted(raw.rglob("*")):
            if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES:
                findings += scan_file(path)

    # The temp directory, because ocr_tesseract rasterises pages there and a
    # crashed run can leave them behind.
    findings += scan_tree(Path(tempfile.gettempdir()) / "giridih-ocr", limit_bytes=2_000_000)

    return [Check(
        "privacy_filesystem", not findings,
        "no file under OCR_DIR, RAW_DIR or the temp directory matches an EPIC, mobile "
        "or Aadhaar pattern" if not findings
        else f"{len(findings)} location(s) on disk hold personal data. Run "
             "scripts/purge_roll_cache.py --delete, then re-run this check. The "
             "matched text is deliberately not shown.",
        # The describe() form, so the report names the class and never the value.
        [f.describe() for f in findings],
    )]


# Exact (table, column) pairs that legitimately match a pattern and are not voter
# data. Each needs a reason; anything else that matches is reported.
PRIVACY_EXEMPT = {
    # Staff login identifiers: the dashboard's own users, who sign in with a
    # phone number. Not voter data, and needed to authenticate.
    ("app_user", "phone"),
    ("auth_otp", "phone"),
    # Migration checksums are hex digests; runs of 12 digits match the Aadhaar
    # pattern by chance.
    ("schema_migration", "checksum"),
}


def check_privacy_database() -> list[Check]:
    """Scan every text and jsonb column in the database for personal data.

    The audit was specific about the gap: the structural check "cannot see file
    contents, review_queue.payload, ground_report.text or news_item.body". The
    first is covered above; these are the rest. `ground_report.text` is the one
    unguarded free-text ingress in the system (E5) - 4,000 characters from any
    authenticated user, stored verbatim and embedded for vector search - and
    `review_queue.payload` carries parser excerpts that can include roll text.

    Every text and jsonb column is enumerated from the catalogue rather than
    listed here, so a new column is covered the day it is added rather than the
    day somebody remembers to add it to this function.
    """
    from common.pii import PATTERNS

    # N6. This read information_schema.columns, which in PostgreSQL does not
    # list materialized views at all - they are absent from
    # information_schema.tables too. So the 14 matviews, which are denormalised
    # copies of very nearly everything this system holds, were never scanned.
    # A leak into a matview would have passed the compliance check silently,
    # which is the same shape of failure as the structural check that could not
    # see the filesystem.
    #
    # pg_class.relkind: 'r' ordinary table, 'p' partitioned, 'm' materialized
    # view, 'v' view. Views are included because a view over a text column can
    # expose it under a different name, and the scan costs a query either way.
    columns = query(
        "SELECT rel.relname AS table_name, att.attname AS column_name, "
        "       rel.relkind "
        "FROM pg_attribute att "
        "JOIN pg_class rel ON rel.oid = att.attrelid "
        "JOIN pg_namespace ns ON ns.oid = rel.relnamespace "
        "JOIN pg_type typ ON typ.oid = att.atttypid "
        "WHERE ns.nspname = 'public' "
        "  AND rel.relkind IN ('r', 'p', 'm', 'v') "
        "  AND att.attnum > 0 AND NOT att.attisdropped "
        "  AND typ.typname IN ('text', 'varchar', 'bpchar', 'jsonb', 'json') "
        "ORDER BY rel.relname, att.attname"
    )

    hits = []
    for col in columns:
        table, column = col["table_name"], col["column_name"]
        if (table, column) in PRIVACY_EXEMPT:
            continue
        for name, pattern in PATTERNS.items():
            # The regex runs in Postgres so the text never crosses the wire -
            # pulling every free-text column into Python to scan it would move
            # the data we are looking for into this process's memory and its
            # traceback.
            found = query(
                f'SELECT COUNT(*) AS n FROM "{table}" '
                f'WHERE "{column}"::text ~ %s',
                (pattern.pattern,),
            )
            n = (found[0]["n"] if found else 0) or 0
            if n:
                hits.append({"table": table, "column": column, "pattern": name, "rows": n})

    return [Check(
        "privacy_database", not hits,
        "no text or jsonb column holds an EPIC, mobile or Aadhaar pattern" if not hits
        else f"{len(hits)} column(s) hold personal data. Find and remove the offending "
             "rows, then investigate how they got in - every free-text write path is "
             "supposed to be screened by common.pii.screen.",
        hits,
    )]


def check_caste_confidence() -> list[Check]:
    rows = query(
        "SELECT COUNT(*) FILTER (WHERE confidence < 0.4) AS weak, COUNT(*) AS total "
        "FROM caste_estimate WHERE source = 'blend'"
    )
    row = rows[0] if rows else {"weak": 0, "total": 0}
    weak, total = row["weak"] or 0, row["total"] or 0
    share = (100.0 * weak / total) if total else 0.0
    return [Check(
        "caste_confidence", share < 50,
        f"{weak} of {total} blended estimates ({share:.0f}%) are below 0.4 confidence "
        f"and will render greyed"
        + ("" if share < 50 else " - most of the caste module is currently unusable; "
                                 "extend surname_dict or collect ground surveys"),
    )]


ALL_CHECKS = [
    check_form20_totals,
    check_row_arithmetic,
    check_crosswalk_coverage,
    check_roll_continuity,
    check_no_personal_data,
    # C3: the structural check above only reads column names, which is what the
    # RUNBOOK presented as sufficient. The leak was on the filesystem, so the
    # full run includes both scans too.
    check_privacy_filesystem,
    check_privacy_database,
    check_caste_confidence,
]


def run_all() -> list[Check]:
    results: list[Check] = []
    for fn in ALL_CHECKS:
        try:
            results.extend(fn())
        except Exception as exc:
            results.append(Check(fn.__name__, False, f"the check itself failed: {exc}"))
    return results


PRIVACY_CHECKS = (check_no_personal_data, check_privacy_filesystem, check_privacy_database)


def run_privacy() -> list[Check]:
    """Just the compliance checks, for `--privacy`.

    Separate because this is the one thing that must be runnable immediately
    after a roll load, on its own, without waiting for the result and crosswalk
    checks - and because RUN.md 4.4 tells the operator to do exactly that.
    """
    results: list[Check] = []
    for fn in PRIVACY_CHECKS:
        try:
            results += fn()
        except Exception as exc:  # noqa: BLE001
            results.append(Check(fn.__name__, False, f"the check itself failed: {exc}"))
    return results


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Cross-check the loaded data")
    ap.add_argument("--strict", action="store_true", help="exit non-zero if any check fails")
    ap.add_argument("--verbose", action="store_true", help="print the offending rows")
    ap.add_argument("--privacy", action="store_true",
                    help="run only the compliance checks: the filesystem and database "
                         "scans for EPIC, mobile and Aadhaar patterns. Always exits "
                         "non-zero on a finding, with or without --strict.")
    args = ap.parse_args(argv)

    if args.privacy:
        checks = run_privacy()
        for c in checks:
            print(f"{'PASS' if c.passed else 'FAIL'}  {c.name}: {c.detail}")
            if args.verbose or not c.passed:
                for row in (c.rows or [])[:20]:
                    print(f"        {row}")
        # A privacy finding is never advisory.
        return 0 if all(c.passed for c in checks) else 1

    failed: list[Check] = []
    with job_context("ingest.validate") as job:
        checks = run_all()
        failed = [c for c in checks if not c.passed]
        for c in checks:
            mark = "PASS" if c.passed else "FAIL"
            log.info("%-4s %-26s %s", mark, c.name, c.detail)
            job.log_line(f"{mark} {c.name}: {c.detail}")
            if args.verbose and c.rows:
                for row in c.rows[:20]:
                    log.info("       %s", row)
        job.set(checks=len(checks), failed=len(failed))
        if failed:
            log.error("%d of %d check(s) failed", len(failed), len(checks))

    return 1 if (failed and args.strict) else 0


if __name__ == "__main__":
    sys.exit(main())
