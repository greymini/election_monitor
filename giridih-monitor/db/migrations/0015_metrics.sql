-- 0015: the analytics views, rebuilt with ac_id leading and the canonical
-- metric definitions of master prompt 3.2.
--
-- 0014 dropped the previous ten views because they select from `election`,
-- whose shape changed. This rebuilds them once, correctly, rather than having
-- 0014 write view SQL that this migration would immediately replace.
--
-- The definitions here are the same ones `analytics/metrics.py` implements in
-- Python, and `tests/e2e/test_metrics_sql.py` runs one fixture through both to
-- keep them from drifting. Where this file and that module disagree, the
-- module's docstring is the specification and this is the bug.
--
-- What changed, and why, in one place:
--
--   * **One denominator: valid votes including NOTA.** `valid_votes` is the sum
--     of candidate votes with NOTA among them, which is what Form 20 prints as
--     "total valid votes" and what the ECI's published margin percentage
--     divides by. The old views divided by a NOTA-excluding total while showing
--     a NOTA-including one in the same row, so Giridih's headline margin came
--     out at 1.87% against the published 1.85% and no row reconciled against
--     itself (D1). NOTA now loads as a real candidate row, which also revives
--     the nota pivot that had been dead code.
--
--   * **Ranking is per candidate and excludes NOTA.** The old `ranked` CTE
--     grouped on COALESCE(party_id, -1), so every independent and unresolved
--     candidate was summed into one pseudo-party that competed for winner (D3).
--
--   * **Absent is NULL.** swing where there is no prior election (D2), floating
--     vote where only one poll type exists that year (D4), turnout where
--     electors are unknown (B4), new-voter share where a roll link is missing
--     (B1). Every one of those previously produced a number that looked like
--     data: a +38.3 point fabricated swing, a uniform 50.00% floating vote, 0
--     additions everywhere.
--
--   * **Weak crosswalks do not carry a comparison.** A station matched in the
--     0.65-0.85 band now has a `booth_crosswalk` row (B2 - it used to have
--     none, and every view inner-joined that table, so its votes vanished from
--     every rollup silently). Those rows are joined, so the booth appears, but
--     swing is NULL until a human reviews the match.
--
--   * **Splits and merges compare on the lineage group.** Half a booth's
--     electorate against the whole of last time's is not a swing.
--
--   * **The signed margin comes from ac_contest**, per AC per event, so no view
--     hardcodes JMM/BJP.
--
-- No transaction control: apply_migrations wraps each file in one transaction.

-- ---------------------------------------------------------------------------
-- 1. One row per booth per candidate. The grain everything else derives from.
-- ---------------------------------------------------------------------------

CREATE MATERIALIZED VIEW mv_result_booth_candidate AS
SELECT r.ac_id,
       r.election_id,
       e.label            AS election_label,
       e.type             AS election_type,
       e.year             AS election_year,
       x.booth_uid,
       r.candidate_id,
       c.name_en          AS candidate_name,
       c.party_id,
       p.abbr             AS party,
       -- A candidate key that stays distinct for independents, who must compete
       -- individually rather than as a bucket (D3).
       CASE WHEN p.abbr = 'IND' THEN 'IND:' || c.name_en ELSE p.abbr END AS contestant,
       SUM(r.votes)::INT  AS votes,
       -- Carried so downstream views can decide whether this booth's numbers
       -- may be compared across years.
       MIN(x.confidence)  AS crosswalk_confidence,
       BOOL_AND(x.reviewed) AS crosswalk_reviewed,
       MIN(x.match_method) AS crosswalk_method
FROM result_booth r
JOIN election e         ON e.election_id = r.election_id
JOIN booth_crosswalk x  ON x.election_id = r.election_id AND x.ps_number = r.ps_number
JOIN candidate c        ON c.candidate_id = r.candidate_id
LEFT JOIN party p       ON p.party_id = c.party_id
GROUP BY r.ac_id, r.election_id, e.label, e.type, e.year, x.booth_uid,
         r.candidate_id, c.name_en, c.party_id, p.abbr;

CREATE UNIQUE INDEX mv_rbc_key
    ON mv_result_booth_candidate (ac_id, election_id, booth_uid, candidate_id);
CREATE INDEX mv_rbc_booth ON mv_result_booth_candidate (ac_id, booth_uid);

-- ---------------------------------------------------------------------------
-- 2. Per booth per party, and the totals every percentage divides by.
-- ---------------------------------------------------------------------------

CREATE MATERIALIZED VIEW mv_result_booth_party AS
SELECT ac_id, election_id, election_label, election_type, election_year,
       booth_uid, party_id, party,
       SUM(votes)::INT AS votes
FROM mv_result_booth_candidate
GROUP BY ac_id, election_id, election_label, election_type, election_year,
         booth_uid, party_id, party;

CREATE UNIQUE INDEX mv_rbp_key
    ON mv_result_booth_party (ac_id, election_id, booth_uid, COALESCE(party_id, -1));

