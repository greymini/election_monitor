-- 0003: elections, candidates and booth-wise results (Form 20)

CREATE TABLE election (
    election_id SERIAL PRIMARY KEY,
    type        TEXT NOT NULL CHECK (type IN ('VS', 'LS', 'PANCHAYAT', 'WARD')),
    year        SMALLINT NOT NULL,
    poll_date   DATE,
    label       TEXT NOT NULL UNIQUE,
    is_baseline BOOLEAN NOT NULL DEFAULT false,
    notes       TEXT,
    UNIQUE (type, year)
);

CREATE TABLE party (
    party_id      SERIAL PRIMARY KEY,
    abbr          TEXT NOT NULL UNIQUE,
    name_en       TEXT NOT NULL,
    name_hi       TEXT,
    alliance_2024 TEXT,
    colour        TEXT
);

CREATE TABLE candidate (
    candidate_id SERIAL PRIMARY KEY,
    election_id  INT NOT NULL REFERENCES election(election_id) ON DELETE CASCADE,
    name_en      TEXT NOT NULL,
    name_hi      TEXT,
    party_id     INT REFERENCES party(party_id),
    is_winner    BOOLEAN NOT NULL DEFAULT false,
    column_index SMALLINT,
    UNIQUE (election_id, name_en, party_id)
);
CREATE INDEX candidate_election_idx ON candidate (election_id);

-- One row per candidate per polling station, exactly as printed in Form 20.
CREATE TABLE result_booth (
    election_id  INT NOT NULL REFERENCES election(election_id) ON DELETE CASCADE,
    ps_number    INT NOT NULL,
    candidate_id INT NOT NULL REFERENCES candidate(candidate_id) ON DELETE CASCADE,
    votes        INT NOT NULL CHECK (votes >= 0),
    PRIMARY KEY (election_id, ps_number, candidate_id)
);
CREATE INDEX result_booth_cand_idx ON result_booth (candidate_id);

CREATE TABLE result_booth_meta (
    election_id INT NOT NULL REFERENCES election(election_id) ON DELETE CASCADE,
    ps_number   INT NOT NULL,
    electors    INT,
    total_valid INT,
    nota        INT,
    rejected    INT,
    tendered    INT,
    postal      INT,
    source_doc  TEXT,
    source_page INT,
    loaded_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (election_id, ps_number)
);

-- AC-level published totals: the hard validation target for a Form 20 load.
CREATE TABLE result_ac_total (
    election_id  INT NOT NULL REFERENCES election(election_id) ON DELETE CASCADE,
    candidate_id INT REFERENCES candidate(candidate_id) ON DELETE CASCADE,
    metric       TEXT NOT NULL CHECK (metric IN ('votes', 'nota', 'total_valid', 'electors', 'rejected', 'postal')),
    value        INT NOT NULL,
    source       TEXT
);
CREATE UNIQUE INDEX result_ac_total_key
    ON result_ac_total (election_id, COALESCE(candidate_id, -1), metric);
