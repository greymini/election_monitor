-- 0001: extensions and shared helpers
--
-- One extension: pgvector, for the news embedding columns. That is the whole
-- requirement, and it means the schema applies on any stock PostgreSQL 16 with
-- pgvector - including a pip-installed one - so every SQL test can run without
-- Docker or administrator rights. N4.
--
-- Three extensions were removed here, and it is worth recording what each was
-- actually doing:
--
--   * **pg_trgm** and **unaccent** were created and then never used. No
--     trigram index, no `similarity()`, no `unaccent()` call anywhere in the
--     migrations or the application. They were speculative.
--
--   * **postgis** was real: `area.geom` and `booth.geom` were geometry
--     columns, and `GET /booths` and `ingest/geocode.py` called ST_X, ST_Y,
--     ST_MakePoint, ST_SetSRID and ST_Centroid. 0002 now stores longitude and
--     latitude as plain double precision, and area boundaries as GeoJSON in
--     jsonb, because that is all the use those columns were being put to - see
--     the note at the top of 0002 and D-006.
--
-- The trade is deliberate and narrow: no spatial *query* is possible without
-- PostGIS, and none is performed. The day one is needed - a genuine
-- point-in-polygon, a distance ordering, a tile cut - this decision has to be
-- revisited, and D-006 says so. docker/Dockerfile.db still provides PostGIS so
-- that day needs no image change.

CREATE EXTENSION IF NOT EXISTS vector;

-- Schema-version bookkeeping (plain numbered migrations, no sqitch dependency)
CREATE TABLE IF NOT EXISTS schema_migration (
    filename    TEXT PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    checksum    TEXT
);

-- Generic updated_at trigger
CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
