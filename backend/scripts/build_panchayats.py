#!/usr/bin/env python
"""Build the real gram panchayat seed from the Local Government Directory.

    python scripts/build_panchayats.py            # rebuild from raw/geo/lgd/

Needs `shapely` (build-time only, like scripts/build_boundaries.py). Reads the
extracts in `raw/geo/lgd/` (gitignored) and writes three committed files:

* `db/seed/areas_panchayats.csv` - every gram panchayat of the Giridih-district
  blocks our constituencies cover, with its LGD code, the constituency its
  villages belong to, and how sure that is;
* `db/seed/area_aliases.csv` - each panchayat's villages, as names a news item
  or a PS list may use for it;
* `db/seed/geo/areas.json` - panchayat and municipal ward polygons, simplified.

It also appends a "<Block> Block (part)" row to `db/seed/ac_blocks.csv` for any
block whose panchayats are split between constituencies.

**Sources** (all in `raw/geo/lgd/`):

* `lgd_01Oct2026_giridih_district_gps.csv` - LGD gram panchayats of Giridih
  district (`pri_local_bodies`, 01 Oct 2026, via the ramSeraph LGD mirror).
  LGD prints English names only; there is no Hindi name to seed.
* `lgd_30Sep2026_giridih_villages.csv` - LGD villages with their panchayat
  (`villages_by_blocks`, 30 Sep 2026).
* `gp_from_lgd_villages_giridih_district.geojson` - india-geodata
  `LGD_Villages` polygons dissolved by current panchayat code, with each
  panchayat's villages' assembly constituency (`ac_mode` = the most common,
  `acs` = all of them). india-geodata is CC0 per its README.
* `lgd_gp_giridih_district.geojson` - india-geodata `LGD_panchayats`, used for
  a panchayat the village dissolve does not cover.
* `SBM_Wards_giridih.geojson` - india-geodata `SBM_Wards`, the 36 Giridih
  municipal wards (Swachh Bharat Mission, December 2021).
* `db/seed/ps_list/giridih_current_parts.csv` (scripts/build_ps_list_current.py),
  if present: polling-station villages in Hindi, matched to LGD villages. A
  matched village adds its Hindi spelling as an alias of its panchayat, and a
  panchayat named after one of them gets that spelling as `name_hi` - the only
  Hindi panchayat names there are, since LGD has none.

**Which constituency.** AC-32's composition is disputed between sources: the
LGD constituency-coverage table says AC-32 covers the whole Giridih
sub-district, while the ECI 2008 order and the villages' own `ac_no` put twelve
Giridih-block panchayats in Gandey (AC-31). This uses the villages' `ac_no`,
the finest-grained of the three, and writes the evidence into the `membership`
column of every row; a panchayat whose villages span two constituencies says
so. The polling-station list settles it when it is loaded (DECISIONS D-012).
"""

from __future__ import annotations

import csv
import json
import pathlib
import sys
from collections import Counter, defaultdict

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "raw" / "geo" / "lgd"
SEED = ROOT / "db" / "seed"

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

# Constituencies this project tracks.
OUR_ACS = {31, 32, 33, 42, 61, 65}

SOURCE_GP = "LGD pri_local_bodies 01-Oct-2026 (ramSeraph mirror)"
SOURCE_VILLAGE = "LGD villages_by_blocks 30-Sep-2026"
ATTRIBUTION = {
    "lgd-villages": "Panchayats: india-geodata LGD_Villages dissolved by LGD panchayat code, CC0",
    "lgd-panchayats": "Panchayats: india-geodata LGD_panchayats, CC0",
    "sbm-wards": "Wards: india-geodata SBM_Wards (Swachh Bharat Mission, Dec 2021), CC0",
}

# Shorter than a real place name, or a word a headline uses for anything.
MIN_ALIAS = 5


