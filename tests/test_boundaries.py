"""The boundary files the map draws, and the fixture booths placed inside them.

Pure JSON and pure Python: the shapes are built offline by
`scripts/build_boundaries.py` (which needs shapely), and nothing here does, so
the suite still runs anywhere.
"""

from __future__ import annotations

import csv
import json
import pathlib

from fixtures import giridih

ROOT = pathlib.Path(__file__).resolve().parents[1]
SEED = ROOT / "db" / "seed" / "geo" / "boundaries.json"
FIXTURE = ROOT / "fixtures" / "geo" / "giridih_areas.json"
WEB = ROOT / "web" / "src" / "fixtures" / "boundaries.json"

ACS = {31, 32, 33, 42, 61, 65}
# Roughly Jharkhand. A transposed or unprojected coordinate lands far outside.
LON, LAT = (83.0, 88.5), (21.5, 25.5)


def load(path: pathlib.Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def coords(geometry: dict):
    polygons = (geometry["coordinates"] if geometry["type"] == "MultiPolygon"
                else [geometry["coordinates"]])
    for polygon in polygons:
        for ring in polygon:
            yield from ring


def test_every_constituency_has_an_outline():
    outlines = {f["properties"]["ac_number"]: f for f in load(SEED)["features"]
                if f["properties"]["layer"] == "ac"}
    assert set(outlines) == ACS


def test_every_shape_is_polygonal_and_in_jharkhand():
    for path in (SEED, FIXTURE):
        for feature in load(path)["features"]:
            geometry, props = feature["geometry"], feature["properties"]
            assert geometry["type"] in {"Polygon", "MultiPolygon"}, props
            for lon, lat in coords(geometry):
                assert LON[0] < lon < LON[1] and LAT[0] < lat < LAT[1], (props, lon, lat)
            minx, miny, maxx, maxy = props["bbox"]
            assert minx < maxx and miny < maxy, props


def test_nothing_claims_to_be_verified():
    """Neither source has been checked against CEO Jharkhand's maps. A shape
    marked verified would be a false statement the map then repeats."""
    for path in (SEED, FIXTURE):
        assert not any(f["properties"]["verified"] for f in load(path)["features"])


def test_every_seeded_rural_block_has_a_shape_or_a_warning():
    data = load(SEED)
    drawn = {(f["properties"]["ac_number"], f["properties"]["name_en"])
             for f in data["features"] if f["properties"]["layer"] == "block"}
    warned = {(w["ac_number"], w["params"]["block"]) for w in data["warnings"]
              if w["code"] == "seed_block_without_shape"}
    with (ROOT / "db" / "seed" / "ac_blocks.csv").open(encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            key = (int(row["ac_number"]), row["name_en"])
            assert key in drawn or key in warned, key


def test_the_giridih_outline_problem_is_reported_not_hidden():
    """DataMeet's AC-32 does not contain Giridih town. Until a better source is
    loaded, that has to reach the map as a warning."""
    warnings = load(SEED)["warnings"]
    assert any(w["ac_number"] == 32 and w["code"] == "anchor_outside" for w in warnings)


def test_synthetic_areas_say_so():
    for feature in load(FIXTURE)["features"]:
        assert feature["properties"]["synthetic"] is True
        assert feature["properties"]["source"] == "fixture"


def test_web_copy_is_the_two_files_merged():
    """Fixture mode reads the web copy; the API reads the seed. Editing one and
    not rebuilding the other would show different maps in the two modes."""
    seed, fixture, web = load(SEED), load(FIXTURE), load(WEB)
    assert web["features"] == seed["features"] + fixture["features"]
    assert web["warnings"] == seed["warnings"]
    assert web["sources"] == seed["sources"]


def test_files_stay_small():
    """The fixture copy is fetched by the browser. 300 kB is the budget the
    research set for every layer of an AC; the real file serves six."""
    for path in (SEED, FIXTURE, WEB):
        assert path.stat().st_size < 300_000, path


def test_every_placed_fixture_booth_is_inside_its_own_area():
    """The bug this file exists for: booths drawn outside the region they were
    labelled with."""
    shapes = giridih.area_polygons()
    outside = [
        b.booth_uid for b in giridih.booths()
        if b.lat is not None and not giridih.point_in_geometry(b.lon, b.lat, shapes[b.area_en])
    ]
    assert outside == []


def test_every_fixture_area_has_exactly_one_polygon():
    names = [f["properties"]["name_en"] for f in load(FIXTURE)["features"]
             if f["properties"]["layer"] == "area"]
    assert sorted(names) == sorted(a.area_en for a in giridih.areas())


def test_point_in_geometry_handles_holes():
    square = [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]
    hole = [[4, 4], [6, 4], [6, 6], [4, 6], [4, 4]]
    geometry = {"type": "Polygon", "coordinates": [square, hole]}
    assert giridih.point_in_geometry(1, 1, geometry)
    assert not giridih.point_in_geometry(5, 5, geometry)
    assert not giridih.point_in_geometry(11, 5, geometry)
