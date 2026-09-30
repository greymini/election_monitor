-- 0013: record where each source document's bytes actually live.
--
-- Until now source_doc held `filename` (a bare basename, directory deliberately
-- stripped) and `sha256`. The on-disk location was only reconstructible as
-- RAW_DIR/<kind>/<filename>, and only for files that arrived through
-- fetch_ceo; a PDF handed to a parser by an arbitrary path had no recoverable
-- location at all. That is workable while everything is one directory on one
-- host, and it stops working as soon as documents live in more than one place.
--
-- They now do. common/storage.py routes each document kind to its own backend:
-- published documents (Form 20, PS lists, SEC results) may live in
-- S3-compatible object storage so an analyst on a laptop can re-parse against a
-- remote database without first copying hundreds of megabytes of PDFs by hand,
-- while electoral rolls are refused any remote backend and stay on a host we
-- control. Which of those applied to a given document is a fact about that
-- document, not about today's configuration, so it is stored per row: a bucket
-- that is later renamed, or a STORAGE_BACKEND that is later changed, must not
-- silently repoint rows written under the old arrangement.
--
-- No transaction control here: db/apply_migrations.py wraps each file in one
-- transaction and commits on success.

ALTER TABLE source_doc
    ADD COLUMN storage_backend TEXT NOT NULL DEFAULT 'local'
        CHECK (storage_backend IN ('local', 's3')),
    ADD COLUMN storage_key     TEXT;

COMMENT ON COLUMN source_doc.storage_backend IS
    'Which common/storage.py backend holds the bytes. Roll kinds are constrained '
    'to local by roll_docs_stay_local below and by storage.assert_local_only.';

COMMENT ON COLUMN source_doc.storage_key IS
    'Backend-relative key, e.g. form20/2024-VS/32/giridih.pdf. Excludes S3_PREFIX '
    'so changing the prefix does not orphan existing rows.';

-- Backfill before the NOT NULL, so an existing deployment keeps working. Every
-- row that exists today was written by fetch_ceo into RAW_DIR/<kind>/<filename>
-- or registered by extract_pdf from a local path, so 'local' and kind/filename
-- reproduce where the file is.
UPDATE source_doc
   SET storage_key = kind || '/' || filename
 WHERE storage_key IS NULL;

ALTER TABLE source_doc
    ALTER COLUMN storage_key SET NOT NULL;

-- The compliance invariant, enforced by the database as well as by
-- common/storage.py. Defence in depth: the Python guard protects the write path
-- we know about, this protects the table against a future loader, an admin
-- endpoint or a hand-written UPDATE that forgets. An electoral roll carries
-- names, EPIC numbers, relatives' names, house numbers and ages, and must not
-- be recorded as living in third-party object storage (LLD 12, audit C3/C13).
ALTER TABLE source_doc
    ADD CONSTRAINT roll_docs_stay_local
        CHECK (kind NOT IN ('roll_mother', 'roll_supplement') OR storage_backend = 'local');

-- One document per backend+key. sha256 is already UNIQUE and remains the
-- identity used for dedupe; this catches two different documents being written
-- to the same key, which would mean one silently overwrote the other.
CREATE UNIQUE INDEX source_doc_storage_location ON source_doc (storage_backend, storage_key);

-- The parse_status lifecycle gains the two states spec 5.7 requires. 'validated'
-- sits between parsed and loaded: a document whose numbers reconciled but which
-- has not been promoted. 'drifted' is set when a portal's page structure no
-- longer matches its recorded fingerprint, so the document must not be parsed
-- on the old assumptions (Track B, but the state belongs with the column).
ALTER TABLE source_doc
    DROP CONSTRAINT IF EXISTS source_doc_parse_status_check;

ALTER TABLE source_doc
    ADD CONSTRAINT source_doc_parse_status_check
        CHECK (parse_status IN ('new', 'extracted', 'parsed', 'validated', 'loaded',
                                'failed', 'drifted'));

-- Who moved it and when. The audit found parse_status never advanced past
-- 'extracted' and parsed_at was never set, so /admin/sources could not answer
-- "has this PDF been loaded?" (B10). The columns to answer it exist now; the
-- parsers are wired to them separately.
ALTER TABLE source_doc
    ADD COLUMN status_changed_at TIMESTAMPTZ,
    ADD COLUMN status_changed_by TEXT;

COMMENT ON COLUMN source_doc.status_changed_by IS
    'The actor that last moved parse_status: a CLI module name, a job name, or a '
    'user id. Free text on purpose - it spans CLI, scheduler and API.';
