#!/usr/bin/env python
"""Build the boundary layers the map draws, from published open data.

    python scripts/build_boundaries.py            # download (once) and rebuild
    python scripts/build_boundaries.py --offline  # rebuild from raw/geo only

Needs `shapely` and `pyshp`, which are build-time only (requirements-dev.txt);
the API and the frontend read the committed JSON this writes and never import
either library.

**Sources.** Both are downloaded into `raw/geo/`, which is gitignored like the
rest of `raw/`, and nothing from them is committed except the simplified,
clipped shapes for the six constituencies:

* Assembly constituencies: DataMeet `assembly-constituencies/India_AC`, scraped
  from ECI's polling-station-locations site, CC BY 2.5 IN. Jharkhand did not
  adopt the 2008 delimitation, so its ACs still follow the 1976 order, and
  DataMeet's "pre delimitation" Jharkhand shapes are the right vintage. They are
  **not** verified: the README warns of shifts and wrong names, and the anchor
  check below found AC-32 does not contain Giridih town.
* CD blocks: geoBoundaries IND ADM3 (sub-districts), from Pathways / LGD, ODbL.
  In Jharkhand the sub-district is the CD block, and the names match
  `db/seed/ac_blocks.csv` up to spelling (SOURCE_BLOCK_NAMES).

**Outputs.**

* `db/seed/geo/boundaries.json` - the real layers: each AC's outline and every
  block that overlaps it by at least MIN_BLOCK_SHARE, clipped to the AC. Blocks
  the seed lists are matched to it by name; others are kept and marked
  `seeded: false`, because a block covering a quarter of a constituency is a
  fact about it whether or not the seed has caught up.
* `fixtures/geo/giridih_areas.json` - **synthetic** ward and panchayat polygons
  for the AC-32 fixture, which `fixtures/giridih.py` places its booths inside.
  The real panchayat list is not seeded (N9), so no real panchayat polygon can
  be attached to anything yet; these exist so the area layer and the "booth sits
  in its own area" rule can be reviewed. Every feature says `synthetic: true`.
* `frontend/src/fixtures/boundaries.json` - both, merged, for fixture mode.
  `tests/test_boundaries.py` checks it equals the other two.
"""

from __future__ import annotations

import argparse
import json
import math
import pathlib
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
RAW = ROOT / "raw" / "geo"
SEED_OUT = ROOT / "db" / "seed" / "geo" / "boundaries.json"
FIXTURE_OUT = ROOT / "fixtures" / "geo" / "giridih_areas.json"
WEB_OUT = ROOT.parent / "frontend" / "src" / "fixtures" / "boundaries.json"   # ROOT is backend/

sys.path.insert(0, str(ROOT))

DATAMEET = "https://raw.githubusercontent.com/datameet/maps/master/assembly-constituencies/India_AC"
GEOBOUNDARIES = (
    "https://github.com/wmgeolab/geoBoundaries/raw/9469f09/releaseData/gbOpen/IND/ADM3/"
    "geoBoundaries-IND-ADM3_simplified.geojson"
)

SOURCES = {
    "ac": {
        "name": "DataMeet assembly-constituencies (India_AC)",
        "url": "https://github.com/datameet/maps/tree/master/assembly-constituencies",
        "licence": "CC BY 2.5 IN",
        "attribution": "Assembly constituencies © DataMeet contributors, CC BY 2.5 IN",
        "vintage": "pre-2008 delimitation (current for Jharkhand)",
        "verified": False,
    },
    "block": {
        "name": "geoBoundaries IND ADM3 (sub-districts), simplified",
        "url": "https://www.geoboundaries.org/",
        "licence": "ODbL 1.0",
        "attribution": "CD blocks: geoBoundaries / Pathways / LGD, ODbL",
        "vintage": "2018 represented, built 2023",
        "verified": False,
    },
}

ACS = (31, 32, 33, 42, 61, 65)

# geoBoundaries spelling -> db/seed/ac_blocks.csv name.
SOURCE_BLOCK_NAMES = {
    "Giridih": "Giridih Block",
    "Pirtanr": "Pirtand Block",
    "Gande": "Gandey Block",
    "Bengabad": "Bengabad Block",
    "Dumri": "Dumri Block",
    "Nawadih": "Nawadih Block",
    "Chandrapura": "Chandrapura Block",
    "Tundi": "Tundi Block",
    "Purbi Tundi*": "Purvi Tundi Block",
    "Topchanchi": "Topchanchi Block",
    "Silli": "Silli Block",
    "Sonahatu": "Sonahatu Block",
    "Rahe": "Rahe Block",
    "Kanke": "Kanke Block",
    "Burmu": "Burmu Block",
}

