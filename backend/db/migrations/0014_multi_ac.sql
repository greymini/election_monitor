-- 0014: multi-constituency spine (MULTI_AC_EXPANSION_SPEC 2)
--
-- The system stops being "a Giridih tool with tables" and becomes "a
-- constituency-keyed platform with Giridih as one tenant". The spec is blunt
-- about why there is no partial version of this: a half-scoped schema is worse
-- than the single-AC one, because it silently mixes constituencies.
--
-- Five things happen here.
--
-- 1. A spine: state -> district / pc -> ac, with ac_district for Dumri, which
--    spans Giridih and Bokaro.
-- 2. Elections become two-level. An event (VS 2024) is shared; the contest is
--    per AC. For a Lok Sabha event the per-AC row is the *segment* of the PC
--    contest that falls inside that assembly seat.
-- 3. ac_id lands on every table that holds constituency-specific data.
-- 4. booth_uid becomes '{ac_number}-B{nnnn}', minted from a per-AC sequence.
--    This closes audit B3 structurally: the old code minted 'B9001' from a
--    counter that restarted at zero on every invocation, so crosswalking a
--    second historic election bound its stations onto booths created for the
--    first, and two unrelated polling stations' votes were summed onto one
--    booth_uid by mv_result_booth_party's GROUP BY. A per-AC sequence cannot
--    collide, and a UID that carries its AC cannot collide across ACs either.
-- 5. Per-AC contest pairs, so no view or function hardcodes JMM/BJP.
--
-- The existing Giridih data is migrated, not dropped, and the row counts are
-- asserted before and after with RAISE EXCEPTION on mismatch.
--
-- IMPORTANT: this migration DROPS all ten materialized views. They select from
-- `election`, whose shape changes here, so they cannot survive. 0015 rebuilds
-- them with ac_id leading and the canonical metric definitions. The two
-- migrations must be applied together; apply_migrations does that by default
-- since it runs every pending file in order.
--
-- No transaction control: apply_migrations wraps each file in one transaction.

-- ---------------------------------------------------------------------------
-- 0. Drop the views that depend on the shapes we are about to change.
-- ---------------------------------------------------------------------------

DROP MATERIALIZED VIEW IF EXISTS mv_area_rollup;
DROP MATERIALIZED VIEW IF EXISTS mv_booth_priority;
DROP MATERIALIZED VIEW IF EXISTS mv_floating_vote;
DROP MATERIALIZED VIEW IF EXISTS mv_new_voter_share;
DROP MATERIALIZED VIEW IF EXISTS mv_volatility;
DROP MATERIALIZED VIEW IF EXISTS mv_transfer_ls_vs;
DROP MATERIALIZED VIEW IF EXISTS mv_swing;
DROP MATERIALIZED VIEW IF EXISTS mv_result_booth_wide;
DROP MATERIALIZED VIEW IF EXISTS mv_booth_party_share;
DROP MATERIALIZED VIEW IF EXISTS mv_result_booth_party;

-- ---------------------------------------------------------------------------
-- 1. Spine
-- ---------------------------------------------------------------------------

CREATE TABLE state (
    state_id     SMALLINT PRIMARY KEY,
    name_en      TEXT NOT NULL,
    name_hi      TEXT NOT NULL,
    ceo_base_url TEXT,
    sec_base_url TEXT
);

CREATE TABLE district (
    district_id SERIAL PRIMARY KEY,
    state_id    SMALLINT NOT NULL REFERENCES state(state_id),
    name_en     TEXT NOT NULL,
    name_hi     TEXT NOT NULL,
    lgd_code    INT,
    UNIQUE (state_id, name_en)
);

CREATE TABLE pc (
    pc_id       SERIAL PRIMARY KEY,
    state_id    SMALLINT NOT NULL REFERENCES state(state_id),
    pc_number   SMALLINT NOT NULL,
    name_en     TEXT NOT NULL,
    name_hi     TEXT NOT NULL,
    reservation TEXT CHECK (reservation IN ('GEN', 'SC', 'ST')),
    UNIQUE (state_id, pc_number)
);

