-- Reference queries for the metric definitions in HLD 10.
--
-- The materialized views themselves live in db/migrations/0009-0011 so they are
-- versioned with the schema rather than applied ad hoc. This file is the
-- analyst's crib sheet: the same questions, written as plain SELECTs, useful
-- for spot-checking a view after a load and as worked examples for the
-- chatbot's schema documentation.

-- 1. The twenty booths where BJP led by most in VS-2024.
SELECT booth_uid, area_id, bjp, jmm, (bjp - jmm) AS bjp_lead, turnout_pct
FROM mv_result_booth_wide
WHERE election_label = 'VS-2024'
ORDER BY (bjp - jmm) DESC
LIMIT 20;

-- 2. Swing to JMM between VS-2019 and VS-2024, worst first.
SELECT s.booth_uid, a.name_en AS area, s.prev_share_pct, s.share_pct, s.swing_pct
FROM mv_swing s
JOIN party p ON p.party_id = s.party_id AND p.abbr = 'JMM'
JOIN booth b ON b.booth_uid = s.booth_uid
JOIN area a  ON a.area_id = b.area_id
JOIN election e ON e.election_id = s.election_id AND e.label = 'VS-2024'
ORDER BY s.swing_pct ASC
LIMIT 20;

-- 3. Wards that went AJSU in LS-2024 but JMM in VS-2024.
WITH ls_winner AS (
    SELECT DISTINCT ON (booth_uid) booth_uid, party
    FROM mv_transfer_ls_vs WHERE year = 2024 ORDER BY booth_uid, ls_votes DESC
),
vs_winner AS (
    SELECT DISTINCT ON (booth_uid) booth_uid, party
    FROM mv_transfer_ls_vs WHERE year = 2024 ORDER BY booth_uid, vs_votes DESC
)
SELECT a.name_en AS ward, COUNT(*) AS booths
FROM ls_winner l
JOIN vs_winner v ON v.booth_uid = l.booth_uid
JOIN booth b ON b.booth_uid = l.booth_uid
JOIN area a  ON a.area_id = b.area_id AND a.kind = 'ward'
WHERE l.party = 'AJSU' AND v.party = 'JMM'
GROUP BY a.name_en
ORDER BY booths DESC;

-- 4. New voters added per panchayat since the baseline roll.
SELECT a.name_en AS panchayat, a.name_hi,
       SUM(n.additions) AS additions,
       SUM(n.deletions) AS deletions,
       SUM(n.electors_now) AS electors,
       ROUND(100.0 * SUM(n.additions) / NULLIF(SUM(n.electors_now), 0), 2) AS new_voter_pct
FROM mv_new_voter_share n
JOIN area a ON a.area_id = n.area_id
WHERE a.kind = 'panchayat'
GROUP BY a.name_en, a.name_hi
ORDER BY new_voter_pct DESC;

-- 5. Priority booths: tight margin, many new voters, volatile, floating vote.
SELECT p.booth_uid, a.name_en AS area, p.margin_pct, p.new_voter_pct,
       p.margin_stddev, p.floating_pct, p.priority_score
FROM mv_booth_priority p
JOIN area a ON a.area_id = p.area_id
ORDER BY p.priority_score DESC
LIMIT 30;

-- 6. Caste-vote association, booth level, blend estimates only.
-- Read as ECOLOGICAL CORRELATION. It says nothing about how any individual or
-- any community voted (HLD 5, 10).
SELECT c.name_en AS community,
       ROUND(CORR(ce.est_pct, w.jmm::NUMERIC / NULLIF(w.votes_polled, 0) * 100)::NUMERIC, 3) AS corr_jmm,
       ROUND(CORR(ce.est_pct, w.bjp::NUMERIC / NULLIF(w.votes_polled, 0) * 100)::NUMERIC, 3) AS corr_bjp,
       COUNT(*) AS booths
FROM caste_estimate ce
JOIN community c ON c.community_id = ce.community_id
JOIN mv_result_booth_wide w ON w.booth_uid = ce.booth_uid
JOIN election e ON e.election_id = w.election_id AND e.is_baseline
WHERE ce.source = 'blend' AND ce.confidence >= 0.4
GROUP BY c.name_en
HAVING COUNT(*) >= 30
ORDER BY ABS(COALESCE(CORR(ce.est_pct, w.jmm::NUMERIC / NULLIF(w.votes_polled, 0) * 100), 0)) DESC;
