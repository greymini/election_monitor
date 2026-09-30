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
    rows = query(
        "SELECT e.label, c.name_en AS candidate, p.abbr, t.value AS published, "
        "COALESCE(SUM(r.votes), 0) AS booth_sum "
        "FROM result_ac_total t "
        "JOIN election e ON e.election_id = t.election_id "
        "JOIN candidate c ON c.candidate_id = t.candidate_id "
        "LEFT JOIN party p ON p.party_id = c.party_id "
        "LEFT JOIN result_booth r ON r.candidate_id = t.candidate_id "
        "WHERE t.metric = 'votes' "
        "GROUP BY e.label, c.name_en, p.abbr, t.value "
        "HAVING COALESCE(SUM(r.votes), 0) <> t.value"
    )
    return [Check(
        "form20_ac_totals", not rows,
        "every candidate booth sum matches the published AC total" if not rows
        else f"{len(rows)} candidate total(s) do not match the published figure",
        rows,
    )]


def check_row_arithmetic() -> list[Check]:
    rows = query(
        "SELECT e.label, m.ps_number, m.total_valid, "
        "COALESCE(SUM(r.votes), 0) + COALESCE(m.nota, 0) AS computed "
        "FROM result_booth_meta m "
        "JOIN election e ON e.election_id = m.election_id "
        "LEFT JOIN result_booth r ON r.election_id = m.election_id AND r.ps_number = m.ps_number "
        "WHERE m.total_valid IS NOT NULL "
        "GROUP BY e.label, m.ps_number, m.total_valid, m.nota "
        "HAVING COALESCE(SUM(r.votes), 0) + COALESCE(m.nota, 0) <> m.total_valid "
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Cross-check the loaded data")
    ap.add_argument("--strict", action="store_true", help="exit non-zero if any check fails")
    ap.add_argument("--verbose", action="store_true", help="print the offending rows")
    args = ap.parse_args(argv)

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
