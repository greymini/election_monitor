-- field_0013: idempotency keys for web writes that create something (pulse/api/idempotency.py).
-- One row per (user, route, key). The response of the first request is kept for 48 hours so a repeat
-- (a retry after a timeout, a double submit) returns it instead of creating a second row.

CREATE TABLE field.idempotency_key (
    user_id      text        NOT NULL,          -- core user who sent it
    route        text        NOT NULL,          -- e.g. 'POST /txns'
    key          text        NOT NULL CHECK (length(key) BETWEEN 1 AND 100),
    request_hash text        NOT NULL,          -- sha256 of the request body: a reused key must match
    status_code  int         NULL,
    response     jsonb       NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, route, key)
);
CREATE INDEX idempotency_key_created_idx ON field.idempotency_key (created_at);
