-- 0020: three metric views rebuilt for two defects in 0015
--
-- 1. mv_booth_priority ranked a booth with a NULL input near the top. The
--    guard checked whether the *AC* had two values (`COUNT(x) OVER ... >= 2`),
--    not whether *this booth* had one, and PERCENT_RANK sorts NULLs last - so
--    a booth with no new-voter, floating or volatility figure got a rank near 1
--    for that input and an inflated priority score. The Python reference
--    (analytics/metrics.percentile_ranks) keeps NULL as NULL and ranks among
--    the present values only; the ranks below now do exactly that, partitioning
--    on `value IS NULL` so the denominator is the count of present values.
--
-- 2. mv_new_voter_share's electors_start joined every mother roll dated on or
--    before the window start. With two such rolls each booth appeared twice and
--    the refresh failed on the unique index mv_nvs_key.
--
-- mv_ac_summary reads mv_new_voter_share, so it is dropped and recreated
-- unchanged. All three bodies are otherwise copied verbatim from 0015.
-- 0021 re-grants SELECT on the rebuilt views to the read-only role.

DROP MATERIALIZED VIEW IF EXISTS mv_ac_summary;
DROP MATERIALIZED VIEW IF EXISTS mv_booth_priority;
DROP MATERIALIZED VIEW IF EXISTS mv_new_voter_share;

CREATE MATERIALIZED VIEW mv_new_voter_share AS
WITH windows AS (
    -- The window is (linked roll of the previous election of this type, linked
    -- roll of this one]. Both ends must exist or every figure downstream is
    -- NULL - which is the honest answer, and what B1 got wrong by leaving the
    -- baseline CTE empty and reporting 0 additions everywhere.
    -- ac_id from the election, not from the link table: election_roll_link is
    -- (election_id, revision_id) and has no ac_id. This said `l.ac_id`, so
    -- 0015 could not be applied at all - the first `apply_migrations` run
    -- against any database would have stopped here. It survived review because
    -- nothing had ever executed this file.
    SELECT e.ac_id, l.election_id,
           e.type, e.year,
           r.revision_date AS window_end,
           LAG(r.revision_date) OVER (PARTITION BY e.ac_id, e.type ORDER BY e.year)
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
    -- The latest mother roll on or before the window start, one row per booth.
    -- 0015 joined every mother roll dated on or before it, so an AC with two
    -- produced duplicate (ac, election, booth) rows and the refresh failed on
    -- the unique index mv_nvs_key.
    SELECT DISTINCT ON (w.ac_id, w.election_id, s.booth_uid)
           w.ac_id, w.election_id, s.booth_uid, s.electors AS electors_start
    FROM windows w
    JOIN roll_revision r ON r.ac_id = w.ac_id AND r.is_mother
                        AND r.revision_date <= w.window_start
    JOIN roll_snapshot s ON s.revision_id = r.revision_id
    WHERE w.window_start IS NOT NULL
    ORDER BY w.ac_id, w.election_id, s.booth_uid, r.revision_date DESC
)
SELECT w.ac_id, w.election_id, b.booth_uid,
       c.additions, c.deletions, c.modifications,
       ee.electors_end AS electors,
       es.electors_start,
       -- new_voter_pct: additions / electors at window end. NULL when either
       -- roll is missing.
       metric_new_voter_pct(c.additions, ee.electors_end) AS new_voter_pct,
       -- net_roll_change_pct: (additions - deletions) / electors at window start.
       metric_net_roll_change_pct(c.additions, c.deletions,
                                  es.electors_start) AS net_roll_change_pct
FROM windows w
JOIN booth b ON b.ac_id = w.ac_id
LEFT JOIN changes c        ON c.ac_id = w.ac_id AND c.election_id = w.election_id
                          AND c.booth_uid = b.booth_uid
LEFT JOIN electors_at ee   ON ee.ac_id = w.ac_id AND ee.election_id = w.election_id
                          AND ee.booth_uid = b.booth_uid
LEFT JOIN electors_start es ON es.ac_id = w.ac_id AND es.election_id = w.election_id
                           AND es.booth_uid = b.booth_uid;