# A block must cover this share of the AC to be drawn. Below it the overlap is
# usually a boundary sliver from two sources drawn at different scales.
MIN_BLOCK_SHARE = 0.03

# Douglas-Peucker tolerance in degrees (~55 m). Enough for a constituency map
# at zoom 9-13; it keeps the whole file to a few hundred kB.
SIMPLIFY = 0.0005
PRECISION = 5

# Points that must fall inside their AC. A failure is not fatal - this is the
# only open AC layer there is - but it is written into the output and shown on
# the map, so nobody reads the outline as authoritative.
ANCHORS = {32: ("Giridih town", 86.3094, 24.1854)}

# The fixture's municipal corporation. No open source publishes Giridih's ULB
# or ward boundaries (OSM has none either), so the fixture uses a disc around
# the town, clearly marked synthetic.
ULB_CENTRE = (86.3094, 24.1854)
ULB_RADIUS = 0.03


def fetch(url: str, dest: pathlib.Path) -> pathlib.Path:
    if dest.exists():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"downloading {url}")
    request = urllib.request.Request(url, headers={"User-Agent": "election-monitor-build"})
    with urllib.request.urlopen(request, timeout=300) as response:
        dest.write_bytes(response.read())
    return dest


def rounded(geom) -> dict:
    """GeoJSON geometry with coordinates rounded to PRECISION places."""
    from shapely.geometry import mapping

    def walk(value):
        if isinstance(value, (list, tuple)):
            if value and isinstance(value[0], (int, float)):
                return [round(value[0], PRECISION), round(value[1], PRECISION)]
            return [walk(v) for v in value]
        return value

    out = mapping(geom)
    return {"type": out["type"], "coordinates": walk(out["coordinates"])}


def clean(geom):
    """Simplified, valid, polygonal - or None if nothing is left."""
    from shapely.geometry import MultiPolygon, Polygon
    from shapely.validation import make_valid

    geom = make_valid(geom.simplify(SIMPLIFY, preserve_topology=True))
    if geom.geom_type == "GeometryCollection":
        polys = [g for g in geom.geoms if isinstance(g, (Polygon, MultiPolygon))]
        if not polys:
            return None
        from shapely.ops import unary_union
        geom = unary_union(polys)
    # Drop slivers left by clipping two sources against each other.
    if geom.geom_type == "MultiPolygon":
        parts = [p for p in geom.geoms if p.area > 1e-6]
        geom = MultiPolygon(parts) if len(parts) > 1 else (parts[0] if parts else None)
    return geom if geom is not None and not geom.is_empty else None


def bbox(geom) -> list[float]:
    return [round(v, PRECISION) for v in geom.bounds]


def label_point(geom) -> list[float]:
    point = geom.representative_point()
    return [round(point.x, PRECISION), round(point.y, PRECISION)]


def load_acs():
    import shapefile
    from shapely.geometry import shape
    from shapely.ops import unary_union

    for ext in ("shp", "shx", "dbf", "prj"):
        fetch(f"{DATAMEET}.{ext}", RAW / f"India_AC.{ext}")
    reader = shapefile.Reader(str(RAW / "India_AC"))
    pieces: dict[int, list] = {}
    names: dict[int, str] = {}
    for record in reader.iterShapeRecords():
        row = record.record.as_dict()
        if row["ST_NAME"] != "JHARKHAND" or row["AC_NO"] not in ACS:
            continue
        # An AC spanning two districts arrives as one piece per district.
        pieces.setdefault(row["AC_NO"], []).append(shape(record.shape.__geo_interface__))
        names[row["AC_NO"]] = row["AC_NAME"]
    missing = sorted(set(ACS) - set(pieces))
    if missing:
        raise SystemExit(f"DataMeet has no Jharkhand shape for AC {missing}")
    return {n: unary_union(p) for n, p in pieces.items()}, names


def load_blocks():
    from shapely.geometry import shape

    path = fetch(GEOBOUNDARIES, RAW / "geoBoundaries-IND-ADM3_simplified.geojson")
    data = json.loads(path.read_text(encoding="utf-8"))
    return [(f["properties"]["shapeName"], shape(f["geometry"])) for f in data["features"]]