CREATE TABLE ac (
    ac_id       SERIAL PRIMARY KEY,
    state_id    SMALLINT NOT NULL REFERENCES state(state_id),
    ac_number   SMALLINT NOT NULL,
    name_en     TEXT NOT NULL,
    name_hi     TEXT NOT NULL,
    reservation TEXT NOT NULL CHECK (reservation IN ('GEN', 'SC', 'ST')),
    pc_id       INT REFERENCES pc(pc_id),
    is_active   BOOLEAN NOT NULL DEFAULT true,
    bypoll_due  DATE,
    vacancy_date DATE,
    -- false until a human has reconciled this row against ECI/CEO publications.
    -- The spec forbids inventing constituency facts, so the five new ACs are
    -- seeded from secondary sources and the UI shows an "unverified" badge.
    verified    BOOLEAN NOT NULL DEFAULT false,
    notes       TEXT,
    UNIQUE (state_id, ac_number)
);
CREATE INDEX ac_active_idx ON ac (is_active) WHERE is_active;

CREATE TABLE ac_district (
    ac_id       INT NOT NULL REFERENCES ac(ac_id) ON DELETE CASCADE,
    district_id INT NOT NULL REFERENCES district(district_id) ON DELETE CASCADE,
    -- Dumri spans Giridih and Bokaro; one district is the primary for display.
    is_primary  BOOLEAN NOT NULL DEFAULT false,
    PRIMARY KEY (ac_id, district_id)
);

-- ---------------------------------------------------------------------------
-- 2. Elections become two-level
-- ---------------------------------------------------------------------------

CREATE TABLE election_event (
    event_id   SERIAL PRIMARY KEY,
    type       TEXT NOT NULL CHECK (type IN ('VS', 'LS', 'PANCHAYAT', 'ULB')),
    year       SMALLINT NOT NULL,
    label      TEXT NOT NULL UNIQUE,
    poll_dates DATE[],
    count_date DATE,
    is_bypoll  BOOLEAN NOT NULL DEFAULT false,
    UNIQUE (type, year, is_bypoll)
);

-- Populate events from the existing election rows before reshaping them.
INSERT INTO election_event (type, year, label, count_date)
SELECT DISTINCT
       CASE WHEN e.type = 'WARD' THEN 'ULB' ELSE e.type END,
       e.year,
       -- 'VS-2024', and 'LS-2024' for what was 'LS-2024 (AC seg)': the event is
       -- the parliamentary election, the segment is the per-AC row below.
       CASE WHEN e.type = 'WARD' THEN 'ULB-' ELSE e.type || '-' END || e.year,
       e.poll_date
FROM election e
ON CONFLICT (label) DO NOTHING;

ALTER TABLE election
    ADD COLUMN event_id INT REFERENCES election_event(event_id),
    ADD COLUMN ac_id    INT REFERENCES ac(ac_id),
    ADD COLUMN phase    SMALLINT,
    ADD COLUMN electors_published        INT,
    ADD COLUMN votes_polled_published    INT;

COMMENT ON TABLE election IS
    'One contest: an election_event as fought in one AC. For an LS event this is '
    'the AC segment of the PC contest.';

-- ---------------------------------------------------------------------------
-- 3. Party aliases and per-event alliances
-- ---------------------------------------------------------------------------

-- What actually fixes audit C1. Form 20 headers, ECI result pages, news text
-- and TCPD exports all spell parties differently, and resolve_candidates only
-- ever looked at `abbr` and `name_en` - never `name_hi`, which is seeded for
-- every party. A Devanagari header could not match by construction.
CREATE TABLE party_alias (
    alias    TEXT PRIMARY KEY,
    party_id INT NOT NULL REFERENCES party(party_id) ON DELETE CASCADE,
    script   TEXT NOT NULL CHECK (script IN ('hi', 'en', 'other')),
    source   TEXT
);
CREATE INDEX party_alias_party_idx ON party_alias (party_id);

-- Alliances change: JVM merged into BJP in 2020, AJSU has been in and out of the
-- NDA, JLKM is new in 2024. A single alliance_2024 column on `party` cannot
-- express that, and the scenario engine needs the alliance as it stood at the
-- event being modelled.
CREATE TABLE party_alliance (
    party_id INT NOT NULL REFERENCES party(party_id) ON DELETE CASCADE,
    event_id INT NOT NULL REFERENCES election_event(event_id) ON DELETE CASCADE,
    alliance TEXT NOT NULL,
    PRIMARY KEY (party_id, event_id)
);

-- Seed alliances for existing events from the column being retired, so nothing
-- is lost. The column itself stays for one release; analytics reads the table.
INSERT INTO party_alliance (party_id, event_id, alliance)
SELECT p.party_id, ev.event_id, p.alliance_2024
FROM party p
CROSS JOIN election_event ev
WHERE p.alliance_2024 IS NOT NULL AND ev.year = 2024
ON CONFLICT DO NOTHING;