CREATE UNIQUE INDEX mv_nvs_key ON mv_new_voter_share (ac_id, election_id, booth_uid);

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
           CASE WHEN i.margin_pct IS NOT NULL
                 AND COUNT(i.margin_pct) OVER (PARTITION BY i.ac_id) >= 2
                THEN 1 - PERCENT_RANK() OVER (PARTITION BY i.ac_id, i.margin_pct IS NULL
                                              ORDER BY i.margin_pct)
           END AS closeness_rank,
           CASE WHEN i.new_voter_pct IS NOT NULL
                 AND COUNT(i.new_voter_pct) OVER (PARTITION BY i.ac_id) >= 2
                THEN PERCENT_RANK() OVER (PARTITION BY i.ac_id, i.new_voter_pct IS NULL
                                          ORDER BY i.new_voter_pct)
           END AS new_voter_rank,
           CASE WHEN i.floating_pct IS NOT NULL
                 AND COUNT(i.floating_pct) OVER (PARTITION BY i.ac_id) >= 2
                THEN PERCENT_RANK() OVER (PARTITION BY i.ac_id, i.floating_pct IS NULL
                                          ORDER BY i.floating_pct)
           END AS floating_rank,
           CASE WHEN i.margin_stddev IS NOT NULL
                 AND COUNT(i.margin_stddev) OVER (PARTITION BY i.ac_id) >= 2
                THEN PERCENT_RANK() OVER (PARTITION BY i.ac_id, i.margin_stddev IS NULL
                                          ORDER BY i.margin_stddev)
           END AS volatility_rank
    FROM inputs i
), weighted AS (
    SELECT r.*,
           -- Missing inputs are dropped and the remaining weights
           -- renormalised, both inside the generated functions, so the four
           -- weights are written down in exactly one place in the codebase.
           metric_priority_score(r.closeness_rank, r.new_voter_rank,
                                 r.floating_rank, r.volatility_rank) AS score,
           metric_priority_weight(r.closeness_rank, r.new_voter_rank,
                                  r.floating_rank,
                                  r.volatility_rank) AS weight_used,
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
       score AS priority_score,
       -- Quartile over the score itself, so the ranking and the number shown
       -- beside it cannot be computed from different expressions.
       CASE WHEN score IS NOT NULL
            THEN NTILE(4) OVER (PARTITION BY ac_id ORDER BY score DESC)
       END AS priority_quartile
FROM weighted;

CREATE UNIQUE INDEX mv_priority_key ON mv_booth_priority (ac_id, booth_uid);
CREATE INDEX mv_priority_score ON mv_booth_priority (ac_id, priority_score DESC);

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
), candidate_totals AS (
    -- N2. Ranked per candidate, on the same grain and the same `contestant`
    -- key as mv_result_booth_candidate, so the AC headline and the booth table
    -- cannot name different winners.
    --
    -- This view used to rank over party_totals. D3 was fixed at booth grain -
    -- the old booth CTE grouped on COALESCE(party_id, -1), so eight
    -- independents on 500 votes each summed into one 4,000-vote pseudo-party
    -- that outranked a real winner on 3,000 - but the AC summary was left
    -- ranking the same way. Every independent shares a NULL party_id and
    -- collapsed into a single row, so the constituency headline could name a
    -- winner that no booth in the constituency had elected.
    --
    -- party_totals is kept below: it still feeds the party-level share
    -- figures, which are a real aggregate. It is only the ranking that must
    -- not bucket candidates together.
    SELECT ac_id, election_id, candidate_id, candidate_name, party, contestant,
           SUM(votes)::INT AS votes
    FROM mv_result_booth_candidate
    WHERE party IS DISTINCT FROM 'NOTA'
    GROUP BY ac_id, election_id, candidate_id, candidate_name, party, contestant
), ranked AS (
    SELECT ct.*, ROW_NUMBER() OVER (PARTITION BY ct.ac_id, ct.election_id
                                    ORDER BY ct.votes DESC,
                                             ct.candidate_name) AS rn
    FROM candidate_totals ct
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
), contestants AS (
    -- How many contestants the AC had, so that metric_margin_votes applies the
    -- same fewer-than-two rule here as it does at booth grain. The expressions
    -- below used to gate on `r.votes IS NOT NULL` instead - a different rule
    -- for the same metric at a different grain, which is exactly the drift
    -- these functions exist to prevent.
    --
    -- Counted over candidate_totals, so eleven candidates are eleven
    -- contestants and three of them being independents does not make them one.
    SELECT ac_id, election_id, COUNT(*)::INT AS contestants
    FROM candidate_totals
    GROUP BY ac_id, election_id
), new_voters AS (
    SELECT ac_id, election_id,
           SUM(additions)::INT AS additions,
           metric_new_voter_pct(SUM(additions)::INT,
                                SUM(electors)::INT) AS new_voter_pct
    FROM mv_new_voter_share
    GROUP BY ac_id, election_id
)
SELECT t.ac_id, t.election_id, t.election_label, t.election_type, t.election_year AS year,
       e.is_baseline,
       t.booths, t.electors, t.valid_votes, t.votes_polled, t.nota,
       -- `contestant`, not `party`, matching mv_result_booth_wide: for a
       -- major party the two are the same string, and for an independent the
       -- contestant key keeps the candidates apart instead of merging them.
       w.contestant     AS winner_party,
       w.candidate_name AS winner_candidate,
       w.votes          AS winner_votes,
       r.contestant     AS runner_party,
       r.candidate_name AS runner_candidate,
       r.votes          AS runner_votes,
       metric_margin_votes(w.votes, r.votes, cn.contestants)::INT AS margin_votes,
       metric_margin_pct(w.votes, r.votes, cn.contestants,
                         t.valid_votes) AS margin_pct,
       metric_signed_margin_pct(w.contestant, w.votes, r.votes, cn.contestants,
                                t.valid_votes, pa.abbr,
                                pb.abbr) AS signed_margin_pct,
       -- Turnout only where every booth in the AC knows its electors;
       -- otherwise the denominator is a partial sum and the percentage is
       -- quietly wrong rather than absent.
       CASE WHEN t.booths = t.booths_with_electors
            THEN metric_turnout_pct(t.votes_polled, t.electors)
       END AS turnout_pct,
       metric_share_pct(COALESCE(jl.votes, 0), t.valid_votes) AS jlkm_share_pct,
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
LEFT JOIN new_voters nv ON nv.ac_id = t.ac_id AND nv.election_id = t.election_id
LEFT JOIN contestants cn ON cn.ac_id = t.ac_id AND cn.election_id = t.election_id;

CREATE UNIQUE INDEX mv_ac_summary_key ON mv_ac_summary (ac_id, election_id);
CREATE INDEX mv_ac_summary_baseline ON mv_ac_summary (ac_id) WHERE is_baseline;