-- valid_votes: candidate votes INCLUDING NOTA (metrics.valid_votes).
CREATE MATERIALIZED VIEW mv_booth_totals AS
SELECT ac_id, election_id, election_label, election_type, election_year, booth_uid,
       SUM(votes)::INT                                        AS valid_votes,
       SUM(votes) FILTER (WHERE party = 'NOTA')::INT           AS nota,
       SUM(votes) FILTER (WHERE party <> 'NOTA' OR party IS NULL)::INT AS candidate_votes,
       COUNT(*) FILTER (WHERE party <> 'NOTA' OR party IS NULL) AS contestants
FROM mv_result_booth_party
GROUP BY ac_id, election_id, election_label, election_type, election_year, booth_uid;

CREATE UNIQUE INDEX mv_bt_key ON mv_booth_totals (ac_id, election_id, booth_uid);

-- share_pct, with NOTA inside the denominator.
CREATE MATERIALIZED VIEW mv_booth_party_share AS
SELECT p.ac_id, p.election_id, p.election_label, p.election_type, p.election_year,
       p.booth_uid, p.party_id, p.party, p.votes,
       t.valid_votes,
       ROUND((100.0 * p.votes / NULLIF(t.valid_votes, 0))::NUMERIC, 2) AS share_pct
FROM mv_result_booth_party p
JOIN mv_booth_totals t
  ON t.ac_id = p.ac_id AND t.election_id = p.election_id AND t.booth_uid = p.booth_uid;

CREATE UNIQUE INDEX mv_bps_key
    ON mv_booth_party_share (ac_id, election_id, booth_uid, COALESCE(party_id, -1));
CREATE INDEX mv_bps_party ON mv_booth_party_share (ac_id, party, election_year);

-- ---------------------------------------------------------------------------
-- 3. The wide row: one per election per booth, with the headline metrics.
-- ---------------------------------------------------------------------------

CREATE MATERIALIZED VIEW mv_result_booth_wide AS
WITH ranked AS (
    -- Rank contestants, NOTA excluded. Ordered by name after votes so a tie is
    -- deterministic between refreshes rather than dependent on scan order.
    SELECT ac_id, election_id, booth_uid, contestant, party, candidate_name, votes,
           ROW_NUMBER() OVER (PARTITION BY ac_id, election_id, booth_uid
                              ORDER BY votes DESC, contestant) AS rn
    FROM mv_result_booth_candidate
    WHERE party IS DISTINCT FROM 'NOTA'
), pivot AS (
    SELECT ac_id, election_id, booth_uid,
           SUM(votes) FILTER (WHERE party = 'JMM')::INT  AS jmm,
           SUM(votes) FILTER (WHERE party = 'BJP')::INT  AS bjp,
           SUM(votes) FILTER (WHERE party = 'AJSU')::INT AS ajsu,
           SUM(votes) FILTER (WHERE party = 'JLKM')::INT AS jlkm,
           SUM(votes) FILTER (WHERE party = 'INC')::INT  AS inc,
           SUM(votes) FILTER (WHERE party = 'RJD')::INT  AS rjd,
           SUM(votes) FILTER (WHERE party = 'JVM')::INT  AS jvm,
           SUM(votes) FILTER (WHERE party NOT IN
               ('JMM','BJP','AJSU','JLKM','INC','RJD','JVM','NOTA')
               OR party IS NULL)::INT                    AS others
    FROM mv_result_booth_party
    GROUP BY ac_id, election_id, booth_uid
), meta AS (
    SELECT m.ac_id, m.election_id, x.booth_uid,
           SUM(m.electors)::INT    AS electors,
           SUM(m.rejected)::INT    AS rejected,
           SUM(m.total_valid)::INT AS printed_valid,
           string_agg(m.ps_number::TEXT, ',' ORDER BY m.ps_number) AS ps_numbers,
           MIN(m.source_doc)       AS source_doc,
           MIN(m.source_page)      AS source_page
    FROM result_booth_meta m
    JOIN booth_crosswalk x
      ON x.election_id = m.election_id AND x.ps_number = m.ps_number
    GROUP BY m.ac_id, m.election_id, x.booth_uid
), lineage AS (
    -- One row per new booth per old election, so the wide row can say that a
    -- comparison is on an aggregated group rather than a like-for-like booth.
    SELECT new_booth_uid, MIN(kind) AS kind, SUM(weight) AS weight
    FROM booth_lineage
    GROUP BY new_booth_uid
)
SELECT t.ac_id,
       t.election_id,
       t.election_label,
       t.election_type,
       t.election_year,
       t.booth_uid,
       b.area_id,
       a.block_id,
       m.ps_numbers,
       m.electors,
       t.valid_votes,
       -- votes_polled = valid + rejected; tendered excluded by construction.
       (t.valid_votes + COALESCE(m.rejected, 0))::INT AS votes_polled,
       m.rejected,
       t.nota,
       -- Kept so an operator can see the printed total next to the computed one.
       -- validate.py asserts they agree; a divergence means a parse problem, and
       -- silently preferring one was how D1 hid.
       m.printed_valid,
       pv.jmm, pv.bjp, pv.ajsu, pv.jlkm, pv.inc, pv.rjd, pv.jvm, pv.others,
       w.contestant  AS winner_party,
       w.candidate_name AS winner_candidate,
       w.votes       AS winner_votes,
       ru.contestant AS runner_party,
       ru.votes      AS runner_votes,
       t.contestants,
       -- margin: NULL with fewer than two contestants, never 0 (metrics.margin_votes)
       CASE WHEN t.contestants >= 2 THEN (w.votes - ru.votes)::INT END AS margin_votes,
       CASE WHEN t.contestants >= 2
            THEN ROUND((100.0 * (w.votes - ru.votes) / NULLIF(t.valid_votes, 0))::NUMERIC, 2)
       END AS margin_pct,
       -- signed_margin_pct: + if the contest pair's first party won, - if the
       -- second, NULL if neither. Drives the map's diverging ramp (F1).
       CASE
           WHEN t.contestants < 2 THEN NULL
           WHEN w.contestant = pa.abbr
               THEN ROUND((100.0 * (w.votes - ru.votes) / NULLIF(t.valid_votes, 0))::NUMERIC, 2)
           WHEN w.contestant = pb.abbr
               THEN -ROUND((100.0 * (w.votes - ru.votes) / NULLIF(t.valid_votes, 0))::NUMERIC, 2)
       END AS signed_margin_pct,
       pa.abbr AS contest_party_a,
       pb.abbr AS contest_party_b,
       -- turnout: electors from the linked roll snapshot, never from Form 20.
       ROUND((100.0 * (t.valid_votes + COALESCE(m.rejected, 0))
              / NULLIF(m.electors, 0))::NUMERIC, 2) AS turnout_pct,
       lin.kind   AS lineage_kind,
       lin.weight AS lineage_weight,
       m.source_doc,
       m.source_page
