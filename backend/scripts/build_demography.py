#!/usr/bin/env python
"""Census 2011 population, SC, ST, literates and households for every panchayat.

    python scripts/build_demography.py

**Source.** Census of India 2011 Primary Census Abstract, Giridih district
(census district 349), village level: `db/seed/census/village_pca_giridih_pirtand.csv`,
extracted from `PCA_CDB-2004-F-Census.xlsx` (censusindia.gov.in NADA catalog
41027). Its block totals reconcile exactly with the published CD-block rows.

**Join.** Each LGD village carries its Census 2011 village code
(`raw/geo/lgd/lgd_30Sep2026_giridih_villages.csv`), and each panchayat in
`db/seed/areas_panchayats.csv` its LGD code, so a panchayat's figures are the
sum of its villages. A village of 2011 that LGD no longer lists under a
panchayat (absorbed by the Municipal Corporation, or a census town) is not in
any panchayat's sum. These are 2011 counts of residents, not electors.

Writes `db/seed/census/demography_ac<NN>.csv`, one per constituency, in the
format `python -m ingest.load_csv demography <csv> --ac NN` reads.
"""

from __future__ import annotations

import csv
import pathlib
import sys
from collections import defaultdict

ROOT = pathlib.Path(__file__).resolve().parents[1]
SEED = ROOT / "db" / "seed"
PCA = SEED / "census" / "village_pca_giridih_pirtand.csv"
VILLAGES = ROOT / "raw" / "geo" / "lgd" / "lgd_30Sep2026_giridih_villages.csv"
FIELDS = ["area_name", "census_year", "population", "sc", "st", "literate", "main_workers", "households"]


def _rows(path: pathlib.Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def build() -> dict[str, list[dict]]:
    pca = {r["village_code_2011"]: r for r in _rows(PCA) if r["level"] == "VILLAGE"}
    gp_of = defaultdict(list)
    for v in _rows(VILLAGES):
        if v["Local Body Code"] not in ("", "0") and v["Village Census 2011 Code"] in pca:
            gp_of[v["Local Body Code"]].append(pca[v["Village Census 2011 Code"]])
    out: dict[str, list[dict]] = defaultdict(list)
    for gp in _rows(SEED / "areas_panchayats.csv"):
        villages = gp_of.get(gp["code"])
        if not villages:
            continue
        total = {k: sum(int(v[k] or 0) for v in villages)
                 for k in ("population", "sc", "st", "literates", "households")}
        out[gp["ac_number"]].append({
            "area_name": gp["name_en"], "census_year": 2011, "population": total["population"],
            "sc": total["sc"], "st": total["st"], "literate": total["literates"],
            "main_workers": "", "households": total["households"]})
    return out


def main() -> int:
    for ac, rows in sorted(build().items()):
        path = SEED / "census" / f"demography_ac{ac}.csv"
        with path.open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
            w.writeheader()
            w.writerows(sorted(rows, key=lambda r: r["area_name"]))
        print(f"AC {ac}: {len(rows)} panchayats, population {sum(r['population'] for r in rows):,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
