-- 0011: booth priority score and area rollup (HLD 10)

-- Floating vote per booth: Pedersen index between the LS and VS polls of the
-- same year - half the sum of absolute party share changes. High value = the
-- booth votes very differently in the two polls, i.e. a genuinely movable vote.
CREATE MATERIALIZED VIEW mv_floating_vote AS
SELECT year,
       booth_uid,
       ROUND(SUM(ABS(delta_share_pct)) / 2.0, 2) AS floating_pct
FROM mv_transfer_ls_vs
GROUP BY year, booth_uid;

CREATE UNIQUE INDEX mv_floating_key ON mv_floating_vote (year, booth_uid);

CREATE MATERIALIZED VIEW mv_booth_priority AS
WITH baseline AS (
    SELECT election_id, year FROM election WHERE is_baseline ORDER BY year DESC LIMIT 1
),
base AS (
    SELECT w.booth_uid,
           w.area_id,
           w.margin_pct,
           w.margin_votes,
           w.electors,
           w.turnout_pct,
           w.winner_party,
           w.runner_party
    FROM mv_result_booth_wide w
    WHERE w.election_id = (SELECT election_id FROM baseline)
),
joined AS (
    SELECT b.booth_uid,
           b.area_id,
           b.margin_pct,
           b.margin_votes,
           b.electors,
           b.turnout_pct,
           b.winner_party,
           b.runner_party,
           COALESCE(n.new_voter_pct, 0)   AS new_voter_pct,
           COALESCE(n.additions, 0)       AS additions,
           COALESCE(v.margin_stddev, 0)   AS margin_stddev,
           COALESCE(f.floating_pct, 0)    AS floating_pct
    FROM base b
    LEFT JOIN mv_new_voter_share n ON n.booth_uid = b.booth_uid
    LEFT JOIN mv_volatility v      ON v.booth_uid = b.booth_uid
    LEFT JOIN mv_floating_vote f   ON f.booth_uid = b.booth_uid
                                  AND f.year = (SELECT year FROM baseline)
),
ranked AS (
    SELECT j.*,
           PERCENT_RANK() OVER (ORDER BY j.margin_pct ASC NULLS LAST)  AS pr_margin,
           PERCENT_RANK() OVER (ORDER BY j.new_voter_pct ASC)          AS pr_new_voter,
           PERCENT_RANK() OVER (ORDER BY j.margin_stddev ASC)          AS pr_volatility,
           PERCENT_RANK() OVER (ORDER BY j.floating_pct ASC)           AS pr_floating
    FROM joined j
)
SELECT booth_uid,
       area_id,
       margin_pct,
       margin_votes,
       electors,
       turnout_pct,
       winner_party,
       runner_party,
       new_voter_pct,
       additions,
       margin_stddev,
       floating_pct,
       ROUND((100 * (
             0.35 * (1 - pr_margin)
           + 0.25 * pr_new_voter
           + 0.20 * pr_volatility
           + 0.20 * pr_floating
       ))::NUMERIC, 1) AS priority_score,
       NTILE(4) OVER (ORDER BY (
             0.35 * (1 - pr_margin)
           + 0.25 * pr_new_voter
           + 0.20 * pr_volatility
           + 0.20 * pr_floating
       ) DESC) AS priority_quartile
FROM ranked;

CREATE UNIQUE INDEX mv_priority_key ON mv_booth_priority (booth_uid);
CREATE INDEX mv_priority_score_idx  ON mv_booth_priority (priority_score DESC);

-- Area rollup: the same result numbers aggregated to panchayat / ward.
CREATE MATERIALIZED VIEW mv_area_rollup AS
SELECT w.election_id,
       w.election_label,
       w.election_type,
       w.election_year,
       a.area_id,
       a.name_en   AS area_name_en,
       a.name_hi   AS area_name_hi,
       a.kind      AS area_kind,
       a.block_id,
       bl.name_en  AS block_name_en,
       COUNT(*)::INT              AS booths,
       SUM(w.electors)::INT       AS electors,
       SUM(w.votes_counted)::INT  AS votes_counted,
       SUM(w.jmm)::INT   AS jmm,
       SUM(w.bjp)::INT   AS bjp,
       SUM(w.ajsu)::INT  AS ajsu,
       SUM(w.jlkm)::INT  AS jlkm,
       SUM(w.inc)::INT   AS inc,
       SUM(w.rjd)::INT   AS rjd,
       SUM(w.jvm)::INT   AS jvm,
       SUM(w.others)::INT AS others,
       SUM(w.nota)::INT  AS nota,
       ROUND((100.0 * SUM(w.votes_counted) / NULLIF(SUM(w.electors), 0))::NUMERIC, 2) AS turnout_pct,
       ROUND((100.0 * SUM(w.jmm) / NULLIF(SUM(w.votes_counted), 0))::NUMERIC, 2) AS jmm_pct,
       ROUND((100.0 * SUM(w.bjp) / NULLIF(SUM(w.votes_counted), 0))::NUMERIC, 2) AS bjp_pct,
       (SUM(w.jmm) - SUM(w.bjp))::INT AS jmm_minus_bjp
FROM mv_result_booth_wide w
JOIN area a  ON a.area_id = w.area_id
JOIN block bl ON bl.block_id = a.block_id
GROUP BY w.election_id, w.election_label, w.election_type, w.election_year,
         a.area_id, a.name_en, a.name_hi, a.kind, a.block_id, bl.name_en;

CREATE UNIQUE INDEX mv_area_rollup_key ON mv_area_rollup (election_id, area_id);
