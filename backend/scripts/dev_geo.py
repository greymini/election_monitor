#!/usr/bin/env python
"""Synthetic geography for the dev stack: area outlines and booth points.

    python -m scripts.dev_geo --ac 32

**Development only, and synthetic.** The dev stack's polling-station lists are
generated (`ingest/mock_documents.py`), so their buildings and villages exist
nowhere and cannot be geocoded; and no real panchayat or ward boundary is
published for this AC. Without this step every booth on the local stack has no
location and the map is empty, which tests nothing.

What it does, per block of the AC that has booths:

* takes the block's **real** outline from `block.boundary` (geoBoundaries,
  clipped to the AC by scripts/build_boundaries.py). The municipal corporation
  has no published boundary, so it gets the same disc around the town the
  frontend fixture uses;
* cuts that region into one cell per area with booths (k-means + Voronoi, the
  same routine as the fixture), and writes each cell to `area.boundary` with
  `boundary_source` saying it is synthetic;
* places every booth inside its own area's cell, with `geocode_source =
  'synthetic'` and a confidence of 0.05, so nothing downstream can mistake it for
  a geocode - `ingest.geocode --redo-weak` would retry every one of them.

It refuses to run against a database whose name does not contain "dev" or
"test", because writing invented coordinates over real geocodes would be a
silent, unrecoverable corruption of a real deployment.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.config import get_settings  # noqa: E402
from common.db import cursor, query, query_one  # noqa: E402

SOURCE = "synthetic: scripts/dev_geo.py (dev stack only)"
GEOCODE_SOURCE = "synthetic"
GEOCODE_CONF = 0.05


def database_name(url: str) -> str:
    """The database a connection URL names.

    Not `url.rsplit("/")`: pgserver on macOS and Linux connects over a Unix
    socket and puts the socket directory in the query string
    (postgresql://postgres:@/giridih_dev?host=/path/.devstack/pgdata), so the
    last path segment of the whole string is the socket directory, "pgdata".
    """
    from psycopg.conninfo import conninfo_to_dict

    return str(conninfo_to_dict(url).get("dbname") or "")


def refuse_unless_dev() -> None:
    name = database_name(get_settings().database_url)
    if "dev" not in name and "test" not in name:
        raise SystemExit(
            f"refusing to write synthetic geography into database {name!r}; "
            "dev_geo only runs against a dev or test database"
        )


def place(uid: str, cell, seed: int) -> tuple[float, float]:
    from shapely import prepared
    from shapely.geometry import Point

    rng = random.Random(f"{seed}:{uid}")
    inside = prepared.prep(cell)
    minx, miny, maxx, maxy = cell.bounds
    for _ in range(10_000):
        x, y = rng.uniform(minx, maxx), rng.uniform(miny, maxy)
        if inside.contains(Point(x, y)):
            return round(x, 5), round(y, 5)
    point = cell.representative_point()
    return round(point.x, 5), round(point.y, 5)


def run(ac_number: int, seed: int) -> dict:
    from shapely.geometry import Point, shape

    from scripts.build_boundaries import ULB_CENTRE, ULB_RADIUS, bbox, clean, lloyd_cells, rounded

    ac = query_one("SELECT ac_id FROM ac WHERE ac_number = %s", (ac_number,))
    if ac is None:
        raise SystemExit(f"AC {ac_number} is not seeded")
    blocks = query(
        "SELECT block_id, name_en, kind, boundary FROM block WHERE ac_id = %s "
        "ORDER BY block_id", (ac["ac_id"],))

    stats = {"areas": 0, "booths": 0, "blocks_skipped": []}
    for block in blocks:
        areas = query(
            "SELECT a.area_id, a.name_en FROM area a "
            "WHERE a.block_id = %s AND EXISTS (SELECT 1 FROM booth b "
            "  WHERE b.area_id = a.area_id AND b.is_active) "
            "ORDER BY a.name_en", (block["block_id"],))
        if not areas:
            continue
        if block["boundary"]:
            region = shape(block["boundary"])
        elif block["kind"] == "ulb" and ac_number == 32:
            region = Point(*ULB_CENTRE).buffer(ULB_RADIUS, quad_segs=24)
            geometry = clean(region)
            with cursor() as cur:
                cur.execute(
                    "UPDATE block SET boundary = %s::jsonb, boundary_bbox = %s, "
                    "boundary_source = %s WHERE block_id = %s",
                    (json.dumps(rounded(geometry)), bbox(geometry),
                     SOURCE + "; no published ULB boundary", block["block_id"]))
        else:
            stats["blocks_skipped"].append(block["name_en"])
            continue

        cells = ([region] if len(areas) == 1
                 else lloyd_cells(region, len(areas), seed=seed + block["block_id"]))
        with cursor() as cur:
            for area, cell in zip(areas, cells, strict=True):
                geometry = clean(cell)
                centre = geometry.representative_point()
                cur.execute(
                    "UPDATE area SET boundary = %s::jsonb, centroid_lon = %s, "
                    "centroid_lat = %s, boundary_source = %s WHERE area_id = %s",
                    (json.dumps(rounded(geometry)), round(centre.x, 5),
                     round(centre.y, 5), SOURCE, area["area_id"]))
                stats["areas"] += 1
                for booth in query(
                        "SELECT booth_uid FROM booth WHERE area_id = %s AND is_active "
                        "ORDER BY booth_uid", (area["area_id"],)):
                    lon, lat = place(booth["booth_uid"], geometry, seed)
                    cur.execute(
                        "UPDATE booth SET lon = %s, lat = %s, geocode_conf = %s, "
                        "geocode_source = %s WHERE booth_uid = %s",
                        (lon, lat, GEOCODE_CONF, GEOCODE_SOURCE, booth["booth_uid"]))
                    stats["booths"] += 1
    return stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Synthetic dev-stack geography")
    ap.add_argument("--ac", type=int, default=32)
    ap.add_argument("--seed", type=int, default=20241123)
    args = ap.parse_args(argv)
    refuse_unless_dev()
    stats = run(args.ac, args.seed)
    print(f"dev_geo: {stats['areas']} area outlines, {stats['booths']} booths placed"
          + (f"; no outline for {stats['blocks_skipped']}" if stats["blocks_skipped"] else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