FROM mv_booth_totals t
JOIN booth b        ON b.booth_uid = t.booth_uid
JOIN area a         ON a.area_id = b.area_id
JOIN election e     ON e.election_id = t.election_id
LEFT JOIN pivot pv  ON pv.ac_id = t.ac_id AND pv.election_id = t.election_id
                   AND pv.booth_uid = t.booth_uid
LEFT JOIN meta m    ON m.ac_id = t.ac_id AND m.election_id = t.election_id
                   AND m.booth_uid = t.booth_uid
LEFT JOIN ranked w  ON w.ac_id = t.ac_id AND w.election_id = t.election_id
                   AND w.booth_uid = t.booth_uid AND w.rn = 1
LEFT JOIN ranked ru ON ru.ac_id = t.ac_id AND ru.election_id = t.election_id
                   AND ru.booth_uid = t.booth_uid AND ru.rn = 2
LEFT JOIN ac_contest ct ON ct.ac_id = t.ac_id AND ct.event_id = e.event_id
LEFT JOIN party pa  ON pa.party_id = ct.party_a
LEFT JOIN party pb  ON pb.party_id = ct.party_b
LEFT JOIN lineage lin ON lin.new_booth_uid = t.booth_uid;

CREATE UNIQUE INDEX mv_rbw_key ON mv_result_booth_wide (ac_id, election_id, booth_uid);
CREATE INDEX mv_rbw_label ON mv_result_booth_wide (ac_id, election_label);
CREATE INDEX mv_rbw_area  ON mv_result_booth_wide (ac_id, area_id);

-- ---------------------------------------------------------------------------
-- 4. Swing, per party per booth, same election type.
-- ---------------------------------------------------------------------------

CREATE MATERIALIZED VIEW mv_swing AS
WITH shares AS (
    SELECT s.ac_id, s.booth_uid, s.party_id, s.party, s.election_type,
           s.election_year, s.election_id, s.election_label, s.share_pct, s.votes,
           LAG(s.share_pct) OVER w  AS prev_share_pct,
           LAG(s.votes)     OVER w  AS prev_votes,
           LAG(s.election_id) OVER w AS prev_election_id,
           LAG(s.election_label) OVER w AS prev_election_label
    FROM mv_booth_party_share s
    WHERE s.party IS DISTINCT FROM 'NOTA'
    WINDOW w AS (PARTITION BY s.ac_id, s.booth_uid, s.party_id, s.election_type
                 ORDER BY s.election_year)
), quality AS (
    SELECT ac_id, election_id, booth_uid,
           MIN(crosswalk_confidence) AS confidence,
           BOOL_AND(crosswalk_reviewed) AS reviewed
    FROM mv_result_booth_candidate
    GROUP BY ac_id, election_id, booth_uid
)
SELECT sh.ac_id, sh.booth_uid, sh.party_id, sh.party, sh.election_type,
       sh.election_id, sh.election_label, sh.election_year,
       sh.prev_election_id, sh.prev_election_label,
       sh.share_pct, sh.prev_share_pct,
       -- NULL, not 0, when there is no prior election for this booth (D2), when
       -- the crosswalk is weak and unreviewed, or when the booth split or merged
       -- and the lineage group has not been aggregated.
       CASE
           WHEN sh.prev_share_pct IS NULL THEN NULL
           WHEN w.lineage_kind IN ('split', 'merge') THEN NULL
           WHEN COALESCE(q.reviewed, false) = false
                AND COALESCE(q.confidence, 0) < 0.85 THEN NULL
           ELSE ROUND((sh.share_pct - sh.prev_share_pct)::NUMERIC, 2)
       END AS swing_pct,
       CASE
           WHEN sh.prev_votes IS NULL THEN NULL
           WHEN w.lineage_kind IN ('split', 'merge') THEN NULL
           WHEN COALESCE(q.reviewed, false) = false
                AND COALESCE(q.confidence, 0) < 0.85 THEN NULL
           ELSE (sh.votes - sh.prev_votes)::INT
       END AS swing_votes,
       q.confidence AS crosswalk_confidence,
       q.reviewed   AS crosswalk_reviewed,
       w.lineage_kind
