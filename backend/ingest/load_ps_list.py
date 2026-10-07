"""Name and place AC-32's booths from the 2024 polling-station list.

    python -m ingest.load_ps_list                 # db/seed/ps_list/giridih_ps2024.csv
    python -m ingest.load_ps_list --dry-run

Run after the Form 20 load has created the booths (one per station number).
`scripts/build_ps_list_2024.py` builds the CSV from the CEO Jharkhand register;
this only writes it, booth by booth, matched on `booth.current_ps_number`:

* name (English and Hindi), building and locality from the register (real);
* area: the panchayat by LGD code, or the municipal ward by number (derived -
  see the CSV's `locate_method`);
* lon/lat and `geocode_conf` from the CSV, `geocode_source` = `ps-list-2024:`
  plus how the point was found. These are approximate (village or ward level,
  `locate_conf` 0.15-0.6); the register has no coordinates.

A booth already pinned by hand (`geocode_source = 'manual'`) keeps its point.
The source document is registered in `source_doc` like any other load.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import sys
from pathlib import Path

from common.db import cursor, query_one
from common.logging_setup import get_logger, setup_logging

log = get_logger(__name__)

DEFAULT = Path(__file__).resolve().parents[1] / "db" / "seed" / "ps_list" / "giridih_ps2024.csv"
WARD_BLOCK = "Giridih Municipal Corporation"


def _area(cur, ac_id: int, row: dict) -> int | None:
    if row["setting"] == "urban" and row["ward_no"]:
        cur.execute(
            "SELECT a.area_id FROM area a JOIN block b ON b.block_id = a.block_id "
            "WHERE a.ac_id = %s AND a.kind = 'ward' AND b.name_en = %s AND a.name_en = %s",
            (ac_id, WARD_BLOCK, f"Ward {int(row['ward_no'])}"))
    elif row["panchayat_lgd_code"]:
        cur.execute("SELECT area_id FROM area WHERE ac_id = %s AND kind = 'panchayat' AND code = %s",
                    (ac_id, row["panchayat_lgd_code"]))
    else:
        return None
    hit = cur.fetchone()
    return hit["area_id"] if hit else None


def load(path: Path = DEFAULT, dry_run: bool = False) -> dict:
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    ac_number = int(rows[0]["ac_number"])
    ac = query_one("SELECT ac_id FROM ac WHERE ac_number = %s", (ac_number,))
    if ac is None:
        raise SystemExit(f"AC {ac_number} is not seeded")
    ac_id = ac["ac_id"]
    stats = {"stations": len(rows), "booths": 0, "no_booth": 0, "no_area": 0, "pinned_kept": 0}
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with cursor() as cur:
        cur.execute(
            "INSERT INTO source_doc (filename, kind, sha256, bytes, ac_id, parse_status, is_synthetic, "
            "method, method_note, storage_backend, storage_key) "
            "VALUES (%s, 'ps_list', %s, %s, %s, 'loaded', false, 'published', %s, 'local', %s) "
            "ON CONFLICT (sha256) DO NOTHING",
            (path.name, digest, path.stat().st_size, ac_id,
             "Station names from the CEO Jharkhand register; village, panchayat, ward and "
             "location matched by scripts/build_ps_list_2024.py (approximate, see locate_method)",
             f"db/seed/ps_list/{path.name}"))
        for r in rows:
            cur.execute("SELECT booth_uid, geocode_source FROM booth "
                        "WHERE ac_id = %s AND current_ps_number = %s AND is_active",
                        (ac_id, int(r["ps_number"])))
            booth = cur.fetchone()
            if booth is None:
                stats["no_booth"] += 1
                continue
            area_id = _area(cur, ac_id, r)
            if area_id is None:
                stats["no_area"] += 1
                log.warning("PS %s: no area for %s/%s", r["ps_number"], r["panchayat_en"], r["ward_no"])
            locality = r["village_en"] or (f"Ward {r['ward_no']}" if r["ward_no"] else None)
            keep_pin = booth["geocode_source"] == "manual"
            stats["pinned_kept"] += keep_pin
            note = (f"PS list 2024: {r['setting']}, located by {r['locate_method']}"
                    + (f"; {r['note']}" if r["note"] else ""))
            if dry_run:
                stats["booths"] += 1
                continue
            cur.execute(
                "UPDATE booth SET ps_name_en = %s, ps_name_hi = %s, building = %s, "
                "village_or_locality = %s, area_id = COALESCE(%s, area_id), notes = %s, "
                "lon = CASE WHEN %s THEN lon ELSE %s END, lat = CASE WHEN %s THEN lat ELSE %s END, "
                "geocode_conf = CASE WHEN %s THEN geocode_conf ELSE %s END, "
                "geocode_source = CASE WHEN %s THEN geocode_source ELSE %s END, updated_at = now() "
                "WHERE booth_uid = %s",
                (r["ps_name_en"], r["ps_name_hi"] or None, r["ps_name_en"], locality, area_id, note,
                 keep_pin, float(r["lon"]) if r["lon"] else None,
                 keep_pin, float(r["lat"]) if r["lat"] else None,
                 keep_pin, float(r["locate_conf"]) if r["locate_conf"] else None,
                 keep_pin, f"ps-list-2024:{r['locate_method'].split(':')[0]}",
                 booth["booth_uid"]))
            stats["booths"] += 1
    log.info("load_ps_list: %s", stats)
    return stats


def main(argv: list[str] | None = None) -> int:
    setup_logging()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("csv", nargs="?", type=Path, default=DEFAULT)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    stats = load(args.csv, args.dry_run)
    print(stats)
    return 0 if stats["no_booth"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
