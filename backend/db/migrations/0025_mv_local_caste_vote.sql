-- 0025: caste composition aggregated to panchayat area level.
--
-- Rolls up booth-level caste estimates from caste_estimate to the panchayat
-- (area) level so the /local-caste endpoint can show the estimated community
-- composition of each GP without returning per-booth granularity.
--
-- The view is empty until caste_estimate rows are loaded for the AC. That is
-- expected: the endpoint returns an empty array, not an error, until then.
--
-- Turnout-weighted vote estimate: for each booth we know the total valid votes
-- cast (result_booth_meta) and the total registered electors (roll_snapshot).
-- We scale each community's estimated voter count by the booth's turnout rate
-- to get an estimated community vote. This is an approximation because caste
-- does not determine individual vote choices, but it gives the most useful
-- upper-bound denominator for the frontend's "community share of vote" display.
--
-- The view joins on the most recent VS election result per booth (ORDER BY
-- election_id DESC) so it always uses the latest available results data.

CREATE MATERIALIZED VIEW mv_local_caste_vote AS
SELECT
    a.area_id,
    a.name_en                               AS area_en,
    a.name_hi                               AS area_hi,
    bl.name_en                              AS block_en,
    a.ac_id,
    cm.community_id,
    cm.name_en                              AS community,
    SUM(ce.estimate)::INT                   AS estimated_voters,
    ROUND(
        100.0 * SUM(ce.estimate) /
        NULLIF(SUM(SUM(ce.estimate)) OVER (PARTITION BY a.area_id), 0),
        1
    )                                       AS community_pct,
    -- Turnout-weighted estimated votes (NULL when booth result data absent)
    SUM(
        CASE
            WHEN snap.electors > 0 AND rbm.total_valid IS NOT NULL
            THEN ce.estimate::FLOAT / snap.electors * rbm.total_valid
            ELSE NULL
        END
    )::INT                                  AS estimated_votes
FROM caste_estimate ce
JOIN booth b
    ON  b.booth_uid  = ce.booth_uid
JOIN area a
    ON  a.area_id    = b.area_id
   AND  a.kind       = 'panchayat'
JOIN block bl
    ON  bl.block_id  = a.block_id
JOIN community cm
    ON  cm.community_id = ce.community_id
-- latest roll snapshot for electorate count
LEFT JOIN LATERAL (
    SELECT rs.electors
    FROM   roll_snapshot rs
    WHERE  rs.booth_uid = ce.booth_uid
    ORDER  BY rs.revision_id DESC
    LIMIT  1
) snap ON true
-- latest VS result_booth_meta for total valid votes
LEFT JOIN LATERAL (
    SELECT rbm2.total_valid
    FROM   result_booth_meta rbm2
    JOIN   election e2 ON e2.election_id = rbm2.election_id
    WHERE  rbm2.booth_uid = ce.booth_uid
      AND  e2.kind        = 'VS'
      AND  e2.ac_id       = a.ac_id
    ORDER  BY e2.election_id DESC
    LIMIT  1
) rbm ON true
GROUP BY
    a.area_id, a.name_en, a.name_hi, bl.name_en, a.ac_id,
    cm.community_id, cm.name_en
WITH DATA;

CREATE UNIQUE INDEX mv_local_caste_vote_pk
    ON mv_local_caste_vote (area_id, community_id);

CREATE INDEX mv_local_caste_vote_ac_idx
    ON mv_local_caste_vote (ac_id);

COMMENT ON MATERIALIZED VIEW mv_local_caste_vote IS
    'Caste composition by panchayat area, derived from booth-level caste estimates. '
    'Refresh with: REFRESH MATERIALIZED VIEW CONCURRENTLY mv_local_caste_vote. '
    'Empty until caste_estimate rows are loaded for the AC.';