FROM shares sh
LEFT JOIN quality q ON q.ac_id = sh.ac_id AND q.election_id = sh.election_id
                   AND q.booth_uid = sh.booth_uid
LEFT JOIN mv_result_booth_wide w ON w.ac_id = sh.ac_id
                                AND w.election_id = sh.election_id
                                AND w.booth_uid = sh.booth_uid;

CREATE UNIQUE INDEX mv_swing_key
    ON mv_swing (ac_id, booth_uid, COALESCE(party_id, -1), election_id);

-- D9: a party that contested the earlier election and not the later one has no
-- row in the later one, so its collapse never appeared. JVM is exactly this -
-- it merged into BJP in 2020, so a 2019-to-2024 swing table showed BJP's gain
-- with no corresponding JVM loss. These are the missing rows.
CREATE MATERIALIZED VIEW mv_swing_vanished AS
WITH pairs AS (
    SELECT DISTINCT e1.ac_id, e1.election_id AS prev_election_id,
           e2.election_id AS election_id, e1.type AS election_type,
           e1.label AS prev_election_label, e2.label AS election_label, e2.year
    FROM election e1
    JOIN election e2 ON e2.ac_id = e1.ac_id AND e2.type = e1.type AND e2.year > e1.year
    WHERE NOT EXISTS (SELECT 1 FROM election mid
                       WHERE mid.ac_id = e1.ac_id AND mid.type = e1.type
                         AND mid.year > e1.year AND mid.year < e2.year)
)
SELECT p.ac_id, prev.booth_uid, prev.party_id, prev.party, p.election_type,
       p.election_id, p.election_label, p.year AS election_year,
       p.prev_election_id, p.prev_election_label,
       0.0::NUMERIC       AS share_pct,
       prev.share_pct     AS prev_share_pct,
       ROUND((0 - prev.share_pct)::NUMERIC, 2) AS swing_pct,
       (0 - prev.votes)::INT AS swing_votes
FROM pairs p
JOIN mv_booth_party_share prev
  ON prev.ac_id = p.ac_id AND prev.election_id = p.prev_election_id
WHERE prev.party IS DISTINCT FROM 'NOTA'
  AND NOT EXISTS (
      SELECT 1 FROM mv_booth_party_share now
       WHERE now.ac_id = p.ac_id AND now.election_id = p.election_id
         AND now.booth_uid = prev.booth_uid
         AND now.party_id IS NOT DISTINCT FROM prev.party_id
  )
  -- Only where the later election actually has results for this booth;
  -- otherwise "did not contest" is indistinguishable from "not loaded".
  AND EXISTS (
      SELECT 1 FROM mv_booth_totals t
       WHERE t.ac_id = p.ac_id AND t.election_id = p.election_id
         AND t.booth_uid = prev.booth_uid
  );

CREATE UNIQUE INDEX mv_swing_vanished_key
    ON mv_swing_vanished (ac_id, booth_uid, COALESCE(party_id, -1), election_id);

-- ---------------------------------------------------------------------------
-- 5. LS / VS transfer and the floating vote.
-- ---------------------------------------------------------------------------

CREATE MATERIALIZED VIEW mv_transfer_ls_vs AS
SELECT COALESCE(ls.ac_id, vs.ac_id)         AS ac_id,
       COALESCE(ls.election_year, vs.election_year) AS year,
       COALESCE(ls.booth_uid, vs.booth_uid) AS booth_uid,
       COALESCE(ls.party, vs.party)         AS party,
       COALESCE(ls.party_id, vs.party_id)   AS party_id,
       ls.votes      AS ls_votes,
       vs.votes      AS vs_votes,
       ls.share_pct  AS ls_share_pct,
       vs.share_pct  AS vs_share_pct,
       (vs.votes - ls.votes)::INT AS delta_votes,
       -- transfer_delta: NULL when either leg is missing, never a full share
       -- masquerading as a swing.
       CASE WHEN ls.share_pct IS NOT NULL AND vs.share_pct IS NOT NULL
            THEN ROUND((vs.share_pct - ls.share_pct)::NUMERIC, 2)
       END AS delta_share_pct