COMMENT ON COLUMN party.alliance_2024 IS
    'Retired: use party_alliance, which is per event. Kept for one release.';

-- ---------------------------------------------------------------------------
-- 4. Seed the state and Giridih itself, so existing rows have an ac_id to take
-- ---------------------------------------------------------------------------

INSERT INTO state (state_id, name_en, name_hi, ceo_base_url, sec_base_url)
VALUES (20, 'Jharkhand', 'झारखंड', 'https://ceo.jharkhand.gov.in',
        'https://jharkhandsec.gov.in')
ON CONFLICT (state_id) DO NOTHING;

INSERT INTO district (state_id, name_en, name_hi) VALUES
    (20, 'Giridih', 'गिरिडीह'),
    (20, 'Bokaro',  'बोकारो'),
    (20, 'Dhanbad', 'धनबाद'),
    (20, 'Ranchi',  'रांची'),
    (20, 'Koderma', 'कोडरमा')
ON CONFLICT (state_id, name_en) DO NOTHING;

INSERT INTO pc (state_id, pc_number, name_en, name_hi, reservation) VALUES
    (20, 11, 'Giridih', 'गिरिडीह', 'GEN'),
    (20,  4, 'Kodarma', 'कोडरमा',  'GEN'),
    (20,  7, 'Ranchi',  'रांची',   'GEN')
ON CONFLICT (state_id, pc_number) DO NOTHING;

-- Giridih is verified=true only for the facts already reconciled in the HLD and
-- checked against the published 2024 result. The other five arrive in the seed
-- CSV with verified=false.
INSERT INTO ac (state_id, ac_number, name_en, name_hi, reservation, pc_id,
                bypoll_due, vacancy_date, verified, notes)
SELECT 20, 32, 'Giridih', 'गिरिडीह', 'GEN', pc.pc_id,
       DATE '2027-03-06', DATE '2026-09-06', true,
       'Sitting MLA died 6 Sep 2026; ECI must poll within six months.'
FROM pc WHERE pc.state_id = 20 AND pc.pc_number = 11
ON CONFLICT (state_id, ac_number) DO NOTHING;

INSERT INTO ac_district (ac_id, district_id, is_primary)
SELECT a.ac_id, d.district_id, true
FROM ac a JOIN district d ON d.state_id = a.state_id AND d.name_en = 'Giridih'
WHERE a.ac_number = 32
ON CONFLICT DO NOTHING;

-- ---------------------------------------------------------------------------
-- 5. ac_id on every constituency-scoped table (spec 2.2)
-- ---------------------------------------------------------------------------

