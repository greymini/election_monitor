#!/usr/bin/env python
"""Giridih (AC-32) polling stations from the current BLO list, matched to LGD villages.

    python scripts/build_ps_list_current.py

Reads `raw/ps_list/extracted_blo_data.xlsx` (gitignored): a transcription of a
scanned BLO (booth level officer) list, five pages, parts 276-385 - every one
in Pirtand block. Writes `db/seed/ps_list/giridih_current_parts.csv`.

**What is kept, and what is not.** The list names each part's polling station
building ("उ0 म0 वि0 हरलाडीह": the upgraded middle school, Harladih). That, the
part number and the village are kept. The BLOs' names and mobile numbers are
**not**: they are personal contact details of individuals, and nothing in this
repository needs them. They stay in `raw/`, which is never committed.

**Part numbers are not 2024 PS numbers.** The 2024 Form 20 has 367 polling
stations; this list runs to part 385, so the stations were renumbered after
2024 (rationalisation before the 2026 roll revision). Nothing here is attached
to a 2024 booth by number.

**Village matching.** The building name ends in its village, in Hindi; LGD
spells villages in English. Both are reduced to a Latin key (ड़ -> r, w -> v,
vowels dropped) and compared with Jaro-Winkler within Pirtand block's LGD
villages. A match is accepted only when it is close on the key *and* on the
full spelling and clearly beats the next village, and when no other village
in the block has the same name. Everything else is written as unmatched, with
the best candidate for a person to check - a wrong village would put a station
in the wrong panchayat.
"""

from __future__ import annotations

import csv
import pathlib
import re
import sys
from collections import Counter

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SRC = ROOT / "raw" / "ps_list" / "extracted_blo_data.xlsx"
VILLAGES = ROOT / "raw" / "geo" / "lgd" / "lgd_30Sep2026_giridih_villages.csv"
OUT = ROOT / "db" / "seed" / "ps_list" / "giridih_current_parts.csv"

BLOCK = "Pirtand"
SOURCE = ("BLO list, AC 32-Giridih, parts 276-385 (scanned PDF, 5 pages, transcribed to "
          "extracted_blo_data.xlsx); BLO names and phone numbers not kept")

# Building prefixes as the list prints them: school types and other buildings.
_PREFIX = re.compile(
    r"^(\+2\s*)?(प्रोजेक्ट\s*)?(बालिका\s*)?(उ0\s*)?(म0|प्रा0|उ0)?\s*(वि0|विद्यालय)\s*"
    r"|^पंचायत भवन\s*|^आंगनबाड़ी केन्द्र\s*")
# "पूर्वी भाग" (eastern part) and the like: one building split into two stations.
_SECTION = re.compile(r"[,]?\s*(पूर्वी|पश्चिमी|प0|उ0|उत्तरी|दक्षिणी|दक्षिण|मध्य)\s*भाग.*$|\s+II?$")

KEY_MIN, FULL_MIN, MARGIN = 0.95, 0.85, 0.04


def village_of(building: str) -> str:
    """The village a building name ends in: "उ0 म0 वि0 राजो पूर्वी भाग" -> "राजो"."""
    text = building.replace("उ0म0वि0", "उ0 म0 वि0")
    text = _SECTION.sub("", _PREFIX.sub("", text)).strip(" ,")
    text = text.split(",")[0].strip()
    return text.split()[0] if text else ""


def latin(hindi: str) -> str:
    from common.textnorm import to_latin

    # LGD writes the flapped ड़ as r (Pirtanr for पीरटांड़).
    return to_latin(hindi.replace("ड़", "र").replace("ढ़", "र"))


def key(text: str) -> str:
    s = re.sub(r"[^a-z]", "", text.lower()).replace("w", "v").replace("sh", "s")
    s = re.sub(r"[aeiouy]", "", s)
    return re.sub(r"(.)\1+", r"\1", s)


def match(village_hi: str, villages: list[dict], latin_input: bool = False) -> dict:
    """Best LGD village for a name; `latin_input` when the name is already in Latin script."""
    import jellyfish

    lat = village_hi.lower() if latin_input else latin(village_hi)
    scored = sorted(
        ((jellyfish.jaro_winkler_similarity(key(lat), key(v["name"])),
          jellyfish.jaro_winkler_similarity(lat, v["name"].lower()), v) for v in villages),
        key=lambda t: (-t[0], -t[1]))
    (k1, f1, best), (k2, _f2, second) = scored[0], scored[1]
    namesakes = sum(1 for v in villages if v["name"].lower() == best["name"].lower())
    ok = k1 >= KEY_MIN and f1 >= FULL_MIN and namesakes == 1 and k1 - k2 >= MARGIN
    reason = ("" if ok else
              f"name shared by {namesakes} villages" if namesakes > 1 else
              "not close enough" if k1 < KEY_MIN or f1 < FULL_MIN else
              f"too close to {second['name']}")
    return {"village_lgd": best["name"], "village_code": best["code"], "panchayat": best["gp"],
            "panchayat_code": best["gp_code"], "score": round(k1, 2), "full": round(f1, 2),
            "census": best.get("census", ""), "block": best.get("block", ""),
            "status": "matched" if ok else "unmatched", "note": reason}


def build() -> list[dict]:
    import openpyxl

    villages = [
        {"name": v["Village Name (In English)"].strip(), "code": v["Village Code"],
         "gp": v["Local Body Name (In English)"].strip(), "gp_code": v["Local Body Code"]}
        for v in csv.DictReader(VILLAGES.open(encoding="utf-8"))
        if v["Development Block Name (In English)"] == BLOCK
    ]
    sheet = openpyxl.load_workbook(SRC, read_only=True, data_only=True)["Extracted Data"]
    rows = []
    for r in list(sheet.iter_rows(values_only=True))[1:]:
        if r[3] is None:
            continue
        building = str(r[4]).strip()
        village_hi = village_of(building)
        m = match(village_hi, villages) if village_hi else {
            "village_lgd": "", "village_code": "", "panchayat": "", "panchayat_code": "",
            "score": "", "status": "unmatched", "note": "no village in the building name"}
        rows.append({"ac_number": 32, "part_number": int(r[3]), "building_hi": building,
                     "village_hi": village_hi, "block_name_en": f"{BLOCK} Block",
                     **{k: v for k, v in m.items() if k not in ("full", "census", "block")},
                     "source": SOURCE})
    rows.sort(key=lambda r: r["part_number"])
    return rows


def main() -> int:
    rows = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    status = Counter(r["status"] for r in rows)
    print(f"{len(rows)} parts: {dict(status)}; "
          f"{len({r['panchayat'] for r in rows if r['status'] == 'matched'})} panchayats")
    return 0


if __name__ == "__main__":
    sys.exit(main())
