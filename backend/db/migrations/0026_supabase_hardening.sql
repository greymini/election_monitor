-- 0026: lock down public schema for Supabase PostgREST exposure.
-- The FastAPI app connects as postgres (table owner) and bypasses RLS.
-- giridih_ro retains SELECT on the chatbot allow-list.

-- Supabase defines anon/authenticated; vanilla Postgres (pgserver, local dev) does not.
DO $$
DECLARE
    r RECORD;
    api_role TEXT;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_roles WHERE rolname IN ('anon', 'authenticated')
    ) THEN
        RETURN;
    END IF;

    FOR api_role IN SELECT rolname FROM pg_roles WHERE rolname IN ('anon', 'authenticated')
    LOOP
        FOR r IN
            SELECT quote_ident(schemaname) AS sn, quote_ident(tablename) AS tn
            FROM pg_tables
            WHERE schemaname = 'public'
        LOOP
            EXECUTE format('REVOKE ALL ON TABLE %s.%s FROM %I', r.sn, r.tn, api_role);
        END LOOP;

        FOR r IN
            SELECT quote_ident(schemaname) AS sn, quote_ident(sequencename) AS tn
            FROM pg_sequences
            WHERE schemaname = 'public'
        LOOP
            EXECUTE format('REVOKE ALL ON SEQUENCE %s.%s FROM %I', r.sn, r.tn, api_role);
        END LOOP;

        FOR r IN
            SELECT quote_ident(schemaname) AS sn, quote_ident(matviewname) AS tn
            FROM pg_matviews
            WHERE schemaname = 'public'
        LOOP
            EXECUTE format('REVOKE ALL ON TABLE %s.%s FROM %I', r.sn, r.tn, api_role);
        END LOOP;
    END LOOP;
END
$$;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon')
       AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN
        ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM anon, authenticated;
        ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM anon, authenticated;
    END IF;
END
$$;

DO $$
DECLARE
    r RECORD;
BEGIN
    FOR r IN
        SELECT quote_ident(schemaname) AS sn, quote_ident(tablename) AS tn
        FROM pg_tables
        WHERE schemaname = 'public'
    LOOP
        EXECUTE format('ALTER TABLE %s.%s ENABLE ROW LEVEL SECURITY', r.sn, r.tn);
    END LOOP;
END
$$;

-- giridih_ro: read-only policies mirroring 0012 allow-list (extended in later migrations).
DO $$
DECLARE
    t TEXT;
    tables TEXT[] := ARRAY[
        'block', 'area', 'booth', 'booth_crosswalk', 'election', 'party', 'candidate',
        'community', 'demography', 'caste_estimate', 'local_result', 'roll_revision',
        'roll_snapshot', 'roll_change'
    ];
BEGIN
    FOREACH t IN ARRAY tables
    LOOP
        IF to_regclass('public.' || t) IS NOT NULL THEN
            EXECUTE format('DROP POLICY IF EXISTS giridih_ro_select ON %I', t);
            EXECUTE format(
                'CREATE POLICY giridih_ro_select ON %I FOR SELECT TO giridih_ro USING (true)',
                t
            );
        END IF;
    END LOOP;
END
$$;