-- block_id stays a globally unique SMALLINT. db/seed/load_seed.py mints it as
-- ac_number * 100 + n so the id is legible (3201 is Giridih's first block) and
-- cannot collide across constituencies. 65 * 100 + 17 is well inside SMALLINT.
ALTER TABLE block             ADD COLUMN ac_id INT REFERENCES ac(ac_id);
ALTER TABLE area              ADD COLUMN ac_id INT REFERENCES ac(ac_id);
ALTER TABLE booth             ADD COLUMN ac_id INT REFERENCES ac(ac_id);
ALTER TABLE ps_list_entry     ADD COLUMN ac_id INT REFERENCES ac(ac_id);
ALTER TABLE booth_crosswalk   ADD COLUMN ac_id INT REFERENCES ac(ac_id);
ALTER TABLE candidate         ADD COLUMN ac_id INT REFERENCES ac(ac_id);
ALTER TABLE result_booth      ADD COLUMN ac_id INT REFERENCES ac(ac_id);
ALTER TABLE result_booth_meta ADD COLUMN ac_id INT REFERENCES ac(ac_id);
ALTER TABLE result_ac_total   ADD COLUMN ac_id INT REFERENCES ac(ac_id);
ALTER TABLE roll_revision     ADD COLUMN ac_id INT REFERENCES ac(ac_id);
ALTER TABLE roll_snapshot     ADD COLUMN ac_id INT REFERENCES ac(ac_id);
ALTER TABLE roll_change       ADD COLUMN ac_id INT REFERENCES ac(ac_id);
ALTER TABLE caste_estimate    ADD COLUMN ac_id INT REFERENCES ac(ac_id);
ALTER TABLE caste_survey      ADD COLUMN ac_id INT REFERENCES ac(ac_id);
ALTER TABLE local_result      ADD COLUMN ac_id INT REFERENCES ac(ac_id);
ALTER TABLE ground_report     ADD COLUMN ac_id INT REFERENCES ac(ac_id);
ALTER TABLE knowledge_card    ADD COLUMN ac_id INT REFERENCES ac(ac_id);
ALTER TABLE source_doc        ADD COLUMN ac_id INT REFERENCES ac(ac_id);
ALTER TABLE review_queue      ADD COLUMN ac_id INT REFERENCES ac(ac_id);
ALTER TABLE demography        ADD COLUMN ac_id INT REFERENCES ac(ac_id);

-- Nullable on purpose: some jobs are global (news crawl, backup, prompt purge).
ALTER TABLE job_run           ADD COLUMN ac_id INT REFERENCES ac(ac_id);

-- An article can concern several constituencies, so this is an array rather
-- than a scalar, and is GIN-indexed for the per-AC news stream.
ALTER TABLE news_item         ADD COLUMN ac_ids INT[] NOT NULL DEFAULT '{}';
CREATE INDEX news_item_ac_idx ON news_item USING gin (ac_ids);

-- ---------------------------------------------------------------------------
-- 6. Backfill: everything that exists today is Giridih
-- ---------------------------------------------------------------------------

DO $$
DECLARE
    giridih_id INT;
    ev_id      INT;
BEGIN
    SELECT ac_id INTO STRICT giridih_id FROM ac WHERE ac_number = 32;

    UPDATE block             SET ac_id = giridih_id WHERE ac_id IS NULL;
    UPDATE area              SET ac_id = giridih_id WHERE ac_id IS NULL;
    UPDATE booth             SET ac_id = giridih_id WHERE ac_id IS NULL;
    UPDATE ps_list_entry     SET ac_id = giridih_id WHERE ac_id IS NULL;
    UPDATE booth_crosswalk   SET ac_id = giridih_id WHERE ac_id IS NULL;
    UPDATE candidate         SET ac_id = giridih_id WHERE ac_id IS NULL;
    UPDATE result_booth      SET ac_id = giridih_id WHERE ac_id IS NULL;
    UPDATE result_booth_meta SET ac_id = giridih_id WHERE ac_id IS NULL;
    UPDATE result_ac_total   SET ac_id = giridih_id WHERE ac_id IS NULL;
    UPDATE roll_revision     SET ac_id = giridih_id WHERE ac_id IS NULL;
    UPDATE roll_snapshot     SET ac_id = giridih_id WHERE ac_id IS NULL;
    UPDATE roll_change       SET ac_id = giridih_id WHERE ac_id IS NULL;
    UPDATE caste_estimate    SET ac_id = giridih_id WHERE ac_id IS NULL;
    UPDATE caste_survey      SET ac_id = giridih_id WHERE ac_id IS NULL;
    UPDATE local_result      SET ac_id = giridih_id WHERE ac_id IS NULL;
    UPDATE ground_report     SET ac_id = giridih_id WHERE ac_id IS NULL;
    UPDATE source_doc        SET ac_id = giridih_id WHERE ac_id IS NULL;
    UPDATE review_queue      SET ac_id = giridih_id WHERE ac_id IS NULL;
    UPDATE demography        SET ac_id = giridih_id WHERE ac_id IS NULL;
    UPDATE news_item         SET ac_ids = ARRAY[giridih_id]
        WHERE ac_ids = '{}' OR ac_ids IS NULL;

    -- knowledge_card stays AC-agnostic where it is general guidance; only the
    -- Giridih-specific cards take an ac_id. Leaving the rest NULL is deliberate:
    -- the caste-guardrails and provenance cards apply to every constituency.
    UPDATE knowledge_card SET ac_id = giridih_id
     WHERE slug IN ('baseline-2024', 'ls-vs-split-2024', 'bypoll-context', 'geography');

    -- Link each existing election to its event and to Giridih.
    FOR ev_id IN SELECT event_id FROM election_event LOOP
        UPDATE election e
           SET event_id = ev_id, ac_id = giridih_id
         WHERE e.event_id IS NULL
           AND (CASE WHEN e.type = 'WARD' THEN 'ULB' ELSE e.type END
                || '-' || e.year) = (SELECT label FROM election_event WHERE event_id = ev_id);
    END LOOP;
END $$;

-- ---------------------------------------------------------------------------
-- 7. booth_uid becomes '{ac_number}-B{nnnn}'
-- ---------------------------------------------------------------------------

-- Every FK to booth_uid is rewritten in this one transaction. Row counts are
-- captured before, compared after, and any mismatch aborts - which with
-- apply_migrations' per-file transaction rolls the whole migration back.
DO $$
DECLARE
    before_booth   BIGINT;
    before_cross   BIGINT;
    before_snap    BIGINT;
    before_change  BIGINT;
    before_caste   BIGINT;
    before_survey  BIGINT;
    before_ground  BIGINT;
    after_count    BIGINT;
    renamed        BIGINT;
BEGIN
    SELECT COUNT(*) INTO before_booth  FROM booth;
    SELECT COUNT(*) INTO before_cross  FROM booth_crosswalk;
    SELECT COUNT(*) INTO before_snap   FROM roll_snapshot;
    SELECT COUNT(*) INTO before_change FROM roll_change;
    SELECT COUNT(*) INTO before_caste  FROM caste_estimate;
    SELECT COUNT(*) INTO before_survey FROM caste_survey;
    SELECT COUNT(*) INTO before_ground FROM ground_report;

    -- Defer the FKs so children can be updated after the parent.
    SET CONSTRAINTS ALL DEFERRED;

    CREATE TEMP TABLE booth_uid_map ON COMMIT DROP AS
    SELECT b.booth_uid AS old_uid,
           a.ac_number || '-' || b.booth_uid AS new_uid
    FROM booth b JOIN ac a ON a.ac_id = b.ac_id
    -- Idempotent: skip anything already carrying its AC prefix.
    WHERE b.booth_uid NOT LIKE a.ac_number || '-%';

    SELECT COUNT(*) INTO renamed FROM booth_uid_map;

    IF renamed > 0 THEN
        UPDATE booth b SET booth_uid = m.new_uid
          FROM booth_uid_map m WHERE b.booth_uid = m.old_uid;
        UPDATE booth_crosswalk c SET booth_uid = m.new_uid
          FROM booth_uid_map m WHERE c.booth_uid = m.old_uid;
        UPDATE roll_snapshot r SET booth_uid = m.new_uid
          FROM booth_uid_map m WHERE r.booth_uid = m.old_uid;
        UPDATE roll_change r SET booth_uid = m.new_uid
          FROM booth_uid_map m WHERE r.booth_uid = m.old_uid;
        UPDATE caste_estimate ce SET booth_uid = m.new_uid
          FROM booth_uid_map m WHERE ce.booth_uid = m.old_uid;
        UPDATE caste_survey cs SET booth_uid = m.new_uid
          FROM booth_uid_map m WHERE cs.booth_uid = m.old_uid;
        UPDATE ground_report g SET booth_uid = m.new_uid
          FROM booth_uid_map m WHERE g.booth_uid = m.old_uid;
    END IF;

    -- Nothing may have been lost or duplicated.
    SELECT COUNT(*) INTO after_count FROM booth;
    IF after_count <> before_booth THEN
        RAISE EXCEPTION 'booth row count changed during uid migration: % -> %',
            before_booth, after_count;
    END IF;
    SELECT COUNT(*) INTO after_count FROM booth_crosswalk;
    IF after_count <> before_cross THEN
        RAISE EXCEPTION 'booth_crosswalk row count changed: % -> %', before_cross, after_count;
    END IF;
    SELECT COUNT(*) INTO after_count FROM roll_snapshot;
    IF after_count <> before_snap THEN
        RAISE EXCEPTION 'roll_snapshot row count changed: % -> %', before_snap, after_count;
    END IF;
    SELECT COUNT(*) INTO after_count FROM roll_change;
    IF after_count <> before_change THEN
        RAISE EXCEPTION 'roll_change row count changed: % -> %', before_change, after_count;
    END IF;
    SELECT COUNT(*) INTO after_count FROM caste_estimate;
    IF after_count <> before_caste THEN
        RAISE EXCEPTION 'caste_estimate row count changed: % -> %', before_caste, after_count;
    END IF;
    SELECT COUNT(*) INTO after_count FROM caste_survey;
    IF after_count <> before_survey THEN
        RAISE EXCEPTION 'caste_survey row count changed: % -> %', before_survey, after_count;
    END IF;
    SELECT COUNT(*) INTO after_count FROM ground_report;
    IF after_count <> before_ground THEN
        RAISE EXCEPTION 'ground_report row count changed: % -> %', before_ground, after_count;
    END IF;

    -- No orphans: every child booth_uid must still resolve.
    IF EXISTS (SELECT 1 FROM booth_crosswalk c
                LEFT JOIN booth b ON b.booth_uid = c.booth_uid WHERE b.booth_uid IS NULL) THEN
        RAISE EXCEPTION 'booth_crosswalk has orphaned booth_uid values after migration';
    END IF;
    IF EXISTS (SELECT 1 FROM roll_snapshot r
                LEFT JOIN booth b ON b.booth_uid = r.booth_uid WHERE b.booth_uid IS NULL) THEN
        RAISE EXCEPTION 'roll_snapshot has orphaned booth_uid values after migration';
    END IF;

    RAISE NOTICE 'booth_uid migration: % row(s) renamed, % booth(s) total', renamed, before_booth;
END $$;

-- Now that every booth has an AC, the scoping columns can be enforced.
ALTER TABLE block             ALTER COLUMN ac_id SET NOT NULL;
ALTER TABLE area              ALTER COLUMN ac_id SET NOT NULL;
ALTER TABLE booth             ALTER COLUMN ac_id SET NOT NULL;
ALTER TABLE ps_list_entry     ALTER COLUMN ac_id SET NOT NULL;
ALTER TABLE booth_crosswalk   ALTER COLUMN ac_id SET NOT NULL;
ALTER TABLE candidate         ALTER COLUMN ac_id SET NOT NULL;
ALTER TABLE result_booth      ALTER COLUMN ac_id SET NOT NULL;
ALTER TABLE result_booth_meta ALTER COLUMN ac_id SET NOT NULL;
ALTER TABLE roll_revision     ALTER COLUMN ac_id SET NOT NULL;
ALTER TABLE roll_snapshot     ALTER COLUMN ac_id SET NOT NULL;
ALTER TABLE roll_change       ALTER COLUMN ac_id SET NOT NULL;
ALTER TABLE caste_estimate    ALTER COLUMN ac_id SET NOT NULL;
ALTER TABLE election          ALTER COLUMN ac_id SET NOT NULL;
ALTER TABLE election          ALTER COLUMN event_id SET NOT NULL;

-- A polling-station number is only unique within its own AC, which is the whole
-- point of the change: the same PS 147 exists in six constituencies.
CREATE INDEX block_ac_idx           ON block (ac_id);
CREATE INDEX area_ac_idx            ON area (ac_id);
CREATE INDEX booth_ac_idx           ON booth (ac_id);
CREATE INDEX ps_list_entry_ac_idx   ON ps_list_entry (ac_id, ps_number);
CREATE INDEX booth_crosswalk_ac_idx ON booth_crosswalk (ac_id, ps_number);
CREATE INDEX result_booth_ac_idx    ON result_booth (ac_id, election_id);
CREATE INDEX caste_estimate_ac_idx  ON caste_estimate (ac_id);
CREATE INDEX roll_snapshot_ac_idx   ON roll_snapshot (ac_id);
CREATE INDEX roll_change_ac_idx     ON roll_change (ac_id);
CREATE INDEX source_doc_ac_idx      ON source_doc (ac_id);
CREATE INDEX review_queue_ac_idx    ON review_queue (ac_id) WHERE status = 'open';

-- `label` and (type, year) were globally unique, which six constituencies each
-- holding a VS-2024 contest cannot satisfy. Both become per-AC: the label stays
-- the human handle an operator types at `--election VS-2024`, but it identifies
-- a contest only together with the AC.
ALTER TABLE election DROP CONSTRAINT IF EXISTS election_label_key;
ALTER TABLE election DROP CONSTRAINT IF EXISTS election_type_year_key;
CREATE UNIQUE INDEX election_ac_label ON election (ac_id, label);

-- One contest per event per AC, and one baseline per AC. The old schema had a
-- plain boolean with no constraint; flag a second baseline and scenario's
-- load_baseline joins every booth twice, doubling all projected totals (B9).
CREATE UNIQUE INDEX election_event_ac_key ON election (event_id, ac_id);
CREATE UNIQUE INDEX one_baseline_per_ac   ON election (ac_id) WHERE is_baseline;

-- B6: these carried an election_id with no foreign key, so a typo created
-- orphan rows no join would find and no constraint would reject.
ALTER TABLE booth_crosswalk
    ADD CONSTRAINT booth_crosswalk_election_fk
        FOREIGN KEY (election_id) REFERENCES election(election_id) ON DELETE CASCADE;
ALTER TABLE ps_list_entry
    ADD CONSTRAINT ps_list_entry_election_fk
        FOREIGN KEY (election_id) REFERENCES election(election_id) ON DELETE CASCADE;

-- B11: roll_revision has UNIQUE (revision_date, is_mother) but parse_roll's
-- upsert targeted (label) only, so two supplements dated the same day under
-- different labels raised an unhandled UniqueViolation mid-load. Revisions are
-- also per AC now, so the uniqueness has to be per AC.
ALTER TABLE roll_revision DROP CONSTRAINT IF EXISTS roll_revision_label_key;
ALTER TABLE roll_revision DROP CONSTRAINT IF EXISTS roll_revision_revision_date_is_mother_key;
CREATE UNIQUE INDEX roll_revision_ac_label ON roll_revision (ac_id, label);
CREATE UNIQUE INDEX roll_revision_ac_date_mother
    ON roll_revision (ac_id, revision_date, is_mother);

-- ---------------------------------------------------------------------------
-- 8. Per-AC booth sequences
-- ---------------------------------------------------------------------------

-- booth_uid is minted from a per-AC sequence and never derived from a PS
-- number. Deriving it from PS numbering is what made re-anchoring dangerous
-- (B5): running parse_pslist --anchor against a newer list silently rebound
-- 'B0147' to whatever station was number 147 in the new list, and every
-- crosswalk, roll snapshot and caste estimate keyed on it then referred to a
-- different physical booth.
CREATE OR REPLACE FUNCTION next_booth_uid(p_ac_number INT) RETURNS TEXT AS $$
DECLARE
    seq_name TEXT := format('booth_seq_%s', p_ac_number);
    next_val BIGINT;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_class WHERE relkind = 'S' AND relname = seq_name) THEN
        EXECUTE format('CREATE SEQUENCE %I START WITH 1', seq_name);
    END IF;
    EXECUTE format('SELECT nextval(%L)', seq_name) INTO next_val;
    RETURN format('%s-B%s', p_ac_number, lpad(next_val::TEXT, 4, '0'));
