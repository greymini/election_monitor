-- field_0007: job queue and job runs (LLD §8; CLAUDE.md: field.job_run instead of the core's job_run).
-- The queue is consumed with FOR UPDATE SKIP LOCKED; a job is marked running in the same transaction
-- that claims it, so two workers never take the same job. A running job whose lease has expired
-- (worker died) can be claimed again.

CREATE TABLE field.job (
    job_id       bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind         text        NOT NULL,
    payload      jsonb       NOT NULL DEFAULT '{}'::jsonb,
    status       text        NOT NULL DEFAULT 'queued' CHECK (status IN ('queued', 'running', 'done', 'failed')),
    run_after    timestamptz NOT NULL DEFAULT now(),
    attempts     integer     NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    max_attempts integer     NOT NULL DEFAULT 5 CHECK (max_attempts > 0),
    dedupe_key   text        NULL,
    locked_by    text        NULL,
    locked_at    timestamptz NULL,
    last_error   text        NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    finished_at  timestamptz NULL
);
CREATE INDEX job_ready_idx ON field.job (run_after, job_id) WHERE status = 'queued';
CREATE INDEX job_running_idx ON field.job (locked_at) WHERE status = 'running';
-- at most one waiting job per dedupe key (e.g. one card refresh per report)
CREATE UNIQUE INDEX job_dedupe_queued ON field.job (dedupe_key) WHERE status = 'queued' AND dedupe_key IS NOT NULL;

CREATE TABLE field.job_run (
    run_id      bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job_name    text        NOT NULL,
    worker_id   text        NOT NULL,
    job_id      bigint      NULL REFERENCES field.job (job_id),
    started_at  timestamptz NOT NULL,
    finished_at timestamptz NULL,
    status      text        NOT NULL CHECK (status IN ('ok', 'error', 'retry')),
    detail      jsonb       NULL
);
CREATE INDEX job_run_name_idx ON field.job_run (job_name, started_at DESC);
