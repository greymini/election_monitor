-- 0021: restore the read-only role's grants on rebuilt views; add the AC spine
--
-- A GRANT belongs to the object, not to its name. 0014 dropped every
-- materialized view, 0015 recreated them and 0020 rebuilt three again, so
-- giridih_ro - the role chatbot/sql_guard.py's run_sql uses - lost SELECT on
-- every mv_* it was granted in 0012 and could read only the base tables.
--
-- This re-grants the 0012 list and adds the multi-constituency tables a query
-- now needs to scope itself (ac, election_event, ac_contest, mv_ac_summary,
-- mv_result_booth_candidate). The union of the GRANT blocks in 0012 and here
-- must equal ALLOWED_TABLES in chatbot/sql_guard.py; tests/test_sql_guard.py
-- checks that.
--
-- Any later migration that drops and recreates one of these must re-grant it.

GRANT SELECT ON
    mv_result_booth_party,
    mv_booth_party_share,
    mv_result_booth_wide,
    mv_swing,
    mv_transfer_ls_vs,
    mv_volatility,
    mv_new_voter_share,
    mv_floating_vote,
    mv_booth_priority,
    mv_area_rollup,
    mv_result_booth_candidate,
    mv_ac_summary,
    ac,
    election_event,
    ac_contest
TO giridih_ro;
