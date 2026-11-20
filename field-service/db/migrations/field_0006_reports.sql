-- field_0006: ground reports, media and their event log (LLD §3.4; LLD 0022).
-- Deviation (CLAUDE.md): field.report instead of altering the core's ground_report;
-- issue_id is a plain uuid with no foreign key. Category seeds (LLD §4.2) come from pulse/seed.py.

CREATE TABLE field.report_category (
    category_id     integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code            text    NOT NULL UNIQUE,
    name_hi         text    NOT NULL,
    name_en         text    NOT NULL,
    default_urgency text    NOT NULL DEFAULT 'normal' CHECK (default_urgency IN ('normal', 'urgent')),
    restricted      boolean NOT NULL DEFAULT false,   -- principal, coordinator and reporter only
    sort            integer NOT NULL DEFAULT 0,
    active          boolean NOT NULL DEFAULT true
);

-- keyword stored normalised (pulse/common/text.py); urgency_hint 'urgent' feeds auto-file urgency
CREATE TABLE field.report_keyword (
    keyword      text          NOT NULL CHECK (keyword <> ''),
    script       text          NOT NULL CHECK (script IN ('deva', 'latn')),
    category_id  integer       NOT NULL REFERENCES field.report_category (category_id),
    urgency_hint text          NULL CHECK (urgency_hint IN ('urgent')),
    weight       numeric(4, 2) NOT NULL DEFAULT 1 CHECK (weight > 0),
    PRIMARY KEY (keyword, category_id)
);

-- Per-AC counter for report_no (32-R-000087), same locking pattern as txn_counter.
CREATE TABLE field.report_counter (
    ac_id   text   PRIMARY KEY,
    last_no bigint NOT NULL CHECK (last_no > 0)
);

CREATE TABLE field.report (
    report_id          uuid          PRIMARY KEY DEFAULT gen_random_uuid(),
    report_no          text          NULL UNIQUE,              -- assigned at filing
    ac_id              text          NOT NULL,
    reporter_member_id uuid          NOT NULL REFERENCES field.member (member_id),
    block_id           text          NULL,
    area_id            text          NULL,
    booth_uid          text          NULL,                     -- core booth id, attached later on the web
    lat                numeric(9, 6) NULL CHECK (lat BETWEEN -90 AND 90),
    lon                numeric(9, 6) NULL CHECK (lon BETWEEN -180 AND 180),
    -- deviation: last_used and chosen added to LLD's shared | area_default | none
    loc_source         text          NOT NULL DEFAULT 'none'
                       CHECK (loc_source IN ('shared', 'area_default', 'last_used', 'chosen', 'none')),
    category_id        integer       NULL REFERENCES field.report_category (category_id),
    restricted         boolean       NOT NULL DEFAULT false,   -- copied from the category, for query-level filtering
    urgency            text          NOT NULL DEFAULT 'normal' CHECK (urgency IN ('normal', 'urgent')),
    title              text          NULL,
    note_text          text          NULL,                     -- typed text and captions, PII masked
    transcript_hi      text          NULL,
    transcript_status  text          NOT NULL DEFAULT 'none' CHECK (transcript_status IN ('none', 'queued', 'done', 'failed')),
    transcript_engine  text          NULL,
    transcript_conf    numeric(4, 3) NULL CHECK (transcript_conf BETWEEN 0 AND 1),
    summary            text          NULL,
    summary_source     text          NULL CHECK (summary_source IN ('human', 'llm')),
    status             text          NOT NULL DEFAULT 'draft'
                       CHECK (status IN ('draft', 'new', 'acknowledged', 'assigned', 'in_progress', 'resolved',
                                         'closed_no_action', 'duplicate')),
    assigned_to        uuid          NULL REFERENCES field.member (member_id),
    ack_by             uuid          NULL REFERENCES field.member (member_id),
    ack_at             timestamptz   NULL,
    resolved_at        timestamptz   NULL,
    resolution_note    text          NULL,
    duplicate_of       uuid          NULL REFERENCES field.report (report_id),
    issue_id           uuid          NULL,                     -- core public_issue, linked at merge
    occurred_at        timestamptz   NULL,
    created_via        text          NOT NULL CHECK (created_via IN ('telegram', 'web')),
    created_at         timestamptz   NOT NULL DEFAULT now(),
    filed_at           timestamptz   NULL,
    last_activity_at   timestamptz   NOT NULL DEFAULT now(),   -- auto-file reads this
    CONSTRAINT report_number_when_filed CHECK ((status = 'draft') = (report_no IS NULL)),
    CONSTRAINT report_filed_at_when_filed CHECK ((status = 'draft') = (filed_at IS NULL)),
    CONSTRAINT report_latlon_pair CHECK ((lat IS NULL) = (lon IS NULL)),
    CONSTRAINT report_shared_has_latlon CHECK (loc_source <> 'shared' OR lat IS NOT NULL)
);
CREATE INDEX report_ac_status_idx ON field.report (ac_id, status);
CREATE INDEX report_reporter_idx ON field.report (reporter_member_id, created_at DESC);
CREATE INDEX report_draft_activity_idx ON field.report (last_activity_at) WHERE status = 'draft';

-- Every received file. Written at receipt with Telegram's ids; sha256/storage_key once fetched.
CREATE TABLE field.media_asset (
    asset_id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    ac_id             text        NOT NULL,
    kind              text        NOT NULL CHECK (kind IN ('photo', 'voice', 'video', 'document')),
    tg_kind           text        NOT NULL CHECK (tg_kind IN ('photo', 'voice', 'audio', 'video', 'video_note', 'document')),
    status            text        NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'stored', 'failed')),
    sha256            text        NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    storage_key       text        NULL,
    bytes             bigint      NULL CHECK (bytes >= 0),     -- stored size (after metadata stripping)
    tg_file_size      bigint      NULL CHECK (tg_file_size >= 0),
    mime              text        NULL,
    duration_s        integer     NULL CHECK (duration_s >= 0),
    width             integer     NULL,
    height            integer     NULL,
    tg_file_id        text        NOT NULL,
    tg_file_unique_id text        NOT NULL,
    error             text        NULL,
    uploaded_by       uuid        NOT NULL REFERENCES field.member (member_id),
    created_at        timestamptz NOT NULL DEFAULT now(),
    stored_at         timestamptz NULL,
    delete_after      timestamptz NULL,
    CONSTRAINT media_asset_stored_fields CHECK ((status = 'stored') = (sha256 IS NOT NULL AND storage_key IS NOT NULL))
);
CREATE INDEX media_asset_sha_idx ON field.media_asset (sha256);
CREATE INDEX media_asset_unique_idx ON field.media_asset (tg_file_unique_id);

CREATE TABLE field.report_media (
    report_id uuid    NOT NULL REFERENCES field.report (report_id),
    asset_id  uuid    NOT NULL REFERENCES field.media_asset (asset_id),
    seq       integer NOT NULL CHECK (seq > 0),
    PRIMARY KEY (report_id, asset_id),
    UNIQUE (report_id, seq)
);

CREATE TABLE field.report_event (
    event_id        bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    report_id       uuid        NOT NULL REFERENCES field.report (report_id),
    at              timestamptz NOT NULL DEFAULT now(),
    actor_member_id uuid        NULL REFERENCES field.member (member_id),   -- NULL: the system (worker)
    kind            text        NOT NULL,
    note            text        NULL,
    data            jsonb       NULL
);
CREATE INDEX report_event_report_idx ON field.report_event (report_id, at);