def _rows(path: pathlib.Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _seed_blocks() -> dict[int, set[str]]:
    blocks: dict[int, set[str]] = defaultdict(set)
    for r in _rows(SEED / "ac_blocks.csv"):
        blocks[int(r["ac_number"])].add(r["name_en"])
    return blocks


def _block_ac(lgd_block: str, seed_blocks: dict[int, set[str]]) -> int | None:
    """The constituency whose seed lists this LGD block, if exactly one does."""
    owners = [ac for ac, names in seed_blocks.items() if f"{lgd_block} Block" in names]
    return owners[0] if len(owners) == 1 else None


def build() -> dict:
    from build_boundaries import bbox, clean, label_point, rounded
    from shapely.geometry import shape

    from common.textnorm import fold

    seed_blocks = _seed_blocks()
    gps = _rows(SRC / "lgd_01Oct2026_giridih_district_gps.csv")
    covered = {g["block_en"] for g in gps if _block_ac(g["block_en"], seed_blocks)}

    dissolved = {f["properties"]["gp_code"]: f for f in json.loads(
        (SRC / "gp_from_lgd_villages_giridih_district.geojson").read_text(encoding="utf-8")
    )["features"]}
    fallback = {int(f["properties"]["gpcode"]): f for f in json.loads(
        (SRC / "lgd_gp_giridih_district.geojson").read_text(encoding="utf-8")
    )["features"] if f["properties"].get("gpcode")}

    panchayats, features, skipped = [], [], []
    new_blocks: set[tuple[int, str]] = set()
    gp_area: dict[int, tuple[int, str, str]] = {}      # gp code -> (ac, block, name)
    for g in sorted(gps, key=lambda g: (g["block_en"], g["gp_en"])):
        if g["block_en"] not in covered:
            continue
        code = int(g["gp_code"])
        poly = dissolved.get(code)
        if poly is not None:
            props = poly["properties"]
            ac = int(props["ac_mode"])
            acs = [int(a) for a in str(props["acs"]).split(";") if a]
            membership = (f"villages' ac_no: {props['acs'].replace(';', ', ')} "
                          f"({props['n_vill']} villages; most in AC {ac})")
            if len(acs) == 1:
                membership = f"all {props['n_vill']} villages in AC {ac} (village ac_no)"
        else:
            ac = _block_ac(g["block_en"], seed_blocks)
            acs = [ac]
            membership = "no village data; constituency of its block in ac_blocks.csv"
        if ac not in OUR_ACS:
            skipped.append(f"{g['gp_en']} ({g['block_en']}): villages in AC {ac}")
            continue
        block_name = f"{g['block_en']} Block"
        if block_name not in seed_blocks[ac]:
            block_name = f"{g['block_en']} Block (part)"
            new_blocks.add((ac, g["block_en"]))
        name = g["gp_en"].strip()
        gp_area[code] = (ac, block_name, name)
        panchayats.append({
            "ac_number": ac, "block_name_en": block_name, "kind": "panchayat",
            "name_en": name, "name_hi": name, "code": code, "census_code": "",
            "source": SOURCE_GP, "membership": membership,
        })

        geom, source = None, None
        if poly is not None:
            geom, source = clean(shape(poly["geometry"])), "lgd-villages"
        elif code in fallback:
            geom, source = clean(shape(fallback[code]["geometry"])), "lgd-panchayats"
        if geom is not None:
            features.append({"type": "Feature", "geometry": rounded(geom), "properties": {
                "kind": "panchayat", "ac_number": ac, "block_name_en": block_name,
                "name_en": name, "code": str(code), "source": source,
                "bbox": bbox(geom), "label_point": label_point(geom)}})

    # Municipal wards: the seed already lists Ward 1-36 of Giridih MC.
    for f in json.loads((SRC / "SBM_Wards_giridih.geojson").read_text(encoding="utf-8"))["features"]:
        geom = clean(shape(f["geometry"]))
        if geom is None:
            continue
        number = int(f["properties"]["wardcode"])
        features.append({"type": "Feature", "geometry": rounded(geom), "properties": {
            "kind": "ward", "ac_number": 32, "block_name_en": "Giridih Municipal Corporation",
            "name_en": f"Ward {number}", "code": str(number), "source": "sbm-wards",
            "bbox": bbox(geom), "label_point": label_point(geom)}})

    # Villages as aliases of their panchayat - only names that point to one
    # panchayat and cannot be mistaken for a panchayat, block or constituency.
    villages = [v for v in _rows(SRC / "lgd_30Sep2026_giridih_villages.csv")
                if v["Local Body Code"].isdigit() and int(v["Local Body Code"]) in gp_area]
    by_name: dict[str, set[int]] = defaultdict(set)
    for v in villages:
        by_name[fold(v["Village Name (In English)"])].add(int(v["Local Body Code"]))
    reserved = ({fold(p["name_en"]) for p in panchayats}
                | {fold(g["block_en"]) for g in gps}
                | {fold(r["name_en"]) for r in _rows(SEED / "ac.csv")})
    aliases, seen = [], set()
    for v in sorted(villages, key=lambda v: (v["Local Body Code"], v["Village Name (In English)"])):
        key = fold(v["Village Name (In English)"])
        code = int(v["Local Body Code"])
        ac, block_name, gp_name = gp_area[code]
        if (len(key) < MIN_ALIAS or len(by_name[key]) > 1 or key in reserved
                or key == fold(gp_name) or key in seen):
            continue
        seen.add(key)
        aliases.append({"ac_number": ac, "block_name_en": block_name, "kind": "panchayat",
                        "area_name_en": gp_name, "alias": v["Village Name (In English)"].strip(),
                        "script": "en", "source": f"{SOURCE_VILLAGE}: village of {gp_name}"})

    # Hindi spellings from the polling-station list (official, printed in Hindi).
    ps_list = SEED / "ps_list" / "giridih_current_parts.csv"
    if ps_list.exists():
        from build_ps_list_current import key as latin_key

        by_code = {int(p["code"]): p for p in panchayats}
        seen_hi = set()
        for r in _rows(ps_list):
            if r["status"] != "matched" or not r["panchayat_code"].isdigit():
                continue
            gp = by_code.get(int(r["panchayat_code"]))
            if gp is None:
                continue
            if latin_key(r["village_lgd"]) == latin_key(gp["name_en"]) and gp["name_hi"] == gp["name_en"]:
                gp["name_hi"] = r["village_hi"]
                gp["source"] += "; Hindi name from the polling-station list"
            key = fold(r["village_hi"])
            if len(key) < MIN_ALIAS or key in seen_hi or key in reserved:
                continue
            seen_hi.add(key)
            aliases.append({"ac_number": gp["ac_number"], "block_name_en": gp["block_name_en"],
                            "kind": "panchayat", "area_name_en": gp["name_en"],
                            "alias": r["village_hi"], "script": "hi",
                            "source": f"polling-station list part {r['part_number']}: "
                                      f"{r['village_lgd']}, village of {gp['name_en']}"})

    return {"panchayats": panchayats, "aliases": aliases, "features": features,
            "new_blocks": sorted(new_blocks), "skipped": skipped}


def write(out: dict) -> None:
    def dump_csv(path: pathlib.Path, rows: list[dict], fields: list[str]) -> None:
        with path.open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
            w.writeheader()
            w.writerows(rows)

    dump_csv(SEED / "areas_panchayats.csv", out["panchayats"],
             ["ac_number", "block_name_en", "kind", "name_en", "name_hi", "code", "census_code",
              "source", "membership"])
    dump_csv(SEED / "area_aliases.csv", out["aliases"],
             ["ac_number", "block_name_en", "kind", "area_name_en", "alias", "script", "source"])

    blocks_path = SEED / "ac_blocks.csv"
    existing = _rows(blocks_path)
    names = {(r["ac_number"], r["name_en"]) for r in existing}
    hindi = {"Giridih": "गिरिडीह", "Pirtand": "पीरटांड़", "Gandey": "गांडेय",
             "Bengabad": "बेंगाबाद", "Dumri": "डुमरी"}
    for ac, block in out["new_blocks"]:
        name = f"{block} Block (part)"
        if (str(ac), name) not in names:
            existing.append({"ac_number": str(ac), "name_en": name,
                             "name_hi": f"{hindi.get(block, block)} प्रखंड (अंश)",
                             "kind": "rural", "source": "lgd-village-ac"})
    dump_csv(blocks_path, existing, ["ac_number", "name_en", "name_hi", "kind", "source"])

    geo = {"type": "FeatureCollection", "sources": ATTRIBUTION, "features": out["features"]}
    (SEED / "geo" / "areas.json").write_text(
        json.dumps(geo, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")


def main() -> int:
    out = build()
    write(out)
    per_ac = Counter(p["ac_number"] for p in out["panchayats"])
    print(f"panchayats: {len(out['panchayats'])} {dict(sorted(per_ac.items()))}")
    print(f"village aliases: {len(out['aliases'])}")
    print(f"polygons: {sum(f['properties']['kind'] == 'panchayat' for f in out['features'])} "
          f"panchayat, {sum(f['properties']['kind'] == 'ward' for f in out['features'])} ward")
    print(f"blocks split between constituencies: {out['new_blocks']}")
    for line in out["skipped"]:
        print(f"skipped: {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
