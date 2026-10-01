-- 0018: review-queue kinds and scoping, and the caste columns the API reads
--
-- Two findings, both from running the loaders against a database for the first
-- time (N17, N19).
--
-- 1. `form20_column`. Master prompt 3.1 step 6 says an unresolved Form 20
--    column aborts the load and goes to the review queue carrying the raw
--    header and the top three guesses. `ingest/parse_form20.queue_unresolved`
--    writes exactly that - with `kind = 'form20_column'`, which the CHECK
--    constraint in 0008 does not allow. So the failure path of the fix for C1,
--    the audit's most consequential defect, would itself have failed: the
--    insert raises, the handler logs "could not write to review_queue", and the
--    operator is told a column did not resolve with no record of which or why.
--
--    It was never hit because every column in every generated document
--    resolves. That is exactly the kind of path a dev stack does not exercise,
--    which is why `tests/test_resolve.py` and the contract test both have to.
--
-- 2. `ac_id NOT NULL`. 0014 added the column to `review_queue` and left it
--    nullable, and `queue_errors` never set it. `GET /acs/{ac}/admin/review-queue`
--    filters on `ac_id`, so every Form 20 row rejected by the LLD 4.2
--    arithmetic gate landed in a queue the Admin page could not show. A
--    rejected load that reports nothing to review is worse than one that fails
--    loudly: the operator sees "NOT LOADING" and an empty queue.
--
--    Existing NULLs are assigned to Giridih, which is the only constituency any
--    deployment of this system has ever loaded. If that is ever untrue the
--    UPDATE below is the place it would be wrong, so it reports what it did.

-- 1. The kinds. Rewritten whole rather than extended, so the list in the
--    schema is the list, and `kind` values no writer produces are not implied
--    to be supported. `local_tag` and `geocode` have no writer yet and are
--    kept: `ingest/geocode.py` and the news labeller are both specified to use
--    them.
ALTER TABLE review_queue DROP CONSTRAINT IF EXISTS review_queue_kind_check;
ALTER TABLE review_queue
    ADD CONSTRAINT review_queue_kind_check CHECK (kind IN (
        'ocr_page',        -- a page whose OCR confidence is below the floor
        'form20_row',      -- a row whose arithmetic does not reconcile (LLD 4.2)
        'form20_column',   -- a header column that resolved to no candidate (3.1 step 6)
        'crosswalk',       -- a station matched between the review floor and auto-accept
        'roll_section',    -- a roll section whose PS number has no booth
        'geocode',         -- a booth that could not be placed
        'area_alias',      -- a printed area name that could not be placed in a block
        'local_tag'        -- a panchayat/ULB result whose party affiliation needs a human
    ));

-- 2. ac_id. Backfill, then enforce.
DO $$
DECLARE
    orphans  BIGINT;
    giridih  INT;
BEGIN
    SELECT COUNT(*) INTO orphans FROM review_queue WHERE ac_id IS NULL;
    IF orphans = 0 THEN
        RAISE NOTICE 'review_queue: no rows without an ac_id';
    ELSE
        SELECT ac_id INTO giridih FROM ac WHERE ac_number = 32;
        IF giridih IS NULL THEN
            RAISE EXCEPTION
                'review_queue has % row(s) with no ac_id and AC 32 is not seeded, so '
                'they cannot be assigned. Set ac_id by hand and re-run.', orphans;
        END IF;
        UPDATE review_queue SET ac_id = giridih WHERE ac_id IS NULL;
        RAISE NOTICE 'review_queue: % row(s) without an ac_id assigned to AC 32', orphans;
    END IF;
END
$$;

ALTER TABLE review_queue ALTER COLUMN ac_id SET NOT NULL;

COMMENT ON COLUMN review_queue.ac_id IS
    'Which constituency the item belongs to. NOT NULL since 0018: the admin route filters on it, so a NULL made the item invisible rather than global (N19).';

-- ---------------------------------------------------------------------------
-- 3. caste_estimate.matched_pct and .method_version (N21, N22)
--
-- `api/routers/data.py` has always selected both, and neither existed, so
-- GET /acs/{ac}/caste returned 500 on every call - the Caste page could never
-- have loaded.
--
-- They are not decoration. C14's fix is that a surname estimate is a share of
-- the *electorate*, not of the names the 176-entry dictionary happened to
-- recognise, with the shortfall left explicitly unallocated. `matched_pct`
-- is how much the dictionary placed, so a reader can see that a booth at 38%
-- coverage is 38% attributed and 62% unknown rather than a complete picture.
-- `method_version` says which rule produced the row, because a table holding a
-- mixture of pre- and post-C14 estimates with nothing to tell them apart is how
-- that defect stayed invisible.
-- ---------------------------------------------------------------------------

ALTER TABLE caste_estimate ADD COLUMN IF NOT EXISTS matched_pct REAL
    CHECK (matched_pct IS NULL OR matched_pct BETWEEN 0 AND 100);
ALTER TABLE caste_estimate ADD COLUMN IF NOT EXISTS method_version TEXT;

COMMENT ON COLUMN caste_estimate.matched_pct IS
    'Share of this booth electorate the surname dictionary could place, 0-100. NULL for sources where it does not apply. The complement is the UNMATCHED row (C14).';
COMMENT ON COLUMN caste_estimate.method_version IS
    'Which rule produced this row: surname-1 normalised over matched tokens (pre-C14), surname-2 shares the electorate with an explicit residual.';
