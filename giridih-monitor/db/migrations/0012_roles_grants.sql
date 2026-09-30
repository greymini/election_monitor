-- 0012: least-privilege role for the chatbot run_sql tool (LLD 8.2, 12)
--
-- Two layers of defence, both required:
--   1. sql_guard.py rejects non-SELECT statements and any table outside the
--      allow-list before the query ever reaches Postgres.
--   2. This role physically cannot read anything else, so a guard bypass is
--      still contained. In particular it has NO access to app_user, auth_otp,
--      llm_prompt_log, ground_report or news_item body text.
--
-- The password is set by db/apply_migrations.py from READONLY_DB_PASSWORD.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'giridih_ro') THEN
        CREATE ROLE giridih_ro LOGIN PASSWORD 'set-by-apply-migrations';
    END IF;
END
$$;

REVOKE ALL ON SCHEMA public FROM giridih_ro;
GRANT USAGE ON SCHEMA public TO giridih_ro;

-- Allow-list, mirroring chatbot/sql_guard.py ALLOWED_TABLES exactly.
GRANT SELECT ON
    block,
    area,
    booth,
    booth_crosswalk,
    election,
    party,
    candidate,
    community,
    demography,
    caste_estimate,
    local_result,
    roll_revision,
    roll_snapshot,
    roll_change,
    mv_result_booth_party,
    mv_booth_party_share,
    mv_result_booth_wide,
    mv_swing,
    mv_transfer_ls_vs,
    mv_volatility,
    mv_new_voter_share,
    mv_floating_vote,
    mv_booth_priority,
    mv_area_rollup
TO giridih_ro;

-- Hard guard rails on the session itself.
ALTER ROLE giridih_ro SET statement_timeout = '5s';
ALTER ROLE giridih_ro SET idle_in_transaction_session_timeout = '10s';
ALTER ROLE giridih_ro SET default_transaction_read_only = on;
ALTER ROLE giridih_ro SET search_path = public;

-- Nothing created later is granted by default.
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM giridih_ro;