END $$ LANGUAGE plpgsql;

COMMENT ON FUNCTION next_booth_uid(INT) IS
    'Mint the next booth_uid for an AC. Never derive a booth_uid from a PS '
    'number: PS numbers are reassigned at every revision (audit B3, B5).';

-- Advance each existing AC's sequence past the UIDs already in use, so the next
-- minted UID cannot collide with a migrated one.
DO $$
DECLARE
    rec      RECORD;
    seq_name TEXT;
    highest  BIGINT;
BEGIN
    FOR rec IN SELECT a.ac_id, a.ac_number FROM ac a LOOP
        seq_name := format('booth_seq_%s', rec.ac_number);
        IF NOT EXISTS (SELECT 1 FROM pg_class WHERE relkind = 'S' AND relname = seq_name) THEN
            EXECUTE format('CREATE SEQUENCE %I START WITH 1', seq_name);
        END IF;
        SELECT COALESCE(MAX(NULLIF(regexp_replace(booth_uid, '^\d+-B', ''), '')::BIGINT), 0)
          INTO highest
          FROM booth
         WHERE ac_id = rec.ac_id AND booth_uid ~ ('^' || rec.ac_number || '-B\d+$');
        IF highest > 0 THEN
            EXECUTE format('SELECT setval(%L, %s)', seq_name, highest);
        END IF;
    END LOOP;
