-- 0002: geographic backbone (HLD 3, LLD 3)
-- Booth (polling station) is the atomic unit. Everything rolls up
-- booth -> area (panchayat/ward) -> block -> AC.

CREATE TABLE block (
    block_id    SMALLINT PRIMARY KEY,
    name_en     TEXT NOT NULL,
    name_hi     TEXT NOT NULL,
    kind        TEXT NOT NULL CHECK (kind IN ('rural', 'ulb'))
);

CREATE TABLE area (
    area_id     SERIAL PRIMARY KEY,
    block_id    SMALLINT NOT NULL REFERENCES block(block_id),
    kind        TEXT NOT NULL CHECK (kind IN ('panchayat', 'ward')),
    name_en     TEXT NOT NULL,
    name_hi     TEXT NOT NULL,
    code        TEXT,
    census_code TEXT,
    -- N4. Was `geom geometry(MultiPolygon, 4326)`, which required PostGIS and
     -- was never populated by anything: no loader wrote it, so the area-centroid
     -- geocoding fallback that read it could not fire. Boundaries are held as a
     -- GeoJSON geometry object so the map can draw them when they are loaded,
     -- and the centroid is stored explicitly rather than computed by
     -- ST_Centroid - the only thing the polygon was ever read for.
     boundary        JSONB,
     centroid_lon    DOUBLE PRECISION CHECK (centroid_lon BETWEEN -180 AND 180),
     centroid_lat    DOUBLE PRECISION CHECK (centroid_lat BETWEEN -90 AND 90),
     CONSTRAINT area_centroid_both_or_neither CHECK (
         (centroid_lon IS NULL) = (centroid_lat IS NULL)
     ),
    UNIQUE (block_id, kind, name_en)
);
CREATE INDEX area_block_idx ON area (block_id);

-- Maps free-text place names (news labelling, ground reports, roll headers,
-- transliterations) to a canonical area. LLD 6.4.
CREATE TABLE area_alias (
    alias   TEXT PRIMARY KEY,
    area_id INT NOT NULL REFERENCES area(area_id) ON DELETE CASCADE,
    script  TEXT NOT NULL CHECK (script IN ('hi', 'en')),
    source  TEXT
);
CREATE INDEX area_alias_area_idx ON area_alias (area_id);

CREATE TABLE booth (
    booth_uid           TEXT PRIMARY KEY,
    area_id             INT NOT NULL REFERENCES area(area_id),
    ps_name_en          TEXT,
    ps_name_hi          TEXT,
    building            TEXT,
    village_or_locality TEXT,
    current_ps_number   INT,
    -- N4. Was `geom geometry(Point, 4326)`. Read only by ST_X/ST_Y to hand
     -- longitude and latitude to the map, and written only from a
     -- longitude/latitude pair, so the geometry type was a round trip through
     -- PostGIS to get back the two numbers that went in. CHECKed ranges catch a
     -- transposed pair, which the geometry type did not.
     lon                 DOUBLE PRECISION CHECK (lon BETWEEN -180 AND 180),
     lat                 DOUBLE PRECISION CHECK (lat BETWEEN -90 AND 90),
     CONSTRAINT booth_lonlat_both_or_neither CHECK ((lon IS NULL) = (lat IS NULL)),
     geocode_conf        REAL CHECK (geocode_conf BETWEEN 0 AND 1),
    geocode_source      TEXT,
    is_active           BOOLEAN NOT NULL DEFAULT true,
    notes               TEXT,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX booth_area_idx ON booth (area_id);
-- A plain composite index, not GiST. The map's query is "every located booth in
-- this AC", which is a partial-index lookup, and a bounding box on a few
-- thousand booths per constituency is a scan either way.
CREATE INDEX booth_located_idx ON booth (lon, lat) WHERE lon IS NOT NULL;
CREATE TRIGGER booth_touch BEFORE UPDATE ON booth
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- PS numbers are re-numbered, split and merged at every revision (HLD 3).
-- Nothing multi-year may be compared without going through this table.
CREATE TABLE booth_crosswalk (
    election_id  INT NOT NULL,
    ps_number    INT NOT NULL,
    booth_uid    TEXT NOT NULL REFERENCES booth(booth_uid),
    match_method TEXT NOT NULL CHECK (match_method IN ('anchor', 'exact', 'fuzzy', 'split', 'merge', 'manual', 'new')),
    confidence   REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    reviewed     BOOLEAN NOT NULL DEFAULT false,
    evidence     JSONB,
    PRIMARY KEY (election_id, ps_number)
);
CREATE INDEX booth_crosswalk_booth_idx ON booth_crosswalk (booth_uid);
CREATE INDEX booth_crosswalk_conf_idx  ON booth_crosswalk (confidence) WHERE reviewed = false;

-- PS list rows exactly as published, per election/revision. Crosswalk input.
CREATE TABLE ps_list_entry (
    election_id         INT NOT NULL,
    ps_number           INT NOT NULL,
    ps_name             TEXT,
    building            TEXT,
    village_or_locality TEXT,
    area_hint           TEXT,
    roll_part           INT,
    source_doc          TEXT,
    source_page         INT,
    PRIMARY KEY (election_id, ps_number)
);