def seed_blocks() -> dict[int, dict[str, dict]]:
    import csv

    out: dict[int, dict[str, dict]] = {}
    with (ROOT / "db" / "seed" / "ac_blocks.csv").open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            out.setdefault(int(row["ac_number"]), {})[row["name_en"]] = row
    return out


def build_real():
    from shapely.geometry import Point

    acs, source_names = load_acs()
    blocks = load_blocks()
    seeded = seed_blocks()

    ac_features, block_features, warnings = [], [], []
    for number in ACS:
        outline = acs[number]
        if number in ANCHORS:
            label, lon, lat = ANCHORS[number]
            if not outline.contains(Point(lon, lat)):
                km = round(outline.exterior.distance(Point(lon, lat)) * 111, 1)
                warnings.append({
                    "ac_number": number,
                    "code": "anchor_outside",
                    "params": {"label": label, "km": km},
                    "message": (
                        f"{label} lies {km} km outside the source outline for AC-{number}; "
                        "the source shape is probably misdrawn or mislabelled here. "
                        "Verify against the CEO Jharkhand AC map before relying on it."
                    ),
                })
        geometry = clean(outline)
        ac_features.append({
            "type": "Feature",
            "geometry": rounded(geometry),
            "properties": {
                "layer": "ac", "ac_number": number,
                "source_name": source_names[number],
                "bbox": bbox(geometry), "label_point": label_point(geometry),
                "source": "ac", "verified": False,
            },
        })

        for source_name, shape_ in blocks:
            if not shape_.intersects(outline):
                continue
            part = shape_.intersection(outline)
            share = part.area / outline.area
            if share < MIN_BLOCK_SHARE:
                continue
            geometry = clean(part)
            if geometry is None:
                continue
            seed_name = SOURCE_BLOCK_NAMES.get(source_name)
            # A block split between constituencies is seeded as "<Block> (part)"
            # in the one that holds the smaller share (scripts/build_panchayats.py).
            row = (seeded.get(number, {}).get(seed_name)
                   or seeded.get(number, {}).get(f"{seed_name} (part)")) if seed_name else None
            block_features.append({
                "type": "Feature",
                "geometry": rounded(geometry),
                "properties": {
                    "layer": "block", "ac_number": number,
                    "name_en": row["name_en"] if row else source_name.rstrip("*"),
                    "name_hi": row["name_hi"] if row else None,
                    "source_name": source_name,
                    "seeded": row is not None,
                    "share_of_ac": round(share, 3),
                    "share_of_block": round(part.area / shape_.area, 3),
                    "bbox": bbox(geometry), "label_point": label_point(geometry),
                    "source": "block", "verified": False,
                },
            })
        unmatched = [
            name for name in seeded.get(number, {})
            if name not in {f["properties"]["name_en"] for f in block_features
                            if f["properties"]["ac_number"] == number}
        ]
        for name in unmatched:
            warnings.append({
                "ac_number": number, "code": "seed_block_without_shape",
                "params": {"block": name},
                "message": f"{name} is seeded for AC-{number} but no source block matches it",
            })

    return acs, {
        "type": "FeatureCollection",
        "sources": SOURCES,
        "warnings": warnings,
        "features": ac_features + block_features,
    }


# ---------------------------------------------------------------------------
# The synthetic fixture areas
# ---------------------------------------------------------------------------


def lloyd_cells(region, count: int, seed: int):
    """`count` cells of roughly equal area tiling `region`.

    k-means over a dense grid of points inside the region, then the Voronoi
    diagram of the centres clipped back to it. Deterministic for a given seed.
    """
    import random

    from shapely import prepared
    from shapely.geometry import MultiPoint, Point
    from shapely.ops import voronoi_diagram

    rng = random.Random(seed)
    minx, miny, maxx, maxy = region.bounds
    inside = prepared.prep(region)
    step = math.sqrt(region.area / (count * 60))
    grid = [
        (x, y)
        for x in [minx + step * (i + 0.5) for i in range(int((maxx - minx) / step) + 1)]
        for y in [miny + step * (j + 0.5) for j in range(int((maxy - miny) / step) + 1)]
        if inside.contains(Point(x, y))
    ]
    centres = rng.sample(grid, count)
    for _ in range(40):
        groups: list[list[tuple[float, float]]] = [[] for _ in centres]
        for x, y in grid:
            k = min(range(len(centres)),
                    key=lambda c: (centres[c][0] - x) ** 2 + (centres[c][1] - y) ** 2)
            groups[k].append((x, y))
        centres = [
            (sum(p[0] for p in g) / len(g), sum(p[1] for p in g) / len(g)) if g else centres[i]
            for i, g in enumerate(groups)
        ]
    cells = voronoi_diagram(MultiPoint(centres), envelope=region.envelope.buffer(0.1))
    by_centre = []
    for cx, cy in centres:
        cell = next(c for c in cells.geoms if c.contains(Point(cx, cy)))
        by_centre.append(((cx, cy), cell.intersection(region)))
    # North-west to south-east, so neighbouring numbers are neighbouring areas.
    by_centre.sort(key=lambda item: (round(-item[0][1], 2), item[0][0]))
    return [cell for _, cell in by_centre]