END $$;

-- ---------------------------------------------------------------------------
-- 9. Per-AC contest pairs (master prompt 3.2)
-- ---------------------------------------------------------------------------

-- The pair a signed margin and a scenario are measured between, per AC per
-- event: Giridih JMM/BJP, Silli JMM/AJSU, Dumri JLKM/JMM, Kanke INC/BJP. The
-- old code hardcoded ("JMM", "BJP") in analytics/scenario.py, so a scenario
-- that moved most of the vote to JLKM still reported JMM or BJP as the winner
-- while the votes dict beside it showed the real leader (D5).
CREATE TABLE ac_contest (
    ac_id    INT NOT NULL REFERENCES ac(ac_id) ON DELETE CASCADE,
    event_id INT NOT NULL REFERENCES election_event(event_id) ON DELETE CASCADE,
    party_a  INT NOT NULL REFERENCES party(party_id),
    party_b  INT NOT NULL REFERENCES party(party_id),
    source   TEXT,
    PRIMARY KEY (ac_id, event_id),
    CONSTRAINT contest_parties_differ CHECK (party_a <> party_b)
);

COMMENT ON TABLE ac_contest IS
    'The two parties a signed margin is measured between for this AC at this '
    'event. party_a positive, party_b negative, NULL if neither won.';

