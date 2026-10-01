"""Fetch panchayat and municipal results from SEC Jharkhand (HLD 4).

Same generic-discovery approach as fetch_ceo: the SEC portal publishes results
in a different layout each cycle, so hardcoding URLs would break every time.

Panchayat elections are held without party symbols. Nothing here assigns a
party - `local_result.tagged_party_id` is filled in by hand afterwards and
`tag_source` records who decided and why.

    python -m ingest.fetch_sec --discover <url> --download
    python -m ingest.fetch_sec --load-csv results.csv --election PANCHAYAT-2022
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from common.jobs import job_context
from common.logging_setup import get_logger
from common.textnorm import alias_key, normalize_text, parse_int
from ingest.fetch_ceo import download, list_remote_documents

log = get_logger(__name__)

SEAT_TYPES = {"mukhiya", "ZP", "panchayat_samiti", "ward"}


def load_csv(path: Path, election_label: str, ac_number: int, dry_run: bool = False) -> dict:
    """Load hand-transcribed local results.

    SEC PDFs vary too much for a reliable parser, and the volume is small (a few
    hundred seats), so transcription into a CSV with a fixed header is both
    faster and more accurate than fighting the layout.

    Columns: seat_type, seat_name, area_name, winner, runner_up, votes,
             runner_up_votes, tagged_party, tag_source, tag_confidence

    Scoped to one AC. It looked the election up by label alone - but since 0014
    every AC has its own PANCHAYAT-2022 row, so it took whichever came first -
    and inserted without `ac_id`, which `local_result` allows to be NULL. A load
    therefore "succeeded" into another constituency's election with no AC at
    all, and the data-health tile, which counts rows for this AC, kept saying
    "not loaded" however many rows went in. Area names are matched only among
    this AC's areas for the same reason.
    """
    from common.db import connection

    with path.open(encoding="utf-8-sig", newline="") as fh:
        rows = [r for r in csv.DictReader(fh) if any(v for v in r.values() if v)]

    stats = {"rows": len(rows), "loaded": 0, "unmatched_area": 0, "bad_seat_type": 0}
    if dry_run:
        for r in rows[:10]:
            log.info("  %s | %s | %s", r.get("seat_type"), r.get("seat_name"), r.get("winner"))
        return stats

    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT e.election_id, e.ac_id FROM election e JOIN ac a ON a.ac_id = e.ac_id "
            "WHERE e.label = %s AND a.ac_number = %s", (election_label, ac_number))
        election = cur.fetchone()
        if election is None:
            raise ValueError(f"AC {ac_number} has no election labelled {election_label!r}")
        eid, ac_id = election["election_id"], election["ac_id"]

        cur.execute("SELECT abbr, party_id FROM party")
        parties = {r["abbr"].upper(): r["party_id"] for r in cur.fetchall()}

        for r in rows:
            seat_type = (r.get("seat_type") or "").strip()
            if seat_type not in SEAT_TYPES:
                stats["bad_seat_type"] += 1
                log.warning("skipping %r: seat_type must be one of %s",
                            r.get("seat_name"), ", ".join(sorted(SEAT_TYPES)))
                continue

            area_id = None
            area_name = normalize_text(r.get("area_name"))
            if area_name:
                cur.execute(
                    "SELECT aa.area_id FROM area_alias aa JOIN area ar ON ar.area_id = aa.area_id "
                    "WHERE aa.alias = %s AND ar.ac_id = %s", (alias_key(area_name), ac_id))
                hit = cur.fetchone()
                if hit:
                    area_id = hit["area_id"]
                else:
                    stats["unmatched_area"] += 1

            votes = parse_int(r.get("votes"))
            runner_votes = parse_int(r.get("runner_up_votes"))
            margin = (votes - runner_votes) if (votes is not None and runner_votes is not None) else None
            tagged = (r.get("tagged_party") or "").strip().upper()

            cur.execute(
                "INSERT INTO local_result (election_id, ac_id, seat_type, area_id, seat_name, "
                "winner, runner_up, tagged_party_id, tag_source, tag_confidence, votes, "
                "runner_up_votes, margin, source_doc) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (election_id, seat_type, seat_name) DO UPDATE SET "
                "winner = EXCLUDED.winner, runner_up = EXCLUDED.runner_up, "
                "tagged_party_id = EXCLUDED.tagged_party_id, tag_source = EXCLUDED.tag_source, "
                "tag_confidence = EXCLUDED.tag_confidence, votes = EXCLUDED.votes, "
                "runner_up_votes = EXCLUDED.runner_up_votes, margin = EXCLUDED.margin",
                (eid, ac_id, seat_type, area_id, normalize_text(r.get("seat_name")),
                 normalize_text(r.get("winner")), normalize_text(r.get("runner_up")),
                 parties.get(tagged), r.get("tag_source") or None,
                 float(r["tag_confidence"]) if r.get("tag_confidence") else None,
                 votes, runner_votes, margin, path.name),
            )
            stats["loaded"] += 1
    return stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="SEC Jharkhand results")
    ap.add_argument("--discover", metavar="URL", help="page to scan for result PDFs")
    ap.add_argument("--download", action="store_true")
    ap.add_argument("--load-csv", metavar="PATH", help="load transcribed results")
    ap.add_argument("--election", help="election label for --load-csv, e.g. PANCHAYAT-2022")
    ap.add_argument("--ac", type=int, help="AC number the results belong to (needed with --load-csv)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    if args.load_csv:
        if not args.election or args.ac is None:
            ap.error("--load-csv needs --election and --ac")
        with job_context("ingest.fetch_sec.load_csv", election=args.election, ac=args.ac) as job:
            stats = load_csv(Path(args.load_csv), args.election, args.ac, args.dry_run)
            job.set(**stats)
            job.log_line(str(stats))
            if stats["unmatched_area"]:
                log.warning("%d row(s) could not be matched to a panchayat or ward - "
                            "add the spellings to area_alias", stats["unmatched_area"])
        return 0

    if not args.discover:
        ap.error("give --discover URL or --load-csv PATH")

    with job_context("ingest.fetch_sec", page=args.discover) as job:
        docs = list_remote_documents(args.discover, kind=None, ac_only=False)
        job.log_line(f"found {len(docs)} PDF link(s)")
        for doc in docs[:40]:
            log.info("  %-52s %s", doc["filename"][:52], doc["label"][:60])
        if args.download:
            for doc in docs:
                try:
                    result = download(doc["url"], "sec_result")
                    if not result["skipped"]:
                        job.log_line(f"downloaded {result['uri']}")
                except Exception as exc:
                    log.error("failed %s: %s", doc["url"], exc)
        else:
            log.info("discovery only. Re-run with --download to fetch these.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