def build_fixture(acs) -> dict:
    from shapely.geometry import Point
    from shapely.ops import unary_union

    from fixtures import giridih

    outline = acs[giridih.AC_NUMBER]
    real = {name: shape_ for name, shape_ in load_blocks()}
    ulb = Point(*ULB_CENTRE).buffer(ULB_RADIUS, quad_segs=24)
    regions = {
        "Giridih Municipal Corporation": ulb,
        "Giridih Block": real["Giridih"].intersection(outline).difference(ulb),
        "Pirtand Block": real["Pirtanr"].intersection(outline).difference(ulb),
    }

    features = []
    ulb_geom = clean(ulb)
    features.append({
        "type": "Feature",
        "geometry": rounded(ulb_geom),
        "properties": {
            "layer": "block", "ac_number": giridih.AC_NUMBER,
            "name_en": "Giridih Municipal Corporation", "name_hi": "गिरिडीह नगर निगम",
            "seeded": True, "synthetic": True,
            "bbox": bbox(ulb_geom), "label_point": label_point(ulb_geom),
            "source": "fixture", "verified": False,
        },
    })
    for block_en, region in regions.items():
        members = [a for a in giridih.areas() if a.block_en == block_en]
        cells = lloyd_cells(region, len(members), seed=giridih.SEED + len(block_en))
        for area, cell in zip(members, cells, strict=True):
            geometry = clean(cell)
            features.append({
                "type": "Feature",
                "geometry": rounded(geometry),
                "properties": {
                    "layer": "area", "ac_number": giridih.AC_NUMBER,
                    "name_en": area.area_en, "name_hi": area.area_hi,
                    "kind": area.kind, "block_en": block_en,
                    "synthetic": True,
                    "bbox": bbox(geometry), "label_point": label_point(geometry),
                    "source": "fixture", "verified": False,
                },
            })
    covered = unary_union([f for f in regions.values()])
    return {
        "type": "FeatureCollection",
        "note": (
            "SYNTHETIC. Ward and panchayat polygons for the AC-32 fixture only, cut from "
            "the real CD block shapes (and a disc around the town for the municipal "
            "corporation) so the area layer can be reviewed. Not real geography."
        ),
        "covered_bbox": bbox(covered),
        "features": features,
    }


def dump(path: pathlib.Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n",
                    encoding="utf-8")
    print(f"wrote {path.relative_to(ROOT.parent)} ({path.stat().st_size // 1024} kB, "
          f"{len(data['features'])} features)")


def merged(real: dict, fixture: dict) -> dict:
    """What fixture mode serves: the real layers plus the synthetic areas, with
    the fixture's municipal corporation standing in for the unpublished ULB."""
    return {
        "type": "FeatureCollection",
        "sources": real["sources"],
        "warnings": real["warnings"],
        "fixture_note": fixture["note"],
        "features": real["features"] + fixture["features"],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--offline", action="store_true", help="use raw/geo only, never download")
    args = ap.parse_args(argv)
    if args.offline:
        global fetch

        def fetch(url: str, dest: pathlib.Path) -> pathlib.Path:  # noqa: F811
            if not dest.exists():
                raise SystemExit(f"--offline and {dest} is missing")
            return dest

    acs, real = build_real()
    fixture = build_fixture(acs)
    dump(SEED_OUT, real)
    dump(FIXTURE_OUT, fixture)
    dump(WEB_OUT, merged(real, fixture))
    for warning in real["warnings"]:
        print(f"WARNING AC-{warning['ac_number']}: {warning['message']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
