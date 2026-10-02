-- 0016: Lok Sabha candidates shared across the assembly segments of a PC
-- (master prompt 3.6, spec 2.3).
--
-- A Lok Sabha Form 20 is published per parliamentary constituency, and each
-- assembly segment's polling stations appear under their own AC header inside
-- it. The `election` row for an LS event is therefore the *segment*: the part of
-- the PC contest that fell inside one assembly seat.
--
-- The candidates, though, are the PC's. C.P. Choudhary stood once in Giridih
-- PC, not six times across its six segments, and his votes are the same votes
-- whichever segment you read them from. Creating a separate `candidate` row per
-- segment would mean:
--
--   * six rows for one person, so "how did this candidate do across the PC" is
--     a fuzzy name match rather than a join;
--   * six different candidate_ids in result_booth for the same human, so an
--     AC-total reconciliation against the published PC result cannot be done at
--     all; and
--   * no way to state that the AJSU vote in the Giridih segment and the AJSU
--     vote in the Dumri segment belong to the same campaign, which is exactly
--     the LS-versus-VS question the HLD calls the single most important dynamic
--     to model.
--
-- So `pc_candidate` holds the person once per PC contest, and `candidate` rows
-- for LS segments point at it. `candidate` still exists per segment because
-- result_booth references it and because a segment is where the votes are
-- counted.
--
-- No transaction control: apply_migrations wraps each file in one transaction.

CREATE TABLE pc_candidate (
    pc_candidate_id SERIAL PRIMARY KEY,
    pc_id     INT NOT NULL REFERENCES pc(pc_id) ON DELETE CASCADE,
    event_id  INT NOT NULL REFERENCES election_event(event_id) ON DELETE CASCADE,
    name_en   TEXT NOT NULL,
    name_hi   TEXT,
    party_id  INT REFERENCES party(party_id),
    is_winner BOOLEAN NOT NULL DEFAULT false,
    -- The PC-wide published total, which is the reconciliation target for the
    -- sum of this candidate's votes across every segment.
    votes_published INT,
    source    TEXT,
    UNIQUE (pc_id, event_id, name_en, party_id)
);
CREATE INDEX pc_candidate_event_idx ON pc_candidate (event_id);

COMMENT ON TABLE pc_candidate IS
    'One candidate in one parliamentary contest. candidate rows for the LS '
    'segments of that contest point here, so the same person is one row and '
    'their PC-wide total is reconcilable against the sum of their segments.';

ALTER TABLE candidate
    ADD COLUMN pc_candidate_id INT REFERENCES pc_candidate(pc_candidate_id);

CREATE INDEX candidate_pc_candidate_idx ON candidate (pc_candidate_id);

COMMENT ON COLUMN candidate.pc_candidate_id IS
    'Set for LS segment contests only. NULL for an assembly election, where the '
    'candidate stands in the AC itself and there is nothing above it to share.';

-- The AC header section each segment was read from, so a segment load can be
-- traced back to the page it came from in a PC-wide document. Without this,
-- "which part of this 400-page PDF produced these rows" has no answer.
ALTER TABLE election
    ADD COLUMN segment_of_pc_id INT REFERENCES pc(pc_id),
    ADD COLUMN segment_header   TEXT,
    ADD COLUMN segment_pages    TEXT;

COMMENT ON COLUMN election.segment_header IS
    'The AC header line in the PC-level Form 20 that this segment was extracted '
    'under, verbatim, so the extraction can be checked against the document.';

-- A view for the question the HLD calls the most important one: how a
-- candidate's PC-wide vote decomposes across the assembly segments, and whether
-- the segments sum to the published total.
CREATE VIEW v_pc_candidate_segments AS
SELECT pcc.pc_candidate_id,
       pcc.pc_id,
       p.name_en          AS pc_name,
       ev.label           AS event_label,
       pcc.name_en        AS candidate_name,
       pty.abbr           AS party,
       pcc.votes_published,
       a.ac_number,
       a.name_en          AS ac_name,
       e.election_id,
       SUM(rb.votes)::INT AS segment_votes
FROM pc_candidate pcc
JOIN pc p              ON p.pc_id = pcc.pc_id
JOIN election_event ev ON ev.event_id = pcc.event_id
LEFT JOIN party pty    ON pty.party_id = pcc.party_id
LEFT JOIN candidate c  ON c.pc_candidate_id = pcc.pc_candidate_id
LEFT JOIN election e   ON e.election_id = c.election_id
LEFT JOIN ac a         ON a.ac_id = e.ac_id
LEFT JOIN result_booth rb ON rb.candidate_id = c.candidate_id
GROUP BY pcc.pc_candidate_id, pcc.pc_id, p.name_en, ev.label, pcc.name_en,
         pty.abbr, pcc.votes_published, a.ac_number, a.name_en, e.election_id;

COMMENT ON VIEW v_pc_candidate_segments IS
    'A candidate PC-wide, decomposed by assembly segment. Comparing SUM '
    '(segment_votes) against votes_published is how a segment extraction is '
    'checked: if they disagree, a segment is missing or was read twice.';
