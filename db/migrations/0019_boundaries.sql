-- 0019: boundary polygons for constituencies and blocks (spec 3, "booth points +
-- panchayat/ward polygons where available").
--
-- `area.boundary` has existed since 0002 but nothing above it could hold a
-- shape, so the map had no constituency outline to scope itself by and no block
-- outline to show which booths belong where. Same representation as the area
-- column (D-006): a GeoJSON geometry in JSONB, no PostGIS. Nothing queries these
-- spatially; they are drawn, and their bounding box is used to fit the view.
--
-- `boundary_source` says where a shape came from, because none of them is
-- verified: the AC outlines are DataMeet's scrape of ECI (CC BY 2.5 IN), the
-- blocks are geoBoundaries ADM3 (ODbL), and both are written by
-- db/seed/load_seed.py from db/seed/geo/boundaries.json, which
-- scripts/build_boundaries.py produces.
--
-- `boundary_bbox` is [min_lon, min_lat, max_lon, max_lat], precomputed so the
-- API can fit the map without parsing the polygon.

ALTER TABLE ac
    ADD COLUMN boundary        JSONB,
    ADD COLUMN boundary_bbox   DOUBLE PRECISION[],
    ADD COLUMN boundary_source TEXT;

ALTER TABLE block
    ADD COLUMN boundary        JSONB,
    ADD COLUMN boundary_bbox   DOUBLE PRECISION[],
    ADD COLUMN boundary_source TEXT;

ALTER TABLE area
    ADD COLUMN boundary_source TEXT;

ALTER TABLE ac ADD CONSTRAINT ac_boundary_is_polygonal CHECK (
    boundary IS NULL OR boundary->>'type' IN ('Polygon', 'MultiPolygon'));
ALTER TABLE block ADD CONSTRAINT block_boundary_is_polygonal CHECK (
    boundary IS NULL OR boundary->>'type' IN ('Polygon', 'MultiPolygon'));
ALTER TABLE ac ADD CONSTRAINT ac_boundary_bbox_shape CHECK (
    boundary_bbox IS NULL OR array_length(boundary_bbox, 1) = 4);
ALTER TABLE block ADD CONSTRAINT block_boundary_bbox_shape CHECK (
    boundary_bbox IS NULL OR array_length(boundary_bbox, 1) = 4);
