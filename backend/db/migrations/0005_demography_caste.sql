-- 0005: demography and caste estimates
-- COMPLIANCE (HLD 5): caste is AGGREGATE-ONLY, estimated at booth level, with a
-- confidence score and a named source. No individual voter is ever tagged.

CREATE TABLE demography (
    area_id      INT NOT NULL REFERENCES area(area_id) ON DELETE CASCADE,
    census_year  SMALLINT NOT NULL,
    population   INT,
    sc           INT,
    st           INT,
    literate     INT,
    main_workers INT,
    households   INT,
    PRIMARY KEY (area_id, census_year)
);

CREATE TABLE community (
    community_id SERIAL PRIMARY KEY,
    name_en      TEXT NOT NULL UNIQUE,
    name_hi      TEXT NOT NULL,
    category     TEXT NOT NULL CHECK (category IN ('GEN', 'OBC', 'SC', 'ST', 'MUSLIM', 'OTHER')),
    sort_order   SMALLINT NOT NULL DEFAULT 100
);

CREATE TABLE caste_estimate (
    booth_uid    TEXT NOT NULL REFERENCES booth(booth_uid) ON DELETE CASCADE,
    community_id INT  NOT NULL REFERENCES community(community_id) ON DELETE CASCADE,
    est_count    INT,
    est_pct      REAL CHECK (est_pct BETWEEN 0 AND 100),
    confidence   REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    source       TEXT NOT NULL CHECK (source IN ('surname', 'census', 'survey', 'blend')),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (booth_uid, community_id, source)
);
CREATE INDEX caste_estimate_blend_idx ON caste_estimate (booth_uid) WHERE source = 'blend';

-- Surname -> community lookup, Giridih-specific. Ambiguous surnames carry
-- weight < 1 and are split across communities (LLD 5).
CREATE TABLE surname_dict (
    surname_hi   TEXT NOT NULL,
    surname_en   TEXT,
    community_id INT NOT NULL REFERENCES community(community_id) ON DELETE CASCADE,
    weight       REAL NOT NULL DEFAULT 1.0 CHECK (weight > 0 AND weight <= 1),
    notes        TEXT,
    PRIMARY KEY (surname_hi, community_id)
);
CREATE INDEX surname_dict_en_idx ON surname_dict (lower(surname_en));

-- Booth in-charge samikaran sheet. Overrides inference when present.
CREATE TABLE caste_survey (
    survey_id    SERIAL PRIMARY KEY,
    booth_uid    TEXT NOT NULL REFERENCES booth(booth_uid) ON DELETE CASCADE,
    community_id INT  NOT NULL REFERENCES community(community_id) ON DELETE CASCADE,
    households   INT,
    voters       INT,
    reported_by  INT,
    reported_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (booth_uid, community_id)
);
