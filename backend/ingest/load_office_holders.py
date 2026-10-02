"""Load current panchayat office-holders into local_office_holder.

Reads a CSV produced by scripts/normalize_panchayat_members.py and upserts
rows into local_office_holder, resolving area names via area_alias.

CSV columns: office, name, area_name, term_start, term_end, notes, tagged_party, tag_source

Valid office values (matching the DB CHECK constraint):
  mukhiya  ward  panchayat_samiti  zila_parishad  mayor  chairperson

Usage:
    python -m ingest.load_office_holders --csv data_giridih/panchayat_members_load.csv --ac 32 --dry-run
    python -m ingest.load_office_holders --csv data_giridih/panchayat_members_load.csv --ac 32
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from common.jobs import job_context
from common.logging_setup import get_logger
from common.textnorm import alias_key, normalize_text

log = get_logger(__name__)

VALID_OFFICES = {"mukhiya", "ward", "panchayat_samiti", "zila_parishad", "mayor", "chairperson"}


def load_csv(path: Path, ac_number: int, dry_run: bool = False) -> dict:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        rows = [r for r in csv.DictReader(fh) if any(v for v in r.values() if v)]

    stats = {
        "rows": len(rows),
        "loaded": 0,
        "updated": 0,
        "skipped_bad_office": 0,
        "skipped_no_name": 0,
        "unmatched_area": 0,
    }

    if dry_run:
        for r in rows[:15]:
            log.info("  %s | %-30s | %s", r.get("office", ""), r.get("name", ""), r.get("area_name", ""))
        log.info("dry-run: %d rows would be processed", len(rows))
        return stats

    from common.db import connection
    from ingest.acscope import resolve_ac

    with connection() as conn, conn.cursor() as cur:
        ac_id, ac_name = resolve_ac(cur, ac_number)
        log.info("loading into AC %s (%s)", ac_number, ac_name)

        party_map: dict[str, int] = {}
        cur.execute("SELECT abbr, party_id FROM party")
        for p in cur.fetchall():
            party_map[p["abbr"].upper()] = p["party_id"]

        unmatched_areas: list[str] = []

        for r in rows:
            name = normalize_text(r.get("name") or "")
            if not name:
                stats["skipped_no_name"] += 1
                continue

            office = (r.get("office") or "").strip().lower()
            if office not in VALID_OFFICES:
                stats["skipped_bad_office"] += 1
                log.warning("skipping %r: office %r not in %s", name, office, sorted(VALID_OFFICES))
                continue

            area_name = normalize_text(r.get("area_name") or "")
            area_id = None
            if area_name:
                cur.execute(
                    "SELECT aa.area_id FROM area_alias aa "
                    "JOIN area a ON a.area_id = aa.area_id "
                    "WHERE aa.alias = %s AND a.ac_id = %s",
                    (alias_key(area_name), ac_id),
                )
                hit = cur.fetchone()
                if hit:
                    area_id = hit["area_id"]
                else:
                    stats["unmatched_area"] += 1
                    if area_name not in unmatched_areas:
                        unmatched_areas.append(area_name)

            tagged_abbr = (r.get("tagged_party") or "").strip().upper()
            tagged_id = party_map.get(tagged_abbr)
            tag_source = (r.get("tag_source") or "").strip() or None

            # tag_needs_a_source constraint: tagged_party requires tag_source
            if tagged_id and not tag_source:
                log.warning(
                    "skipping party tag for %r: tagged_party set but tag_source is empty "
                    "(constraint tag_needs_a_source). Set tag_source in the CSV.",
                    name,
                )
                tagged_id = None

            term_start = r.get("term_start") or None
            term_end = r.get("term_end") or None
            notes = normalize_text(r.get("notes") or "") or None

            cur.execute(
                """
                INSERT INTO local_office_holder
                    (ac_id, area_id, office, name, tagged_party_id, tag_source,
                     term_start, term_end, notes)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (ac_id, area_id, office, name, tagged_id, tag_source,
                 term_start, term_end, notes),
            )
            if cur.rowcount > 0:
                stats["loaded"] += 1
            else:
                stats["updated"] += 1

        if unmatched_areas:
            log.warning(
                "%d area name(s) could not be matched to a panchayat — "
                "add spellings to db/seed/area_aliases.csv and re-run:\n  %s",
                len(unmatched_areas),
                "\n  ".join(unmatched_areas[:20]),
            )

    return stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Load panchayat office-holders")
    ap.add_argument("--csv", required=True, metavar="PATH", help="CSV file to load")
    ap.add_argument("--ac", type=int, required=True, help="AC number, e.g. 32 for Giridih")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    with job_context("ingest.load_office_holders", csv=args.csv, ac=args.ac) as job:
        stats = load_csv(Path(args.csv), args.ac, args.dry_run)
        job.set(**stats)
        job.log_line(str(stats))
        if stats.get("unmatched_area"):
            log.warning(
                "%d row(s) had unmatched areas — add spellings to area_aliases.csv",
                stats["unmatched_area"],
            )
        log.info("done: %s", stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
