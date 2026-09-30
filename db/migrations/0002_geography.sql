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
    geom        geometry(MultiPolygon, 4326),
    UNIQUE (block_id, kind, name_en)
);
CREATE INDEX area_block_idx ON area (block_id);
CREATE INDEX area_geom_idx  ON area USING gist (geom);

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
    geom                geometry(Point, 4326),
    geocode_conf        REAL CHECK (geocode_conf BETWEEN 0 AND 1),
    geocode_source      TEXT,
    is_active           BOOLEAN NOT NULL DEFAULT true,
    notes               TEXT,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX booth_area_idx ON booth (area_id);
CREATE INDEX booth_geom_idx ON booth USING gist (geom);
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