-- ---------------------------------------------------------------------------
-- 10. Booth lineage: splits and merges (master prompt 3.3)
-- ---------------------------------------------------------------------------

-- A booth that splits in two, or two that merge into one, cannot be compared
-- year on year as a single unit. Swing for such a booth is computed on the
-- aggregated lineage group and flagged in the UI, rather than silently reported
-- against half a booth's electorate.
CREATE TABLE booth_lineage (
    old_election_id INT NOT NULL REFERENCES election(election_id) ON DELETE CASCADE,
    old_ps          INT NOT NULL,
    new_booth_uid   TEXT NOT NULL REFERENCES booth(booth_uid) ON DELETE CASCADE,
    kind            TEXT NOT NULL CHECK (kind IN ('split', 'merge')),
    -- Share of the old station's electorate attributed to this new booth. For a
    -- merge every row is 1.0; for a split the rows sum to 1.0.
    weight          REAL NOT NULL DEFAULT 1.0 CHECK (weight > 0 AND weight <= 1),
    ac_id           INT NOT NULL REFERENCES ac(ac_id) ON DELETE CASCADE,
    note            TEXT,
    PRIMARY KEY (old_election_id, old_ps, new_booth_uid)
);
CREATE INDEX booth_lineage_new_idx ON booth_lineage (new_booth_uid);
CREATE INDEX booth_lineage_ac_idx  ON booth_lineage (ac_id);

