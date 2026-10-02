"""Normalize raw panchayat member JSON into a review spreadsheet and load CSV.

Reads: data_giridih/raw_gp/**/*.json  (output of fetch_grampanchayat.py)
Writes:
  data_giridih/panchayat_members.xlsx  — 3 sheets: Members, ForReview, GPIndex
  data_giridih/panchayat_members_load.csv  — DB-ready for ingest/load_office_holders.py

Usage:
    python -m scripts.normalize_panchayat_members
    python -m scripts.normalize_panchayat_members --raw-dir data_giridih/raw_gp --out data_giridih
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

# Offices kept for the DB load (elected offices only; sachiv/sevak/vle are functionaries)
ELECTED_OFFICES = {"mukhiya", "up_mukhiya", "ward", "zila_parishad", "panchayat_samiti"}

# DB office values accepted by local_office_holder.office CHECK constraint
DB_OFFICE_MAP = {
    "mukhiya": "mukhiya",
    "up_mukhiya": "mukhiya",      # No up_mukhiya in DB schema; roll up to mukhiya
    "ward": "ward",
    "zila_parishad": "zila_parishad",
    "panchayat_samiti": "panchayat_samiti",
    "ward_unclassified": "ward",  # Layout B rows with no role → treat as ward member
}

# Offices that need manual role assignment before DB load
NEEDS_REVIEW_OFFICES = {"ward_unclassified", "unknown"}

REVIEW_FLAGS = {
    "no_name": "Member name is empty",
    "no_role": "Role/office not identified (portal Layout B, पद column empty)",
    "duplicate": "Duplicate name within the same GP",
    "empty_gp": "GP has 0 members on the portal (portal data absent)",
}


def load_raw_files(raw_dir: Path) -> list[dict]:
    files = list(raw_dir.rglob("*.json"))
    files = [f for f in files if f.name != "gp_index.json"]
    records = []
    for path in sorted(files):
        try:
            data = json.loads(path.read_text())
            records.append(data)
        except Exception as exc:
            print(f"WARN: could not read {path}: {exc}", file=sys.stderr)
    return records


def build_rows(records: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """Return (members, review_rows, gp_index)."""
    members: list[dict] = []
    review_rows: list[dict] = []
    gp_index: list[dict] = []

    for gp in records:
        block = gp.get("block", "")
        gp_name = gp.get("gp_name", gp.get("gp_key", ""))
        gp_id = gp.get("gp_id", "")
        layout = gp.get("layout", "A")
        stats = gp.get("stats", {})
        gp_members = gp.get("members", [])

        gp_index.append({
            "block": block,
            "gp_key": gp.get("gp_key", ""),
            "gp_name": gp_name,
            "gp_id": gp_id,
            "layout": layout,
            "total_ward": stats.get("total_ward", ""),
            "total_population": stats.get("total_population", ""),
            "member_count": len(gp_members),
        })

        if not gp_members:
            review_rows.append({
                "block": block,
                "gp_name": gp_name,
                "office": "",
                "name": "",
                "flag": REVIEW_FLAGS["empty_gp"],
                "notes": f"GP ID {gp_id}, layout {layout}",
            })
            continue

        seen_names: set[str] = set()
        for m in gp_members:
            name = (m.get("name") or "").strip()
            office = m.get("office", "unknown")
            portal_role = m.get("portal_role", "")
            flags = []

            if not name:
                flags.append("no_name")
            elif name in seen_names:
                flags.append("duplicate")
            seen_names.add(name)

            if office in NEEDS_REVIEW_OFFICES:
                flags.append("no_role")

            db_office = DB_OFFICE_MAP.get(office, "ward")

            row = {
                "block": block,
                "gp_name": gp_name,
                "office": db_office,
                "portal_role": portal_role,
                "name": name,
                "term_start": "",
                "term_end": "",
                "notes": f"layout={layout}" if layout == "B" else "",
                "tagged_party": "",
                "tag_source": "",
                "needs_review": bool(flags),
                "review_flags": "; ".join(REVIEW_FLAGS[f] for f in flags),
            }
            members.append(row)
            if flags:
                review_rows.append({
                    "block": block,
                    "gp_name": gp_name,
                    "office": db_office,
                    "name": name,
                    "flag": row["review_flags"],
                    "notes": f"GP ID {gp_id}",
                })

    return members, review_rows, gp_index


def write_xlsx(members: list[dict], review: list[dict], gp_index: list[dict], out_path: Path) -> None:
    wb = openpyxl.Workbook()

    # --- Sheet 1: Members ---
    ws = wb.active
    ws.title = "Members"
    header = [
        "block", "gp_name", "office", "portal_role", "name",
        "term_start", "term_end", "notes",
        "tagged_party", "tag_source",
        "needs_review", "review_flags",
    ]
    ws.append(header)
    hdr_fill = PatternFill("solid", fgColor="1F4E79")
    hdr_font = Font(bold=True, color="FFFFFF")
    for col, _ in enumerate(header, 1):
        cell = ws.cell(1, col)
        cell.fill = hdr_fill
        cell.font = hdr_font
        cell.alignment = Alignment(horizontal="center")

    review_fill = PatternFill("solid", fgColor="FFF2CC")
    for row in members:
        ws.append([row.get(k, "") for k in header])
        if row.get("needs_review"):
            for col in range(1, len(header) + 1):
                ws.cell(ws.max_row, col).fill = review_fill

    # Auto-width
    for col_idx, col_name in enumerate(header, 1):
        max_len = max(len(str(col_name)), *(len(str(row.get(col_name, ""))) for row in members))
        ws.column_dimensions[get_column_letter(col_idx)].width = min(max_len + 2, 40)

    # --- Sheet 2: ForReview ---
    ws2 = wb.create_sheet("ForReview")
    review_header = ["block", "gp_name", "office", "name", "flag", "notes"]
    ws2.append(review_header)
    for col, _ in enumerate(review_header, 1):
        cell = ws2.cell(1, col)
        cell.fill = PatternFill("solid", fgColor="C00000")
        cell.font = Font(bold=True, color="FFFFFF")
    for row in review:
        ws2.append([row.get(k, "") for k in review_header])
    for col_idx, col_name in enumerate(review_header, 1):
        max_len = max(len(col_name), *(len(str(row.get(col_name, ""))) for row in review))
        ws2.column_dimensions[get_column_letter(col_idx)].width = min(max_len + 2, 50)

    # --- Sheet 3: GPIndex ---
    ws3 = wb.create_sheet("GPIndex")
    idx_header = ["block", "gp_key", "gp_name", "gp_id", "layout", "total_ward", "total_population", "member_count"]
    ws3.append(idx_header)
    for col, _ in enumerate(idx_header, 1):
        cell = ws3.cell(1, col)
        cell.fill = PatternFill("solid", fgColor="375623")
        cell.font = Font(bold=True, color="FFFFFF")
    for row in gp_index:
        ws3.append([row.get(k, "") for k in idx_header])

    wb.save(out_path)
    print(f"Spreadsheet written: {out_path}")


def write_load_csv(members: list[dict], out_path: Path) -> None:
    """Write DB-ready CSV for ingest/load_office_holders.py.

    Columns match the loader's expected schema:
    office, name, area_name, term_start, term_end, notes, tagged_party, tag_source
    """
    fieldnames = ["office", "name", "area_name", "term_start", "term_end", "notes", "tagged_party", "tag_source"]
    with out_path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for m in members:
            if not m.get("name"):
                continue
            writer.writerow({
                "office": m["office"],
                "name": m["name"],
                "area_name": m["gp_name"],
                "term_start": m.get("term_start", ""),
                "term_end": m.get("term_end", ""),
                "notes": m.get("notes", ""),
                "tagged_party": m.get("tagged_party", ""),
                "tag_source": m.get("tag_source", ""),
            })
    print(f"Load CSV written: {out_path}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Normalize raw GP JSON to review spreadsheet")
    ap.add_argument("--raw-dir", default="data_giridih/raw_gp")
    ap.add_argument("--out", default="data_giridih")
    args = ap.parse_args(argv)

    raw_dir = Path(args.raw_dir)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    if not raw_dir.exists():
        print(f"ERROR: raw dir {raw_dir} does not exist. Run fetch_grampanchayat.py first.", file=sys.stderr)
        return 1

    print(f"Loading JSON files from {raw_dir}...")
    records = load_raw_files(raw_dir)
    print(f"  {len(records)} GP files loaded")

    members, review, gp_index = build_rows(records)

    elected = [m for m in members if not m.get("needs_review")]
    needs_review = [m for m in members if m.get("needs_review")]
    print(f"  {len(members)} total member rows")
    print(f"  {len(elected)} clean rows  |  {len(needs_review)} need review  |  {len(review)} review items")

    write_xlsx(members, review, gp_index, out_dir / "panchayat_members.xlsx")
    write_load_csv(members, out_dir / "panchayat_members_load.csv")

    # Summary by block
    by_block: dict[str, int] = {}
    for m in members:
        by_block[m["block"]] = by_block.get(m["block"], 0) + 1
    for block, count in sorted(by_block.items()):
        print(f"  {block}: {count} members")

    return 0


if __name__ == "__main__":
    sys.exit(main())
