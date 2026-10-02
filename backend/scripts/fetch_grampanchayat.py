"""Scrape current panchayat office-holders from grampanchayat.jharkhand.gov.in.

The portal is a CodeIgniter PHP app. The discovery flow is:
  GET /                          → ci_session cookie
  POST //fetch_block val=<dist>  → HTML <option> list of blocks for a district
  POST //fetch_gp    val=<block> → HTML <option> list of GPs for a block
  POST /find_data    district=&block=&gp= → 303 redirect to /<gp_id>
  GET  /<gp_id>                  → HTML page with member spans

All member names are in <span id="ContentPlaceHolder1_lbl_<role>Name"> elements.

Usage:
    python -m scripts.fetch_grampanchayat --dry-run
    python -m scripts.fetch_grampanchayat --blocks GIRIDIH PIRTAND --out data_giridih/raw_gp
"""

from __future__ import annotations

import argparse
import json
import re
import ssl
import sys
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx
import truststore
from bs4 import BeautifulSoup

BASE = "https://grampanchayat.jharkhand.gov.in"
DISTRICT = "GIRIDIH"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 16; Pixel 10) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/152.0.0.0 Mobile Safari/537.36"
    ),
    "Origin": BASE,
    "Referer": BASE + "/",
    "X-Requested-With": "XMLHttpRequest",
}

OFFICE_MAP = {
    "Mukhiya": "mukhiya",
    "Up-Mukhiya": "up_mukhiya",
    "Panchayat Sachiv": "panchayat_sachiv",
    "Rojgar Sevak": "rojgar_sevak",
    "Ward Sadasya": "ward",
    "VLE": "vle",
}

# Offices we want in the output (skip sachiv/sevak/vle as they are functionaries, not elected)
ELECTED_OFFICES = {"mukhiya", "up_mukhiya", "ward"}


def _session() -> httpx.Client:
    # The portal uses an Indian government (NIC) CA that is not in Python's certifi
    # bundle but IS in the macOS system keychain. truststore patches the ssl module
    # to load from the OS certificate store while keeping full TLS verification.
    ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    client = httpx.Client(
        headers={"User-Agent": HEADERS["User-Agent"]},
        follow_redirects=False,
        timeout=30,
        verify=ctx,
    )
    resp = client.get(BASE + "/")
    resp.raise_for_status()
    return client


def fetch_gp_list(client: httpx.Client, block: str) -> list[str]:
    resp = client.post(
        BASE + "//fetch_gp",
        data={"val": block},
        headers=HEADERS,
    )
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")
    return [
        opt["value"]
        for opt in soup.find_all("option")
        if opt.get("value")
    ]


def fetch_gp_page(client: httpx.Client, block: str, gp: str) -> Optional[tuple[str, str]]:
    """POST find_data, follow redirect, return (gp_id, html)."""
    resp = client.post(
        BASE + "/find_data",
        data={"district": DISTRICT, "block": block, "gp": gp},
        headers={k: v for k, v in HEADERS.items() if k != "X-Requested-With"},
    )
    if resp.status_code in (301, 302, 303, 307, 308):
        location = resp.headers.get("location", "")
        gp_id = location.rstrip("/").split("/")[-1]
        final = client.get(location if location.startswith("http") else BASE + location,
                           headers={"User-Agent": HEADERS["User-Agent"],
                                    "Referer": BASE + "/"})
        return gp_id, final.text
    return None, resp.text