-- Every crosswalk decision an operator makes, kept for audit. The crosswalk
-- editor writes one row per accept/reject/reassign/split/merge.
CREATE TABLE crosswalk_audit (
    audit_id     BIGSERIAL PRIMARY KEY,
    ac_id        INT NOT NULL REFERENCES ac(ac_id) ON DELETE CASCADE,
    election_id  INT NOT NULL REFERENCES election(election_id) ON DELETE CASCADE,
    ps_number    INT NOT NULL,
    action       TEXT NOT NULL CHECK (action IN ('accept', 'reject', 'reassign',
                                                 'mark_split', 'mark_merge')),
    old_booth_uid TEXT,
    new_booth_uid TEXT,
    confidence   REAL,
    actor_id     INT REFERENCES app_user(user_id),
    note         TEXT,
    at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX crosswalk_audit_key ON crosswalk_audit (ac_id, election_id, ps_number);

-- ---------------------------------------------------------------------------
-- 11. roll_part on booth and ps_list_entry (audit C10)
-- ---------------------------------------------------------------------------

-- The documented crosswalk formula is 0.5*building + 0.3*place + 0.2*roll_part,
-- but `booth` did not carry roll_part, so the 0.20 term never contributed and
-- every score was really 62.5% building / 37.5% place. ps_list_entry already has
-- it; booth needs it so anchor_stations() can supply both sides.
ALTER TABLE booth ADD COLUMN roll_part INT;

-- ---------------------------------------------------------------------------
-- 12. source_doc / roll provenance (audit C15)
-- ---------------------------------------------------------------------------

-- roll_snapshot and roll_change recorded neither, so a suspicious additions
-- figure could not be traced to a document and page.
ALTER TABLE roll_snapshot ADD COLUMN source_doc TEXT, ADD COLUMN source_page INT;
ALTER TABLE roll_change   ADD COLUMN source_doc TEXT, ADD COLUMN source_page INT;

-- ---------------------------------------------------------------------------
-- 13. review_queue dedupe (audit C16)
-- ---------------------------------------------------------------------------

-- Both crosswalk branches inserted with no dedupe, and the runbook explicitly
-- tells operators to re-run after fixes, so the queue doubled every time.
DELETE FROM review_queue rq
 WHERE EXISTS (SELECT 1 FROM review_queue keep
                WHERE keep.kind = rq.kind AND keep.ref = rq.ref
                  AND keep.id < rq.id);
CREATE UNIQUE INDEX review_queue_kind_ref ON review_queue (kind, ref);
