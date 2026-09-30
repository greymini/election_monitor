-- 0004: electoral roll snapshots and changes
-- COMPLIANCE (HLD 5, LLD 12): no table here stores an individual voter.
-- Parsers count and discard names, EPIC numbers and addresses.

CREATE TABLE roll_revision (
    revision_id   SERIAL PRIMARY KEY,
    revision_date DATE NOT NULL,
    label         TEXT NOT NULL UNIQUE,
    is_post_sir   BOOLEAN NOT NULL DEFAULT false,
    is_mother     BOOLEAN NOT NULL DEFAULT false,
    source_doc    TEXT,
    UNIQUE (revision_date, is_mother)
);

CREATE TABLE roll_snapshot (
    revision_id INT NOT NULL REFERENCES roll_revision(revision_id) ON DELETE CASCADE,
    booth_uid   TEXT NOT NULL REFERENCES booth(booth_uid),
    electors    INT NOT NULL,
    male        INT,
    female      INT,
    other       INT,
    age_18_19   INT,
    age_20_29   INT,
    age_30_39   INT,
    age_40_49   INT,
    age_50_59   INT,
    age_60p     INT,
    PRIMARY KEY (revision_id, booth_uid)
);

CREATE TABLE roll_change (
    revision_id   INT NOT NULL REFERENCES roll_revision(revision_id) ON DELETE CASCADE,
    booth_uid     TEXT NOT NULL REFERENCES booth(booth_uid),
    additions     INT NOT NULL DEFAULT 0,
    deletions     INT NOT NULL DEFAULT 0,
    modifications INT NOT NULL DEFAULT 0,
    add_18_19     INT,
    add_female    INT,
    add_male      INT,
    del_death     INT,
    del_shifted   INT,
    del_other     INT,
    PRIMARY KEY (revision_id, booth_uid)
);

-- Which roll revision was in force for each election, so new-voter share can be
-- measured against the right baseline.
CREATE TABLE election_roll_link (
    election_id INT NOT NULL REFERENCES election(election_id) ON DELETE CASCADE,
    revision_id INT NOT NULL REFERENCES roll_revision(revision_id) ON DELETE CASCADE,
    PRIMARY KEY (election_id)
);
