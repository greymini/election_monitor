"""Load hand-prepared CSVs into the three tables no parser fills.

    python -m ingest.load_csv demography          <csv> --ac 32
    python -m ingest.load_csv candidate_profile   <csv> --ac 32
    python -m ingest.load_csv local_office_holder <csv> --ac 32
    ... add --dry-run to check a file without writing anything

**Why this exists.** RUN.md, the LLD and three screens (data health, Candidates,
Local politics) all named `python -m ingest.load_csv ...`, and the module had
never been written - every one of those commands failed with "No module named
ingest.load_csv". The Census tile also named a kind, `area_indicator`, whose
table does not exist; the tile counts `demography`, so that is the kind here.

Every kind is scoped to one AC, and area and candidate names are matched only
among that AC's rows, for the reason ingest/fetch_sec.py gives: an unscoped
name lookup silently files a row under another constituency. A row that does not
match is reported and skipped, never guessed at - add the spelling to
`area_alias` and load again. Loads are idempotent: re-running a file updates the
same rows.

Column formats (header row required, UTF-8):

  demography           area_name, census_year, population, sc, st, literate,
                       main_workers, households
  candidate_profile    election, candidate_name, incumbent, contests_prior,
                       wins_prior, prev_party, deposit_forfeited, assets_declared,
                       liabilities, criminal_cases, criminal_serious, education,
                       age, profession, affidavit_url, myneta_id, source
  local_office_holder  office, name, area_name, tagged_party, tag_source,
                       term_start, term_end, notes

Booleans are true/false/yes/no/1/0; blanks are NULL, never zero.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from common.jobs import job_context
from common.logging_setup import get_logger
from common.textnorm import alias_key, normalize_text, parse_int

log = get_logger(__name__)

KINDS = ("demography", "candidate_profile", "local_office_holder")
OFFICES = {"mukhiya", "ward", "panchayat_samiti", "zila_parishad", "mayor", "chairperson"}


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return [
            {k.strip(): (v or "").strip() for k, v in r.items() if k}
            for r in csv.DictReader(fh) if any((v or "").strip() for v in r.values())
        ]


def _bool(value: str) -> bool | None:
    v = value.strip().lower()
    if not v:
        return None
    if v in {"1", "true", "yes", "y"}:
        return True
    if v in {"0", "false", "no", "n"}:
        return False
    raise ValueError(f"not a boolean: {value!r}")


def _text(value: str) -> str | None:
    return normalize_text(value) or None


def _area_id(cur, ac_id: int, name: str) -> int | None:
    """An area of this AC by any known spelling, or None."""
    if not name:
        return None
    cur.execute(
        "SELECT aa.area_id FROM area_alias aa JOIN area a ON a.area_id = aa.area_id "
        "WHERE aa.alias = %s AND a.ac_id = %s",
        (alias_key(normalize_text(name)), ac_id))
    hit = cur.fetchone()
    if hit:
        return hit["area_id"]
    cur.execute("SELECT area_id FROM area WHERE ac_id = %s AND lower(name_en) = lower(%s)",
                (ac_id, normalize_text(name)))
    hit = cur.fetchone()
    return hit["area_id"] if hit else None


def _load_demography(cur, ac_id: int, rows: list[dict], stats: dict) -> None:
    for r in rows:
        area_id = _area_id(cur, ac_id, r.get("area_name", ""))
        year = parse_int(r.get("census_year"))
        if area_id is None or year is None:
            stats["skipped"] += 1
            log.warning("skipping %r: %s", r.get("area_name"),
                        "unknown area" if area_id is None else "no census_year")
            continue
        values = [parse_int(r.get(k)) for k in
                  ("population", "sc", "st", "literate", "main_workers", "households")]
        cur.execute(
            "INSERT INTO demography (area_id, census_year, population, sc, st, literate, "
            "main_workers, households) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (area_id, census_year) DO UPDATE SET population = EXCLUDED.population, "
            "sc = EXCLUDED.sc, st = EXCLUDED.st, literate = EXCLUDED.literate, "
            "main_workers = EXCLUDED.main_workers, households = EXCLUDED.households",
            (area_id, year, *values))
        stats["loaded"] += 1


def _load_candidate_profile(cur, ac_id: int, rows: list[dict], stats: dict,
                            parties: dict[str, int]) -> None:
    for r in rows:
        cur.execute(
            "SELECT c.candidate_id, c.party_id FROM candidate c "
            "JOIN election e ON e.election_id = c.election_id "
            "WHERE e.ac_id = %s AND e.label = %s AND lower(c.name_en) = lower(%s)",
            (ac_id, r.get("election", ""), normalize_text(r.get("candidate_name", ""))))
        hits = cur.fetchall()
        if len(hits) != 1:
            stats["skipped"] += 1
            log.warning("skipping %r in %s: %s candidates match", r.get("candidate_name"),
                        r.get("election"), len(hits))
            continue
        candidate = hits[0]
        prev = (r.get("prev_party") or "").upper()
        prev_party_id = parties.get(prev) if prev else None
        if prev and prev_party_id is None:
            stats["skipped"] += 1
            log.warning("skipping %r: unknown prev_party %r", r.get("candidate_name"), prev)
            continue
        # Derived, not asserted: "stood for someone else last time".
        turncoat = (None if prev_party_id is None or candidate["party_id"] is None
                    else prev_party_id != candidate["party_id"])
        cur.execute(
            "INSERT INTO candidate_profile (candidate_id, incumbent, contests_prior, wins_prior, "
            "prev_party_id, turncoat, deposit_forfeited, assets_declared, liabilities, "
            "criminal_cases, criminal_serious, education, age, profession, affidavit_url, "
            "myneta_id, source, fetched_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now()) "
            "ON CONFLICT (candidate_id) DO UPDATE SET incumbent = EXCLUDED.incumbent, "
            "contests_prior = EXCLUDED.contests_prior, wins_prior = EXCLUDED.wins_prior, "
            "prev_party_id = EXCLUDED.prev_party_id, turncoat = EXCLUDED.turncoat, "
            "deposit_forfeited = EXCLUDED.deposit_forfeited, "
            "assets_declared = EXCLUDED.assets_declared, liabilities = EXCLUDED.liabilities, "
            "criminal_cases = EXCLUDED.criminal_cases, "
            "criminal_serious = EXCLUDED.criminal_serious, education = EXCLUDED.education, "
            "age = EXCLUDED.age, profession = EXCLUDED.profession, "
            "affidavit_url = EXCLUDED.affidavit_url, myneta_id = EXCLUDED.myneta_id, "
            "source = EXCLUDED.source, fetched_at = now()",
            (candidate["candidate_id"], _bool(r.get("incumbent", "")),
             parse_int(r.get("contests_prior")), parse_int(r.get("wins_prior")),
             prev_party_id, turncoat, _bool(r.get("deposit_forfeited", "")),
             parse_int(r.get("assets_declared")), parse_int(r.get("liabilities")),
             parse_int(r.get("criminal_cases")), parse_int(r.get("criminal_serious")),
             _text(r.get("education", "")), parse_int(r.get("age")),
             _text(r.get("profession", "")), r.get("affidavit_url") or None,
             r.get("myneta_id") or None, r.get("source") or None))
        stats["loaded"] += 1


def _load_local_office_holder(cur, ac_id: int, rows: list[dict], stats: dict,
                              parties: dict[str, int]) -> None:
    for r in rows:
        office = (r.get("office") or "").strip()
        name = _text(r.get("name", ""))
        if office not in OFFICES or not name:
            stats["skipped"] += 1
            log.warning("skipping %r: office must be one of %s and name is required",
                        r.get("name"), ", ".join(sorted(OFFICES)))
            continue
        area_id = _area_id(cur, ac_id, r.get("area_name", ""))
        if r.get("area_name") and area_id is None:
            stats["unmatched_area"] += 1
        tagged = (r.get("tagged_party") or "").upper()
        tagged_id = parties.get(tagged) if tagged else None
        if tagged and (tagged_id is None or not r.get("tag_source")):
            # The schema refuses a tag without a source; say why here rather
            # than surfacing a constraint violation.
            stats["skipped"] += 1
            log.warning("skipping %r: a party tag needs a known party and a tag_source", name)
            continue
        cur.execute(
            "DELETE FROM local_office_holder WHERE ac_id = %s AND office = %s AND name = %s "
            "AND area_id IS NOT DISTINCT FROM %s AND term_start IS NOT DISTINCT FROM %s",
            (ac_id, office, name, area_id, r.get("term_start") or None))
        cur.execute(
            "INSERT INTO local_office_holder (ac_id, area_id, office, name, tagged_party_id, "
            "tag_source, term_start, term_end, notes) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (ac_id, area_id, office, name, tagged_id, r.get("tag_source") or None,
             r.get("term_start") or None, r.get("term_end") or None, _text(r.get("notes", ""))))
        stats["loaded"] += 1


def load(kind: str, path: Path, ac_number: int, dry_run: bool = False) -> dict:
    from common.db import connection

    rows = _rows(path)
    stats = {"kind": kind, "rows": len(rows), "loaded": 0, "skipped": 0, "unmatched_area": 0}
    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT ac_id FROM ac WHERE ac_number = %s", (ac_number,))
        ac = cur.fetchone()
        if ac is None:
            raise ValueError(f"AC {ac_number} is not seeded")
        cur.execute("SELECT abbr, party_id FROM party")
        parties = {r["abbr"].upper(): r["party_id"] for r in cur.fetchall()}
        if kind == "demography":
            _load_demography(cur, ac["ac_id"], rows, stats)
        elif kind == "candidate_profile":
            _load_candidate_profile(cur, ac["ac_id"], rows, stats, parties)
        else:
            _load_local_office_holder(cur, ac["ac_id"], rows, stats, parties)
        if dry_run:
            conn.rollback()
            stats["dry_run"] = True
    return stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Load a hand-prepared CSV",
                                 epilog=__doc__.split("Column formats", 1)[-1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("kind", choices=KINDS)
    ap.add_argument("csv", type=Path)
    ap.add_argument("--ac", type=int, required=True, help="AC number the rows belong to")
    ap.add_argument("--dry-run", action="store_true", help="validate and roll back")
    args = ap.parse_args(argv)
    with job_context(f"ingest.load_csv.{args.kind}", ac=args.ac) as job:
        stats = load(args.kind, args.csv, args.ac, args.dry_run)
        job.set(**stats)
        job.log_line(str(stats))
    return 0 if stats["skipped"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