FROM (SELECT * FROM mv_booth_party_share WHERE election_type = 'LS') ls
FULL OUTER JOIN (SELECT * FROM mv_booth_party_share WHERE election_type = 'VS') vs
  ON vs.ac_id = ls.ac_id AND vs.booth_uid = ls.booth_uid
 AND vs.election_year = ls.election_year
 AND vs.party_id IS NOT DISTINCT FROM ls.party_id;

CREATE UNIQUE INDEX mv_transfer_key
    ON mv_transfer_ls_vs (ac_id, year, booth_uid, COALESCE(party_id, -1));

-- floating_pct: the Pedersen index, and NULL - not 50.00 - when only one poll
-- type exists for that year at that booth (D4). The old view's FULL OUTER JOIN
-- made every party's delta equal its full share in the single poll that
-- existed, so the index came to exactly 100/2 for every booth in the
-- constituency, appeared on the map and in the priority score, looked like a
-- finding, and flattened PERCENT_RANK so the priority score silently lost its
-- 0.20 floating-vote term.
CREATE MATERIALIZED VIEW mv_floating_vote AS
WITH legs AS (
    SELECT ac_id, election_year AS year, booth_uid,
           COUNT(DISTINCT election_type) AS poll_types
    FROM mv_booth_party_share
    WHERE election_type IN ('LS', 'VS')
    GROUP BY ac_id, election_year, booth_uid
)
SELECT t.ac_id, t.year, t.booth_uid,
       CASE WHEN l.poll_types = 2
            THEN ROUND((SUM(ABS(COALESCE(t.vs_share_pct, 0) - COALESCE(t.ls_share_pct, 0)))
                        / 2.0)::NUMERIC, 2)
       END AS floating_pct,
       l.poll_types
FROM mv_transfer_ls_vs t
JOIN legs l ON l.ac_id = t.ac_id AND l.year = t.year AND l.booth_uid = t.booth_uid
GROUP BY t.ac_id, t.year, t.booth_uid, l.poll_types;

CREATE UNIQUE INDEX mv_floating_key ON mv_floating_vote (ac_id, year, booth_uid);

-- ---------------------------------------------------------------------------
-- 6. Volatility: stdev of signed margin across available VS years.
-- ---------------------------------------------------------------------------

CREATE MATERIALIZED VIEW mv_volatility AS
SELECT ac_id, booth_uid,
       COUNT(signed_margin_pct)        AS years_available,
       -- NULL with fewer than two years: stddev_samp returns NULL there anyway,
       -- and the explicit guard documents the rule.
       CASE WHEN COUNT(signed_margin_pct) >= 2
            THEN ROUND(stddev_samp(signed_margin_pct)::NUMERIC, 2)
       END AS margin_stddev,
       ROUND(AVG(signed_margin_pct)::NUMERIC, 2) AS margin_mean
FROM mv_result_booth_wide
WHERE election_type = 'VS'
GROUP BY ac_id, booth_uid;

CREATE UNIQUE INDEX mv_volatility_key ON mv_volatility (ac_id, booth_uid);

-- ---------------------------------------------------------------------------
-- 7. New voters: additions in the window between two elections' linked rolls.
-- ---------------------------------------------------------------------------

CREATE MATERIALIZED VIEW mv_new_voter_share AS
WITH windows AS (
    -- The window is (linked roll of the previous election of this type, linked
    -- roll of this one]. Both ends must exist or every figure downstream is
    -- NULL - which is the honest answer, and what B1 got wrong by leaving the
    -- baseline CTE empty and reporting 0 additions everywhere.
    SELECT l.ac_id, l.election_id,
           e.type, e.year,
           r.revision_date AS window_end,
           LAG(r.revision_date) OVER (PARTITION BY l.ac_id, e.type ORDER BY e.year)
               AS window_start,
           l.revision_id   AS end_revision_id
    FROM election_roll_link l
    JOIN election e      ON e.election_id = l.election_id
    JOIN roll_revision r ON r.revision_id = l.revision_id
), changes AS (
    SELECT w.ac_id, w.election_id, c.booth_uid,
           SUM(c.additions)::INT     AS additions,
           SUM(c.deletions)::INT     AS deletions,
           SUM(c.modifications)::INT AS modifications
    FROM windows w
    JOIN roll_change c   ON c.ac_id = w.ac_id
    JOIN roll_revision r ON r.revision_id = c.revision_id
    WHERE w.window_start IS NOT NULL
      AND r.revision_date > w.window_start
      AND r.revision_date <= w.window_end
    GROUP BY w.ac_id, w.election_id, c.booth_uid
), electors_at AS (
    SELECT w.ac_id, w.election_id, s.booth_uid,
           s.electors AS electors_end
    FROM windows w
    JOIN roll_snapshot s ON s.revision_id = w.end_revision_id
), electors_start AS (
    SELECT w.ac_id, w.election_id, s.booth_uid, s.electors AS electors_start
    FROM windows w
    JOIN roll_revision r ON r.ac_id = w.ac_id AND r.is_mother
                        AND r.revision_date <= w.window_start
    JOIN roll_snapshot s ON s.revision_id = r.revision_id
    WHERE w.window_start IS NOT NULL
)
SELECT w.ac_id, w.election_id, b.booth_uid,
       c.additions, c.deletions, c.modifications,
       ee.electors_end AS electors,
       es.electors_start,
       -- new_voter_pct: additions / electors at window end. NULL when either
       -- roll is missing.
       CASE WHEN c.additions IS NOT NULL AND ee.electors_end > 0
            THEN ROUND((100.0 * c.additions / ee.electors_end)::NUMERIC, 2)
       END AS new_voter_pct,
       -- net_roll_change_pct: (additions - deletions) / electors at window start.
       CASE WHEN c.additions IS NOT NULL AND c.deletions IS NOT NULL
                 AND es.electors_start > 0
            THEN ROUND((100.0 * (c.additions - c.deletions) / es.electors_start)::NUMERIC, 2)
       END AS net_roll_change_pct