def parse_gp_html(html: str, block: str, gp: str, gp_id: str) -> dict:
    soup = BeautifulSoup(html, "lxml")

    def span_text(span_id: str) -> str:
        el = soup.find("span", {"id": f"ContentPlaceHolder1_lbl_{span_id}"})
        return el.get_text(strip=True) if el else ""

    gp_name = span_text("GPNameMukhiya") or span_text("GPNamevle") or span_text("GPName") or gp

    members: list[dict] = []
    layout = "A"

    # --- Layout A: named role spans (MukhiyaName, Up-MukhiyaName, etc.) ---
    for portal_role, office in OFFICE_MAP.items():
        name = span_text(f"{portal_role}Name")
        if name:
            members.append({
                "portal_role": portal_role,
                "office": office,
                "name": name,
            })

    # Collect all Ward Sadasya names (multiple spans with same id in Layout A)
    all_ward_spans = re.findall(
        r'id="ContentPlaceHolder1_lbl_Ward SadasyaName"[^>]*>\s*(.*?)\s*</span>',
        html, re.DOTALL,
    )
    ward_names_seen = {m["name"] for m in members if m["office"] == "ward"}
    for raw in all_ward_spans:
        name = BeautifulSoup(raw, "lxml").get_text(strip=True)
        if name and name not in ward_names_seen:
            members.append({"portal_role": "Ward Sadasya", "office": "ward", "name": name})
            ward_names_seen.add(name)

    # --- Layout B: table with क्र./नाम/पद columns (role column often empty) ---
    if not members:
        layout = "B"
        tables = soup.find_all("table")
        for tbl in tables:
            headers = [th.get_text(strip=True) for th in tbl.find_all("th")]
            if not headers:
                # Try first row as header
                first_row = tbl.find("tr")
                if first_row:
                    headers = [td.get_text(strip=True) for td in first_row.find_all(["td", "th"])]
            if "नाम" in headers or "Name" in " ".join(headers):
                name_idx = next((i for i, h in enumerate(headers) if "नाम" in h or "Name" in h), None)
                role_idx = next((i for i, h in enumerate(headers) if "पद" in h or "Post" in h or "Designation" in h), None)
                for row in tbl.find_all("tr")[1:]:
                    cells = [td.get_text(strip=True) for td in row.find_all("td")]
                    if name_idx is not None and name_idx < len(cells):
                        name = cells[name_idx].strip()
                        role_text = cells[role_idx].strip() if role_idx is not None and role_idx < len(cells) else ""
                        # Map portal role text to office; unknown if blank
                        office = OFFICE_MAP.get(role_text, "ward_unclassified")
                        if name:
                            members.append({
                                "portal_role": role_text or "unknown",
                                "office": office,
                                "name": name,
                            })

    # GP stats
    return {
        "gp_id": gp_id,
        "district": DISTRICT,
        "block": block,
        "gp_name": gp_name,
        "gp_key": gp,
        "layout": layout,
        "members": members,
        "stats": {
            "area": span_text("Area"),
            "total_ward": span_text("TotalWard"),
            "total_family": span_text("TotalFamily"),
            "total_population": span_text("TotalPopulation"),
            "total_male": span_text("TotalMale"),
            "total_female": span_text("TotalFemale"),
        },
    }


def scrape_block(
    client: httpx.Client,
    block: str,
    out_dir: Path,
    dry_run: bool,
    delay: float,
) -> list[dict]:
    gps = fetch_gp_list(client, block)
    print(f"  {block}: {len(gps)} GPs found")

    results = []
    for gp in gps:
        if dry_run:
            print(f"    [dry-run] would fetch GP: {gp}")
            results.append({"block": block, "gp": gp})
            continue

        try:
            gp_id, html = fetch_gp_page(client, block, gp)
            if not gp_id:
                print(f"    WARN: no redirect for {gp}", file=sys.stderr)
                continue

            data = parse_gp_html(html, block, gp, gp_id)
            member_count = len(data["members"])

            gp_dir = out_dir / block.lower()
            gp_dir.mkdir(parents=True, exist_ok=True)
            out_file = gp_dir / f"{gp_id}_{gp.lower().replace(' ', '_')}.json"
            out_file.write_text(json.dumps(data, ensure_ascii=False, indent=2))

            results.append({
                "block": block,
                "gp": gp,
                "gp_id": gp_id,
                "members": member_count,
                "file": str(out_file),
            })
            print(f"    {gp:<30}  id={gp_id:<8}  {member_count} members → {out_file.name}")
            time.sleep(delay)
        except Exception as exc:
            print(f"    ERROR {gp}: {exc}", file=sys.stderr)
            results.append({"block": block, "gp": gp, "error": str(exc)})

    return results


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Scrape Jharkhand panchayat office-holders")
    ap.add_argument(
        "--blocks", nargs="+", default=["GIRIDIH", "PIRTAND"],
        help="blocks to scrape (default: GIRIDIH PIRTAND)",
    )
    ap.add_argument(
        "--out", default="data_giridih/raw_gp",
        help="output directory for raw JSON files",
    )
    ap.add_argument("--dry-run", action="store_true", help="list GPs without downloading")
    ap.add_argument("--delay", type=float, default=1.5, help="seconds between requests")
    args = ap.parse_args(argv)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Connecting to {BASE}...")
    client = _session()

    index: dict[str, list] = {}
    all_results: list[dict] = []

    for block in args.blocks:
        print(f"\nBlock: {block}")
        results = scrape_block(client, block, out_dir, args.dry_run, args.delay)
        index[block] = results
        all_results.extend(results)

    # Write index
    if not args.dry_run:
        index_path = out_dir / "gp_index.json"
        index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2))
        print(f"\nIndex written to {index_path}")

    # Summary
    total_gps = len(all_results)
    total_members = sum(r.get("members", 0) for r in all_results)
    errors = [r for r in all_results if "error" in r]
    print(f"\n{'='*50}")
    print(f"GPs processed : {total_gps}")
    print(f"Total members : {total_members}")
    if errors:
        print(f"Errors        : {len(errors)}")
        for e in errors:
            print(f"  {e['block']}/{e['gp']}: {e['error']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
