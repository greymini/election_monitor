-- 0028: provenance metadata on source_doc; directory tables for Places page.

ALTER TABLE source_doc
    ADD COLUMN IF NOT EXISTS method TEXT
        CHECK (method IS NULL OR method IN ('published', 'modelled')),
    ADD COLUMN IF NOT EXISTS method_note TEXT;

COMMENT ON COLUMN source_doc.method IS 'published = primary source; modelled = deterministic estimate, not test data.';
COMMENT ON COLUMN source_doc.method_note IS 'One-line method description shown in the UI.';

CREATE TABLE IF NOT EXISTS ps_current_part (
    part_id       SERIAL PRIMARY KEY,
    ac_id         INT NOT NULL REFERENCES ac(ac_id) ON DELETE CASCADE,
    part_number   INT NOT NULL,
    building_hi   TEXT,
    village_hi    TEXT,
    block_id      INT REFERENCES block(block_id),
    village_lgd   TEXT,
    village_code  TEXT,
    area_id       INT REFERENCES area(area_id),
    match_score   REAL,
    match_status  TEXT,
    note          TEXT,
    source        TEXT NOT NULL,
    UNIQUE (ac_id, part_number)
);

CREATE INDEX IF NOT EXISTS ps_current_part_ac_idx ON ps_current_part (ac_id);
CREATE INDEX IF NOT EXISTS ps_current_part_area_idx ON ps_current_part (area_id);

CREATE TABLE IF NOT EXISTS gp_official (
    official_id   SERIAL PRIMARY KEY,
    area_id       INT NOT NULL REFERENCES area(area_id) ON DELETE CASCADE,
    gp_lgd_code   TEXT,
    portal_role   TEXT NOT NULL,
    office        TEXT NOT NULL,
    name          TEXT NOT NULL,
    source        TEXT NOT NULL,
    fetched_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS gp_official_area_idx ON gp_official (area_id);

GRANT SELECT ON ps_current_part, gp_official TO giridih_ro;

DROP POLICY IF EXISTS giridih_ro_select ON ps_current_part;
CREATE POLICY giridih_ro_select ON ps_current_part FOR SELECT TO giridih_ro USING (true);
DROP POLICY IF EXISTS giridih_ro_select ON gp_official;
CREATE POLICY giridih_ro_select ON gp_official FOR SELECT TO giridih_ro USING (true);