FROM windows w
JOIN booth b ON b.ac_id = w.ac_id
LEFT JOIN changes c        ON c.ac_id = w.ac_id AND c.election_id = w.election_id
                          AND c.booth_uid = b.booth_uid
LEFT JOIN electors_at ee   ON ee.ac_id = w.ac_id AND ee.election_id = w.election_id
                          AND ee.booth_uid = b.booth_uid
LEFT JOIN electors_start es ON es.ac_id = w.ac_id AND es.election_id = w.election_id
                           AND es.booth_uid = b.booth_uid;

CREATE UNIQUE INDEX mv_nvs_key ON mv_new_voter_share (ac_id, election_id, booth_uid);

-- ---------------------------------------------------------------------------
-- 8. Booth priority. Percentiles are computed WITHIN one AC.
-- ---------------------------------------------------------------------------

-- Per-AC is not a detail: Kanke has 4,81,815 electors against Giridih's
-- 3,04,898, so a percentile over the pooled set would rank Kanke's booths high
-- on electorate size alone and call that priority.
CREATE MATERIALIZED VIEW mv_booth_priority AS
WITH baseline AS (
    SELECT w.*
    FROM mv_result_booth_wide w
    JOIN election e ON e.election_id = w.election_id AND e.is_baseline
), inputs AS (
    SELECT b.ac_id, b.booth_uid, b.election_id, b.election_label, b.area_id,
           b.electors, b.turnout_pct, b.margin_pct, b.margin_votes,
           b.signed_margin_pct, b.winner_party, b.runner_party,
           n.new_voter_pct, n.additions,
           f.floating_pct,
           v.margin_stddev,
           v.years_available
    FROM baseline b
    LEFT JOIN mv_new_voter_share n ON n.ac_id = b.ac_id AND n.booth_uid = b.booth_uid
                                  AND n.election_id = b.election_id
    LEFT JOIN mv_floating_vote f   ON f.ac_id = b.ac_id AND f.booth_uid = b.booth_uid
                                  AND f.year = b.election_year
    LEFT JOIN mv_volatility v      ON v.ac_id = b.ac_id AND v.booth_uid = b.booth_uid
), ranked AS (
    SELECT i.*,
           -- closeness = 1 - margin percentile, so the tightest booth scores 1.
           CASE WHEN COUNT(i.margin_pct) OVER (PARTITION BY i.ac_id) >= 2
                THEN 1 - PERCENT_RANK() OVER (PARTITION BY i.ac_id ORDER BY i.margin_pct)
           END AS closeness_rank,
           CASE WHEN COUNT(i.new_voter_pct) OVER (PARTITION BY i.ac_id) >= 2
                THEN PERCENT_RANK() OVER (PARTITION BY i.ac_id ORDER BY i.new_voter_pct)
           END AS new_voter_rank,
           CASE WHEN COUNT(i.floating_pct) OVER (PARTITION BY i.ac_id) >= 2
                THEN PERCENT_RANK() OVER (PARTITION BY i.ac_id ORDER BY i.floating_pct)
           END AS floating_rank,
           CASE WHEN COUNT(i.margin_stddev) OVER (PARTITION BY i.ac_id) >= 2
                THEN PERCENT_RANK() OVER (PARTITION BY i.ac_id ORDER BY i.margin_stddev)
           END AS volatility_rank
    FROM inputs i
), weighted AS (
    SELECT r.*,
           -- Missing inputs are dropped and the remaining weights renormalised.
           (COALESCE(0.35 * r.closeness_rank, 0)
            + COALESCE(0.25 * r.new_voter_rank, 0)
            + COALESCE(0.20 * r.floating_rank, 0)
            + COALESCE(0.20 * r.volatility_rank, 0)) AS weighted_sum,
           (CASE WHEN r.closeness_rank  IS NULL THEN 0 ELSE 0.35 END
            + CASE WHEN r.new_voter_rank IS NULL THEN 0 ELSE 0.25 END
            + CASE WHEN r.floating_rank  IS NULL THEN 0 ELSE 0.20 END
            + CASE WHEN r.volatility_rank IS NULL THEN 0 ELSE 0.20 END) AS weight_used,
           -- Which inputs actually contributed. Two of the four were constant
           -- in the audited system - new voters 0 everywhere and floating 50.00
           -- everywhere - so a score that looked like a four-factor ranking was
           -- a two-factor one with no way to tell from the output.
           ARRAY_REMOVE(ARRAY[
               CASE WHEN r.closeness_rank  IS NOT NULL THEN 'closeness' END,
               CASE WHEN r.new_voter_rank  IS NOT NULL THEN 'new_voter_pct' END,
               CASE WHEN r.floating_rank   IS NOT NULL THEN 'floating_pct' END,
               CASE WHEN r.volatility_rank IS NOT NULL THEN 'volatility' END
           ], NULL) AS inputs_used
    FROM ranked r
)
SELECT ac_id, booth_uid, election_id, election_label, area_id,
       electors, turnout_pct, margin_pct, margin_votes, signed_margin_pct,
       winner_party, runner_party, new_voter_pct, additions, floating_pct,
       margin_stddev, years_available, inputs_used, weight_used,
       CASE WHEN weight_used > 0
            THEN ROUND((weighted_sum / weight_used)::NUMERIC, 4)
       END AS priority_score,
       CASE WHEN weight_used > 0
            THEN NTILE(4) OVER (PARTITION BY ac_id
                                ORDER BY weighted_sum / NULLIF(weight_used, 0) DESC)
       END AS priority_quartile
