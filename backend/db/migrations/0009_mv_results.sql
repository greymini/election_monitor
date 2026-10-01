-- 0009: result materialized views (LLD 3)
-- Every multi-year view reads results THROUGH booth_crosswalk, never through raw
-- ps_number. Where a crosswalk marks a split/merge, votes are summed onto the
-- surviving booth_uid so the comparison stays like-for-like (LLD 4.4 step 4).
--
-- party_id = -1 means the Form 20 column could not be attributed to a party
-- (independents loaded before party assignment). Those votes land in "others".

CREATE MATERIALIZED VIEW mv_result_booth_party AS
SELECT r.election_id,
       x.booth_uid,
       COALESCE(c.party_id, -1) AS party_id,
       SUM(r.votes)::INT        AS votes,
       COUNT(*)::INT            AS ps_count,
       MIN(x.confidence)        AS crosswalk_conf
FROM result_booth r
JOIN booth_crosswalk x ON x.election_id = r.election_id AND x.ps_number = r.ps_number
JOIN candidate c       ON c.candidate_id = r.candidate_id
GROUP BY r.election_id, x.booth_uid, COALESCE(c.party_id, -1);

CREATE UNIQUE INDEX mv_rbp_key ON mv_result_booth_party (election_id, booth_uid, party_id);
CREATE INDEX mv_rbp_booth_idx  ON mv_result_booth_party (booth_uid);

-- Vote share per party per booth, with election type/year carried through so the
-- swing view can window over it.
CREATE MATERIALIZED VIEW mv_booth_party_share AS
WITH tot AS (
    SELECT election_id, booth_uid, SUM(votes)::INT AS total_votes
    FROM mv_result_booth_party
    GROUP BY election_id, booth_uid
)
SELECT p.election_id,
       e.type,
       e.year,
       p.booth_uid,
       p.party_id,
       p.votes,
       t.total_votes,
       ROUND((100.0 * p.votes / NULLIF(t.total_votes, 0))::NUMERIC, 2) AS share_pct
FROM mv_result_booth_party p
JOIN election e ON e.election_id = p.election_id
JOIN tot t      ON t.election_id = p.election_id AND t.booth_uid = p.booth_uid;

CREATE UNIQUE INDEX mv_bps_key ON mv_booth_party_share (election_id, booth_uid, party_id);
CREATE INDEX mv_bps_type_idx   ON mv_booth_party_share (type, year);

-- Wide table: one row per election x booth, main parties as columns.
CREATE MATERIALIZED VIEW mv_result_booth_wide AS
WITH meta AS (
    SELECT m.election_id,
           x.booth_uid,
           SUM(m.electors)::INT    AS electors,
           SUM(m.total_valid)::INT AS total_valid,
           SUM(m.nota)::INT        AS nota,
           SUM(m.rejected)::INT    AS rejected,
           SUM(m.postal)::INT      AS postal,
           MIN(m.source_doc)       AS source_doc,
           MIN(m.source_page)      AS source_page,
           STRING_AGG(m.ps_number::TEXT, ',' ORDER BY m.ps_number) AS ps_numbers
    FROM result_booth_meta m
    JOIN booth_crosswalk x ON x.election_id = m.election_id AND x.ps_number = m.ps_number
    GROUP BY m.election_id, x.booth_uid
),
pivot AS (
    SELECT p.election_id,
           p.booth_uid,
           SUM(p.votes)                                              AS votes_total,
           SUM(p.votes) FILTER (WHERE pa.abbr = 'JMM')  AS jmm,
           SUM(p.votes) FILTER (WHERE pa.abbr = 'BJP')  AS bjp,
           SUM(p.votes) FILTER (WHERE pa.abbr = 'AJSU') AS ajsu,
           SUM(p.votes) FILTER (WHERE pa.abbr = 'JLKM') AS jlkm,
           SUM(p.votes) FILTER (WHERE pa.abbr = 'INC')  AS inc,
           SUM(p.votes) FILTER (WHERE pa.abbr = 'RJD')  AS rjd,
           SUM(p.votes) FILTER (WHERE pa.abbr = 'JVM')  AS jvm,
           SUM(p.votes) FILTER (WHERE pa.abbr IS NULL
                                   OR pa.abbr NOT IN ('JMM','BJP','AJSU','JLKM','INC','RJD','JVM','NOTA')) AS others,
           SUM(p.votes) FILTER (WHERE pa.abbr = 'NOTA') AS nota_votes
    FROM mv_result_booth_party p
    LEFT JOIN party pa ON pa.party_id = p.party_id
    GROUP BY p.election_id, p.booth_uid
),
ranked AS (
    SELECT election_id, booth_uid, party_id, votes,
           ROW_NUMBER() OVER (PARTITION BY election_id, booth_uid ORDER BY votes DESC, party_id) AS rn
    FROM mv_result_booth_party
)
SELECT pv.election_id,
       e.label                AS election_label,
       e.type                 AS election_type,
       e.year                 AS election_year,
       pv.booth_uid,
       b.area_id,
       a.block_id,
       m.ps_numbers,
       m.electors,
       COALESCE(m.total_valid, pv.votes_total)::INT AS total_valid,
       pv.votes_total::INT    AS votes_counted,
       COALESCE(pv.jmm, 0)::INT  AS jmm,
       COALESCE(pv.bjp, 0)::INT  AS bjp,
       COALESCE(pv.ajsu, 0)::INT AS ajsu,
       COALESCE(pv.jlkm, 0)::INT AS jlkm,
       COALESCE(pv.inc, 0)::INT  AS inc,
       COALESCE(pv.rjd, 0)::INT  AS rjd,
       COALESCE(pv.jvm, 0)::INT  AS jvm,
       COALESCE(pv.others, 0)::INT     AS others,
       COALESCE(pv.nota_votes, m.nota, 0)::INT AS nota,
       w.party_id             AS winner_party_id,
       wp.abbr                AS winner_party,
       w.votes                AS winner_votes,
       ru.party_id            AS runner_party_id,
       rup.abbr               AS runner_party,
       ru.votes               AS runner_votes,
       (w.votes - COALESCE(ru.votes, 0))::INT AS margin_votes,
       ROUND((100.0 * (w.votes - COALESCE(ru.votes, 0)) / NULLIF(pv.votes_total, 0))::NUMERIC, 2) AS margin_pct,
       ROUND((100.0 * pv.votes_total / NULLIF(m.electors, 0))::NUMERIC, 2) AS turnout_pct,
       m.source_doc,
       m.source_page
FROM pivot pv
JOIN election e  ON e.election_id = pv.election_id
JOIN booth b     ON b.booth_uid = pv.booth_uid
JOIN area a      ON a.area_id = b.area_id
LEFT JOIN meta m ON m.election_id = pv.election_id AND m.booth_uid = pv.booth_uid
LEFT JOIN ranked w   ON w.election_id = pv.election_id  AND w.booth_uid = pv.booth_uid  AND w.rn = 1
LEFT JOIN party wp   ON wp.party_id = w.party_id
LEFT JOIN ranked ru  ON ru.election_id = pv.election_id AND ru.booth_uid = pv.booth_uid AND ru.rn = 2
LEFT JOIN party rup  ON rup.party_id = ru.party_id;

CREATE UNIQUE INDEX mv_rbw_key   ON mv_result_booth_wide (election_id, booth_uid);
CREATE INDEX mv_rbw_area_idx     ON mv_result_booth_wide (area_id);
CREATE INDEX mv_rbw_election_idx ON mv_result_booth_wide (election_id);
