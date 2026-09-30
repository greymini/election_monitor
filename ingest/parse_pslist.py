"""Polling-station list parser: the source of the booth backbone.

The PS list gives, per polling station: number, name/building, the villages or
wards it covers, and the roll part number. Two things come out of it:

  * `ps_list_entry` rows for every election/revision (crosswalk input), and
  * for the **anchor** list (the most recent one), the `booth` table itself,
    with a stable `booth_uid` per station and the panchayat/ward it sits in.

Panchayat names are created from the list rather than seeded, because inventing
them would silently corrupt every rollup (see db/seed/README.md).

    python -m ingest.parse_pslist raw/ps_list_2024.pdf --election VS-2024 --dry-run
    python -m ingest.parse_pslist raw/ps_list_2024.pdf --election VS-2024 --load --anchor
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from common.logging_setup import get_logger
from common.textnorm import alias_key, normalize_text, parse_int
from ingest.documents import (
    DocumentNotFound,
    add_document_arguments,
    advance_status,
    open_document,
)

log = get_logger(__name__)

PS_NO_HEADER = [r"क्रम", r"मतदान\s*केन्?द्र\s*(?:सं|संख्या|number|no)", r"(?:ps|station)\s*(?:no|number)",
                r"serial", r"भाग\s*सं"]
NAME_HEADER = [r"मतदान\s*केन्?द्र\s*(?:का\s*)?नाम", r"(?:name|location)\s*of\s*polling\s*station",
               r"भवन", r"building"]
AREA_HEADER = [r"क्षेत्र", r"area", r"गाँव|गांव|ग्राम", r"village", r"पंचायत", r"panchayat",
               r"वार्ड", r"ward", r"मोहल्ला"]
PART_HEADER = [r"भाग\s*संख्या", r"part\s*(?:no|number)", r"roll\s*part"]

# 'प्रा०वि० चतरो, ग्राम चतरो' -> building + village, where the list crams both
# into one cell.
_SPLIT_RE = re.compile(r"\s*[,;–—-]\s*|\s{3,}")


@dataclass
class PSEntry:
    ps_number: int
    ps_name: str = ""
    building: str = ""
    village_or_locality: str = ""
    area_hint: str = ""
    roll_part: int | None = None
    page_no: int = 0

    def key(self) -> str:
        from common.textnorm import match_key

        return match_key(self.building or self.ps_name, self.village_or_locality)


def _match_any(text: str, patterns: list[str]) -> bool:
    t = normalize_text(text).lower()
    return any(re.search(p, t, flags=re.IGNORECASE) for p in patterns)


def _column_map(header: list[str]) -> dict[str, int] | None:
    """Find which column holds what. Needs at least a number and a name column."""
    cols: dict[str, int] = {}
    for i, cell in enumerate(header):
        if "ps_number" not in cols and _match_any(cell, PS_NO_HEADER):
            cols["ps_number"] = i
        elif "name" not in cols and _match_any(cell, NAME_HEADER):
            cols["name"] = i
        elif "part" not in cols and _match_any(cell, PART_HEADER):
            cols["part"] = i
        elif "area" not in cols and _match_any(cell, AREA_HEADER):
            cols["area"] = i
    if "ps_number" in cols and "name" in cols:
        return cols
    return None


def split_name_and_village(text: str) -> tuple[str, str]:
    """Split a combined 'building, village' cell. Returns (building, village)."""
    t = normalize_text(text)
    if not t:
        return "", ""
    parts = [p for p in _SPLIT_RE.split(t) if p]
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], parts[-1]


def parse_pages(pages, source_doc: str) -> list[PSEntry]:
    entries: list[PSEntry] = []
    cols: dict[str, int] | None = None
    seen: set[int] = set()

    for page in pages:
        for table in page.tables:
            for row in table:
                cells = [normalize_text(c) for c in row]
                if cols is None:
                    cols = _column_map(cells)
                    if cols:
                        log.debug("PS list columns on page %d: %s", page.page_no, cols)
                        continue
                if not cols:
                    continue
                ps_number = parse_int(cells[cols["ps_number"]]) if cols["ps_number"] < len(cells) else None
                if ps_number is None or not (0 < ps_number < 1000) or ps_number in seen:
                    continue
                name_cell = cells[cols["name"]] if cols["name"] < len(cells) else ""
                building, village = split_name_and_village(name_cell)
                area = ""
                if "area" in cols and cols["area"] < len(cells):
                    area = cells[cols["area"]]
                part = None
                if "part" in cols and cols["part"] < len(cells):
                    part = parse_int(cells[cols["part"]])

                entries.append(PSEntry(
                    ps_number=ps_number, ps_name=name_cell, building=building,
                    village_or_locality=village or area, area_hint=area or village,
                    roll_part=part, page_no=page.page_no,
                ))
                seen.add(ps_number)
    return entries


def parse_pdf(pdf_path: Path, force: bool = False) -> list[PSEntry]:
    from ingest.extract_pdf import extract_document

    pages = extract_document(pdf_path, force=force)
    entries = parse_pages(pages, pdf_path.name)
    if not entries:
        raise ValueError(
            f"{pdf_path.name}: no polling-station rows found. The header may not match the "
            f"patterns in parse_pslist.py - check the file and extend PS_NO_HEADER / NAME_HEADER."
        )
    return entries


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------

def store_entries(entries: list[PSEntry], election_label: str, source_doc: str) -> int:
    from common.db import connection

    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT election_id FROM election WHERE label = %s", (election_label,))
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"unknown election label {election_label!r}")
        eid = row["election_id"]
        for e in entries:
            cur.execute(
                "INSERT INTO ps_list_entry (election_id, ps_number, ps_name, building, "
                "village_or_locality, area_hint, roll_part, source_doc, source_page) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (election_id, ps_number) DO UPDATE SET ps_name = EXCLUDED.ps_name, "
                "building = EXCLUDED.building, village_or_locality = EXCLUDED.village_or_locality, "
                "area_hint = EXCLUDED.area_hint, roll_part = EXCLUDED.roll_part, "
                "source_doc = EXCLUDED.source_doc, source_page = EXCLUDED.source_page",
                (eid, e.ps_number, e.ps_name, e.building, e.village_or_locality,
                 e.area_hint, e.roll_part, source_doc, e.page_no),
            )
    return len(entries)


def resolve_area(cur, area_hint: str, default_block_id: int | None) -> int | None:
    """Map a printed area name to an area_id via area_alias, creating a
    panchayat when the hint is new and a block is known."""
    key = alias_key(area_hint)
    if not key:
        return None
    cur.execute("SELECT area_id FROM area_alias WHERE alias = %s", (key,))
    hit = cur.fetchone()
    if hit:
        return hit["area_id"]

    if default_block_id is None:
        return None

    name = normalize_text(area_hint)
    cur.execute(
        "INSERT INTO area (block_id, kind, name_en, name_hi) VALUES (%s, 'panchayat', %s, %s) "
        "ON CONFLICT (block_id, kind, name_en) DO UPDATE SET name_hi = EXCLUDED.name_hi "
        "RETURNING area_id",
        (default_block_id, name, name),
    )
    area_id = cur.fetchone()["area_id"]
    cur.execute(
        "INSERT INTO area_alias (alias, area_id, script, source) VALUES (%s, %s, %s, 'ps_list') "
        "ON CONFLICT (alias) DO NOTHING",
        (key, area_id, "hi" if any("ऀ" <= c <= "ॿ" for c in name) else "en"),
    )
    return area_id


def load_anchor(entries: list[PSEntry], election_label: str, default_block_id: int | None) -> dict[str, int]:
    """Create booth rows and the anchor crosswalk from the newest PS list.

    booth_uid is assigned as B0001... in polling-station order, and is stable
    from then on: later lists map onto these ids via crosswalk.py.
    """
    from common.db import connection

    stats = {"booths": 0, "areas_created": 0, "unmatched_area": 0}
    with connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT election_id FROM election WHERE label = %s", (election_label,))
        eid = cur.fetchone()["election_id"]
        cur.execute("SELECT COUNT(*) AS n FROM area")
        areas_before = cur.fetchone()["n"]

        for e in sorted(entries, key=lambda x: x.ps_number):
            booth_uid = f"B{e.ps_number:04d}"
            area_id = resolve_area(cur, e.area_hint or e.village_or_locality, default_block_id)
            if area_id is None:
                stats["unmatched_area"] += 1
                cur.execute(
                    "INSERT INTO review_queue (kind, ref, payload, note) "
                    "VALUES ('area_alias', %s, %s, %s)",
                    (f"{election_label}#PS{e.ps_number}",
                     json.dumps({"area_hint": e.area_hint, "ps_name": e.ps_name},
                                ensure_ascii=False),
                     f"PS {e.ps_number}: could not place {e.area_hint!r} in a panchayat or ward"),
                )
                continue

            cur.execute(
                "INSERT INTO booth (booth_uid, area_id, ps_name_hi, building, village_or_locality, "
                "current_ps_number) VALUES (%s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (booth_uid) DO UPDATE SET area_id = EXCLUDED.area_id, "
                "ps_name_hi = EXCLUDED.ps_name_hi, building = EXCLUDED.building, "
                "village_or_locality = EXCLUDED.village_or_locality, "
                "current_ps_number = EXCLUDED.current_ps_number",
                (booth_uid, area_id, e.ps_name, e.building, e.village_or_locality, e.ps_number),
            )
            cur.execute(
                "INSERT INTO booth_crosswalk (election_id, ps_number, booth_uid, match_method, "
                "confidence, reviewed) VALUES (%s, %s, %s, 'anchor', 1.0, true) "
                "ON CONFLICT (election_id, ps_number) DO UPDATE SET booth_uid = EXCLUDED.booth_uid, "
                "match_method = 'anchor', confidence = 1.0, reviewed = true",
                (eid, e.ps_number, booth_uid),
            )
            stats["booths"] += 1

        cur.execute("SELECT COUNT(*) AS n FROM area")
        stats["areas_created"] = cur.fetchone()["n"] - areas_before
    return stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Parse a polling-station list")
    add_document_arguments(ap)
    ap.add_argument("--election", required=True, help="election label the list belongs to")
    ap.add_argument("--load", action="store_true", help="write ps_list_entry rows")
    ap.add_argument("--anchor", action="store_true",
                    help="also create booth rows and the anchor crosswalk from this list")
    ap.add_argument("--block", type=int, default=None,
                    help="block_id to file new panchayats under (2 = Giridih, 3 = Pirtand)")
    ap.add_argument("--force", action="store_true", help="ignore the page cache")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    try:
        with open_document(pdf=args.pdf, doc=args.doc, key=args.key,
                           backend=args.backend, kind="ps_list") as (path, provenance):
            return _run(args, path, provenance)
    except DocumentNotFound as exc:
        log.error("%s", exc)
        return 2


def _run(args, path: Path, provenance: dict) -> int:
    entries = parse_pdf(path, force=args.force)
    log.info("%s: %d polling station(s), PS %d..%d",
             path.name, len(entries), min(e.ps_number for e in entries),
             max(e.ps_number for e in entries))
    for e in entries[:5]:
        log.info("  PS %3d  %-40s | area=%s part=%s", e.ps_number, e.building[:40],
                 e.area_hint[:24], e.roll_part)

    if args.dry_run or not args.load:
        log.info("dry run - nothing written. Re-run with --load to write.")
        return 0

    n = store_entries(entries, args.election, path.name)
    log.info("stored %d ps_list_entry row(s)", n)

    if args.anchor:
        if args.block is None:
            log.warning("--anchor without --block: new panchayat names cannot be filed under a "
                        "block and will go to review_queue instead")
        stats = load_anchor(entries, args.election, args.block)
        log.info("anchor load: %(booths)d booth(s), %(areas_created)d new area(s), "
                 "%(unmatched_area)d unplaced", stats)
        if stats["unmatched_area"]:
            log.warning("%d station(s) could not be placed - see review_queue(kind='area_alias')",
                        stats["unmatched_area"])
    if provenance.get("sha256"):
        try:
            advance_status(provenance["sha256"], "loaded", actor="ingest.parse_pslist")
        except Exception as exc:
            log.warning("could not set parse_status: %s", exc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