FROM weighted;

CREATE UNIQUE INDEX mv_priority_key ON mv_booth_priority (ac_id, booth_uid);
CREATE INDEX mv_priority_score ON mv_booth_priority (ac_id, priority_score DESC);

-- ---------------------------------------------------------------------------
-- 9. Area rollup: the same columns aggregated to panchayat or ward.
-- ---------------------------------------------------------------------------

CREATE MATERIALIZED VIEW mv_area_rollup AS
SELECT w.ac_id, w.election_id, w.election_label, w.election_type, w.election_year,
       a.area_id, a.name_en AS area_name_en, a.name_hi AS area_name_hi, a.kind AS area_kind,
       a.block_id, bl.name_en AS block_name_en,
       COUNT(*)                     AS booths,
       SUM(w.electors)::INT         AS electors,
       SUM(w.valid_votes)::INT      AS valid_votes,
       SUM(w.votes_polled)::INT     AS votes_polled,
       SUM(w.nota)::INT             AS nota,
       SUM(w.jmm)::INT AS jmm, SUM(w.bjp)::INT AS bjp, SUM(w.ajsu)::INT AS ajsu,
       SUM(w.jlkm)::INT AS jlkm, SUM(w.inc)::INT AS inc, SUM(w.rjd)::INT AS rjd,
       SUM(w.jvm)::INT AS jvm, SUM(w.others)::INT AS others,
       ROUND((100.0 * SUM(w.jmm) / NULLIF(SUM(w.valid_votes), 0))::NUMERIC, 2) AS jmm_pct,
       ROUND((100.0 * SUM(w.bjp) / NULLIF(SUM(w.valid_votes), 0))::NUMERIC, 2) AS bjp_pct,
       -- Turnout only where every booth in the area knows its electors;
       -- otherwise the denominator is a partial sum and the percentage is
       -- quietly wrong rather than absent.
       CASE WHEN COUNT(*) = COUNT(w.electors)
            THEN ROUND((100.0 * SUM(w.votes_polled) / NULLIF(SUM(w.electors), 0))::NUMERIC, 2)
       END AS turnout_pct,
       COUNT(w.electors) AS booths_with_electors
FROM mv_result_booth_wide w
JOIN area a  ON a.area_id = w.area_id
JOIN block bl ON bl.block_id = a.block_id
GROUP BY w.ac_id, w.election_id, w.election_label, w.election_type, w.election_year,
         a.area_id, a.name_en, a.name_hi, a.kind, a.block_id, bl.name_en;

CREATE UNIQUE INDEX mv_area_rollup_key ON mv_area_rollup (ac_id, election_id, area_id);

-- ---------------------------------------------------------------------------
-- 10. mv_ac_summary: one row per AC per election. The switcher and the
--     comparison screen read this (spec 2.5).
-- ---------------------------------------------------------------------------

