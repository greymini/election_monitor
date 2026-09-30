-- 0010: analytic materialized views (HLD 10 metric definitions)

-- Swing: change in a party vote share between consecutive elections OF THE SAME
-- TYPE, per booth. A party absent in the earlier election counts as 0% there.
CREATE MATERIALIZED VIEW mv_swing AS
SELECT s.election_id,
       s.type,
       s.year,
       s.booth_uid,
       s.party_id,
       s.share_pct,
       LAG(s.election_id) OVER w                                AS prev_election_id,
       LAG(s.year)        OVER w                                AS prev_year,
       LAG(s.share_pct)   OVER w                                AS prev_share_pct,
       ROUND(s.share_pct - COALESCE(LAG(s.share_pct) OVER w, 0), 2) AS swing_pct,
       s.votes - COALESCE(LAG(s.votes) OVER w, 0)               AS swing_votes
FROM mv_booth_party_share s
WINDOW w AS (PARTITION BY s.booth_uid, s.party_id, s.type ORDER BY s.year);

CREATE UNIQUE INDEX mv_swing_key ON mv_swing (election_id, booth_uid, party_id);
CREATE INDEX mv_swing_booth_idx  ON mv_swing (booth_uid, party_id);

-- LS <-> VS transfer for a common year: where did the Lok Sabha vote go six
-- months later? This is the single most important dynamic for AC-32 (HLD 1.1).
CREATE MATERIALIZED VIEW mv_transfer_ls_vs AS
WITH ls AS (
    SELECT year, booth_uid, party_id, votes, share_pct FROM mv_booth_party_share WHERE type = 'LS'
),
vs AS (
    SELECT year, booth_uid, party_id, votes, share_pct FROM mv_booth_party_share WHERE type = 'VS'
)
SELECT COALESCE(ls.year, vs.year)           AS year,
       COALESCE(ls.booth_uid, vs.booth_uid) AS booth_uid,
       COALESCE(ls.party_id, vs.party_id)   AS party_id,
       p.abbr                                AS party,
       COALESCE(ls.votes, 0)::INT            AS ls_votes,
       COALESCE(vs.votes, 0)::INT            AS vs_votes,
       COALESCE(vs.votes, 0)::INT - COALESCE(ls.votes, 0)::INT AS delta_votes,
       COALESCE(ls.share_pct, 0)             AS ls_share_pct,
       COALESCE(vs.share_pct, 0)             AS vs_share_pct,
       ROUND(COALESCE(vs.share_pct, 0) - COALESCE(ls.share_pct, 0), 2) AS delta_share_pct
FROM ls
FULL OUTER JOIN vs
  ON vs.year = ls.year AND vs.booth_uid = ls.booth_uid AND vs.party_id = ls.party_id
LEFT JOIN party p ON p.party_id = COALESCE(ls.party_id, vs.party_id);

CREATE UNIQUE INDEX mv_transfer_key ON mv_transfer_ls_vs (year, booth_uid, party_id);
CREATE INDEX mv_transfer_booth_idx  ON mv_transfer_ls_vs (booth_uid);

-- Volatility: spread of the winning margin across assembly elections per booth.
CREATE MATERIALIZED VIEW mv_volatility AS
SELECT booth_uid,
       COUNT(*)::INT                        AS elections_counted,
       ROUND(STDDEV_SAMP(margin_pct), 2)    AS margin_stddev,
       ROUND(AVG(margin_pct), 2)            AS margin_avg,
       COUNT(DISTINCT winner_party_id)::INT AS distinct_winners
FROM mv_result_booth_wide
WHERE election_type = 'VS' AND margin_pct IS NOT NULL
GROUP BY booth_uid;

CREATE UNIQUE INDEX mv_volatility_key ON mv_volatility (booth_uid);

-- New-voter share: additions recorded after the roll revision used at the
-- baseline election, over current electors.
CREATE MATERIALIZED VIEW mv_new_voter_share AS
WITH base AS (
    SELECT rr.revision_date
    FROM election_roll_link l
    JOIN election e      ON e.election_id = l.election_id
    JOIN roll_revision rr ON rr.revision_id = l.revision_id
    WHERE e.is_baseline
    ORDER BY rr.revision_date DESC
    LIMIT 1
),
latest AS (
    SELECT revision_id, revision_date FROM roll_revision ORDER BY revision_date DESC LIMIT 1
),
changes AS (
    SELECT rc.booth_uid,
           SUM(rc.additions)::INT                  AS additions,
           SUM(rc.deletions)::INT                  AS deletions,
           SUM(COALESCE(rc.add_18_19, 0))::INT     AS add_18_19,
           SUM(COALESCE(rc.add_female, 0))::INT    AS add_female,
           SUM(COALESCE(rc.del_death, 0))::INT     AS del_death,
           SUM(COALESCE(rc.del_shifted, 0))::INT   AS del_shifted
    FROM roll_change rc
    JOIN roll_revision rr ON rr.revision_id = rc.revision_id
    WHERE rr.revision_date > (SELECT revision_date FROM base)
    GROUP BY rc.booth_uid
),
current_roll AS (
    SELECT rs.booth_uid, rs.electors
    FROM roll_snapshot rs
    WHERE rs.revision_id = (SELECT revision_id FROM latest)
)
SELECT b.booth_uid,
       b.area_id,
       COALESCE(cr.electors, 0)::INT   AS electors_now,
       COALESCE(ch.additions, 0)       AS additions,
       COALESCE(ch.deletions, 0)       AS deletions,
       COALESCE(ch.additions, 0) - COALESCE(ch.deletions, 0) AS net_change,
       COALESCE(ch.add_18_19, 0)       AS add_18_19,
       COALESCE(ch.add_female, 0)      AS add_female,
       COALESCE(ch.del_death, 0)       AS del_death,
       COALESCE(ch.del_shifted, 0)     AS del_shifted,
       ROUND((100.0 * COALESCE(ch.additions, 0) / NULLIF(cr.electors, 0))::NUMERIC, 2) AS new_voter_pct,
       ROUND((100.0 * COALESCE(ch.deletions, 0) / NULLIF(cr.electors, 0))::NUMERIC, 2) AS deleted_pct
FROM booth b
LEFT JOIN changes ch      ON ch.booth_uid = b.booth_uid
LEFT JOIN current_roll cr ON cr.booth_uid = b.booth_uid;

CREATE UNIQUE INDEX mv_nvs_key ON mv_new_voter_share (booth_uid);
