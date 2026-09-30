-- 0006: panchayat and municipal results (HLD 7, module 7)
-- Panchayat polls are party-less: affiliation is a manual tag and the source of
-- the tag is always recorded so the subjectivity stays visible.

CREATE TABLE local_result (
    local_result_id SERIAL PRIMARY KEY,
    election_id     INT NOT NULL REFERENCES election(election_id) ON DELETE CASCADE,
    seat_type       TEXT NOT NULL CHECK (seat_type IN ('mukhiya', 'ZP', 'panchayat_samiti', 'ward')),
    area_id         INT REFERENCES area(area_id),
    seat_name       TEXT NOT NULL,
    winner          TEXT NOT NULL,
    runner_up       TEXT,
    tagged_party_id INT REFERENCES party(party_id),
    tag_source      TEXT,
    tag_confidence  REAL CHECK (tag_confidence BETWEEN 0 AND 1),
    votes           INT,
    runner_up_votes INT,
    margin          INT,
    source_doc      TEXT,
    UNIQUE (election_id, seat_type, seat_name)
);
CREATE INDEX local_result_area_idx ON local_result (area_id);