CREATE MATERIALIZED VIEW mv_ac_summary AS
WITH totals AS (
    SELECT w.ac_id, w.election_id, w.election_label, w.election_type, w.election_year,
           COUNT(*)                AS booths,
           SUM(w.valid_votes)::INT AS valid_votes,
           SUM(w.votes_polled)::INT AS votes_polled,
           SUM(w.electors)::INT    AS electors,
           SUM(w.nota)::INT        AS nota,
           COUNT(w.electors)       AS booths_with_electors
    FROM mv_result_booth_wide w
    GROUP BY w.ac_id, w.election_id, w.election_label, w.election_type, w.election_year
), party_totals AS (
    SELECT ac_id, election_id, party, SUM(votes)::INT AS votes
    FROM mv_result_booth_party
    WHERE party IS DISTINCT FROM 'NOTA'
    GROUP BY ac_id, election_id, party
), ranked AS (
    SELECT pt.*, ROW_NUMBER() OVER (PARTITION BY pt.ac_id, pt.election_id
                                    ORDER BY pt.votes DESC, pt.party) AS rn
    FROM party_totals pt
), crosswalk AS (
    -- Coverage denominator is ps_list_entry, not booth_crosswalk. C11: the
    -- review-band stations that had no crosswalk row were missing from both the
    -- numerator and the denominator, so the check reported 100% healthy while
    -- 17% of the constituency had been dropped from every view.
    SELECT p.ac_id, p.election_id,
           COUNT(*) AS ps_rows,
           COUNT(x.booth_uid) AS matched,
           ROUND((100.0 * COUNT(x.booth_uid) / NULLIF(COUNT(*), 0))::NUMERIC, 2)
               AS coverage_pct
    FROM ps_list_entry p
    LEFT JOIN booth_crosswalk x
           ON x.election_id = p.election_id AND x.ps_number = p.ps_number
    GROUP BY p.ac_id, p.election_id
), new_voters AS (
    SELECT ac_id, election_id,
           SUM(additions)::INT AS additions,
           CASE WHEN SUM(electors) > 0
                THEN ROUND((100.0 * SUM(additions) / SUM(electors))::NUMERIC, 2)
           END AS new_voter_pct
    FROM mv_new_voter_share
    GROUP BY ac_id, election_id
)
SELECT t.ac_id, t.election_id, t.election_label, t.election_type, t.election_year AS year,
       e.is_baseline,
       t.booths, t.electors, t.valid_votes, t.votes_polled, t.nota,
       w.party  AS winner_party,
       w.votes  AS winner_votes,
       r.party  AS runner_party,
       r.votes  AS runner_votes,
       CASE WHEN r.votes IS NOT NULL THEN (w.votes - r.votes)::INT END AS margin_votes,
       CASE WHEN r.votes IS NOT NULL
            THEN ROUND((100.0 * (w.votes - r.votes) / NULLIF(t.valid_votes, 0))::NUMERIC, 2)
       END AS margin_pct,
       CASE
           WHEN r.votes IS NULL THEN NULL
           WHEN w.party = pa.abbr
               THEN ROUND((100.0 * (w.votes - r.votes) / NULLIF(t.valid_votes, 0))::NUMERIC, 2)
           WHEN w.party = pb.abbr
               THEN -ROUND((100.0 * (w.votes - r.votes) / NULLIF(t.valid_votes, 0))::NUMERIC, 2)
       END AS signed_margin_pct,
       CASE WHEN t.booths = t.booths_with_electors
            THEN ROUND((100.0 * t.votes_polled / NULLIF(t.electors, 0))::NUMERIC, 2)
       END AS turnout_pct,
       ROUND((100.0 * COALESCE(jl.votes, 0) / NULLIF(t.valid_votes, 0))::NUMERIC, 2)
           AS jlkm_share_pct,
       nv.additions,
       nv.new_voter_pct,
       cw.coverage_pct AS crosswalk_coverage_pct,
       cw.ps_rows      AS ps_list_rows
FROM totals t
JOIN election e  ON e.election_id = t.election_id
LEFT JOIN ranked w  ON w.ac_id = t.ac_id AND w.election_id = t.election_id AND w.rn = 1
LEFT JOIN ranked r  ON r.ac_id = t.ac_id AND r.election_id = t.election_id AND r.rn = 2
LEFT JOIN party_totals jl ON jl.ac_id = t.ac_id AND jl.election_id = t.election_id
                         AND jl.party = 'JLKM'
LEFT JOIN ac_contest ct ON ct.ac_id = t.ac_id AND ct.event_id = e.event_id
LEFT JOIN party pa ON pa.party_id = ct.party_a
LEFT JOIN party pb ON pb.party_id = ct.party_b
LEFT JOIN crosswalk cw ON cw.ac_id = t.ac_id AND cw.election_id = t.election_id
LEFT JOIN new_voters nv ON nv.ac_id = t.ac_id AND nv.election_id = t.election_id;

CREATE UNIQUE INDEX mv_ac_summary_key ON mv_ac_summary (ac_id, election_id);
CREATE INDEX mv_ac_summary_baseline ON mv_ac_summary (ac_id) WHERE is_baseline;
