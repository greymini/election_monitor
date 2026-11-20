-- field_0002: Telegram linking, idempotency and session tables (LLD §2.1; LLD migration 0019).
-- The job queue table from LLD 0019 is left for the worker slice.

-- One-time link tokens. Only the SHA-256 of the token is stored.
CREATE TABLE field.tg_link_token (
    token_hash text        PRIMARY KEY,
    member_id  uuid        NOT NULL REFERENCES field.member (member_id),
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    used_at    timestamptz NULL
);
CREATE INDEX tg_link_token_member_idx ON field.tg_link_token (member_id);

-- Deviation from LLD: surrogate key plus partial unique indexes instead of
-- telegram_user_id PK / member_id UNIQUE, so a revoked link keeps its history and the member
-- (or the Telegram account) can be linked again.
CREATE TABLE field.tg_link (
    link_id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    telegram_user_id bigint      NOT NULL,
    member_id        uuid        NOT NULL REFERENCES field.member (member_id),
    chat_id          bigint      NOT NULL,
    linked_at        timestamptz NOT NULL DEFAULT now(),
    revoked_at       timestamptz NULL,
    pin_hash         text        NULL
);
CREATE UNIQUE INDEX tg_link_active_telegram_user ON field.tg_link (telegram_user_id) WHERE revoked_at IS NULL;
CREATE UNIQUE INDEX tg_link_active_member ON field.tg_link (member_id) WHERE revoked_at IS NULL;

-- Every update is recorded by update_id; duplicates are ignored.
CREATE TABLE field.tg_update (
    update_id   bigint      PRIMARY KEY,
    received_at timestamptz NOT NULL DEFAULT now(),
    outcome     text        NULL
);

CREATE TABLE field.tg_session (
    chat_id    bigint      PRIMARY KEY,
    flow       text        NOT NULL,
    state      text        NOT NULL,
    payload    jsonb       NOT NULL DEFAULT '{}'::jsonb,
    updated_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL
);
CREATE INDEX tg_session_expires_idx ON field.tg_session (expires_at);
