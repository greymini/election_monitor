-- 0008: users, LLM accounting, review queue, job runs, source-document audit

CREATE TABLE app_user (
    user_id            SERIAL PRIMARY KEY,
    phone              TEXT UNIQUE,
    email              TEXT UNIQUE,
    name               TEXT NOT NULL,
    password_hash      TEXT,
    role               TEXT NOT NULL CHECK (role IN ('admin', 'strategist', 'block')),
    block_id           SMALLINT REFERENCES block(block_id),
    daily_token_budget INT NOT NULL DEFAULT 150000,
    is_active          BOOLEAN NOT NULL DEFAULT true,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at      TIMESTAMPTZ,
    CONSTRAINT block_user_needs_block CHECK (role <> 'block' OR block_id IS NOT NULL)
);

CREATE TABLE auth_otp (
    otp_id     SERIAL PRIMARY KEY,
    phone      TEXT NOT NULL,
    code_hash  TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    used_at    TIMESTAMPTZ,
    attempts   SMALLINT NOT NULL DEFAULT 0
);
CREATE INDEX auth_otp_phone_idx ON auth_otp (phone, expires_at DESC);

CREATE TABLE llm_usage (
    id                 BIGSERIAL PRIMARY KEY,
    ts                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    user_id            INT REFERENCES app_user(user_id),
    model              TEXT NOT NULL,
    purpose            TEXT NOT NULL,
    input_tokens       INT NOT NULL DEFAULT 0,
    cached_tokens      INT NOT NULL DEFAULT 0,
    cache_write_tokens INT NOT NULL DEFAULT 0,
    output_tokens      INT NOT NULL DEFAULT 0,
    cost_usd           NUMERIC(10, 6) NOT NULL DEFAULT 0,
    is_batch           BOOLEAN NOT NULL DEFAULT false,
    request_id         TEXT
);
CREATE INDEX llm_usage_ts_idx       ON llm_usage (ts DESC);
CREATE INDEX llm_usage_user_day_idx ON llm_usage (user_id, ts);

CREATE TABLE llm_prompt_log (
    id       BIGSERIAL PRIMARY KEY,
    usage_id BIGINT REFERENCES llm_usage(id) ON DELETE CASCADE,
    ts       TIMESTAMPTZ NOT NULL DEFAULT now(),
    role     TEXT,
    content  TEXT
);
CREATE INDEX llm_prompt_log_ts_idx ON llm_prompt_log (ts);

CREATE TABLE review_queue (
    id          SERIAL PRIMARY KEY,
    kind        TEXT NOT NULL CHECK (kind IN ('ocr_page', 'form20_row', 'crosswalk', 'roll_section', 'geocode', 'area_alias', 'local_tag')),
    ref         TEXT NOT NULL,
    payload     JSONB,
    status      TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'resolved', 'rejected')),
    note        TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at TIMESTAMPTZ,
    resolved_by INT REFERENCES app_user(user_id)
);
CREATE INDEX review_queue_open_idx ON review_queue (kind, status) WHERE status = 'open';

CREATE TABLE job_run (
    id       SERIAL PRIMARY KEY,
    job      TEXT NOT NULL,
    started  TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished TIMESTAMPTZ,
    status   TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'ok', 'failed', 'skipped')),
    log      TEXT,
    meta     JSONB
);
CREATE INDEX job_run_job_idx ON job_run (job, started DESC);

CREATE TABLE source_doc (
    doc_id       SERIAL PRIMARY KEY,
    kind         TEXT NOT NULL CHECK (kind IN ('form20', 'roll_mother', 'roll_supplement', 'ps_list', 'sec_result', 'census', 'other')),
    url          TEXT,
    filename     TEXT NOT NULL,
    sha256       TEXT NOT NULL UNIQUE,
    bytes        BIGINT,
    fetched_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    parsed_at    TIMESTAMPTZ,
    parse_status TEXT NOT NULL DEFAULT 'new' CHECK (parse_status IN ('new', 'extracted', 'parsed', 'loaded', 'failed')),
    pages        INT,
    ocr_pages    INT,
    election_id  INT REFERENCES election(election_id),
    revision_id  INT REFERENCES roll_revision(revision_id),
    note         TEXT
);
