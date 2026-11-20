-- field_0009: hardening (slice 2c). PIN state, per-AC settings, admin audit, bot message ledger for
-- chat cleanup, media purge, and no message bodies in the outbox.

-- PIN (LLD §11). The hash lives on the active link (a new phone needs a new PIN); the lock and the
-- failure count live on the member, so relinking does not clear them. All PIN prompt state is
-- here, never in tg_session.
ALTER TABLE field.member ADD COLUMN pin_failures smallint NOT NULL DEFAULT 0 CHECK (pin_failures >= 0);
ALTER TABLE field.member ADD COLUMN pin_locked_at timestamptz NULL;
ALTER TABLE field.tg_link ADD COLUMN pin_set_at timestamptz NULL;
ALTER TABLE field.tg_link ADD COLUMN pin_stage text NULL
    CHECK (pin_stage IN ('verify_old', 'new', 'confirm', 'action'));
ALTER TABLE field.tg_link ADD COLUMN pin_stage_expires_at timestamptz NULL;
ALTER TABLE field.tg_link ADD COLUMN pin_pending_hash text NULL;         -- first entry while asking for the second
ALTER TABLE field.tg_link ADD CONSTRAINT tg_link_pin_stage_expiry
    CHECK ((pin_stage IS NULL) = (pin_stage_expires_at IS NULL));

-- Per-AC overrides of the environment defaults; read only through pulse/settings_ac.py.
CREATE TABLE field.ac_setting (
    ac_id      text        NOT NULL,
    key        text        NOT NULL CHECK (key IN ('large_entry', 'tail_threshold', 'pin_limit', 'autofile_minutes',
                                                   'ack_remind_hours', 'ack_flag_hours', 'media_retention_days',
                                                   'void_retention_days', 'chat_cleanup_hours')),
    value      text        NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    updated_by text        NOT NULL,                 -- core user id of the admin
    PRIMARY KEY (ac_id, key)
);

-- Administrative actions: PIN lock / unlock / reset, settings changes.
CREATE TABLE field.admin_audit (
    audit_id         bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ac_id            text        NOT NULL,
    at               timestamptz NOT NULL DEFAULT now(),
    actor_user_id    text        NULL,               -- core user (API); NULL when the system acted
    actor_member_id  uuid        NULL REFERENCES field.member (member_id),
    action           text        NOT NULL CHECK (action IN ('pin_set', 'pin_changed', 'pin_locked', 'pin_unlocked',
                                                            'pin_reset', 'setting_changed')),
    target_member_id uuid        NULL REFERENCES field.member (member_id),
    data             jsonb       NULL                -- never a PIN or its hash
);
CREATE INDEX admin_audit_ac_idx ON field.admin_audit (ac_id, at DESC);

-- Messages the bot sent directly (not through the outbox): ids only, never text, so chat_cleanup
-- can delete money cards later.
CREATE TABLE field.bot_message (
    chat_id      bigint      NOT NULL,
    message_id   bigint      NOT NULL,
    purpose      text        NOT NULL CHECK (purpose IN ('money', 'report_card', 'menu', 'notice')),
    report_id    uuid        NULL REFERENCES field.report (report_id),
    txn_id       uuid        NULL REFERENCES field.money_txn (txn_id),
    sent_at      timestamptz NOT NULL DEFAULT now(),
    deleted_at   timestamptz NULL,
    delete_error text        NULL,
    PRIMARY KEY (chat_id, message_id)
);
CREATE INDEX bot_message_cleanup_idx ON field.bot_message (sent_at) WHERE deleted_at IS NULL AND delete_error IS NULL;
ALTER TABLE field.alert_message ADD COLUMN deleted_at timestamptz NULL;

-- Media retention: a purged asset keeps its row (and sha256) but not its file.
ALTER TABLE field.media_asset DROP CONSTRAINT media_asset_status_check;
ALTER TABLE field.media_asset ADD CONSTRAINT media_asset_status_check
    CHECK (status IN ('pending', 'stored', 'failed', 'purged'));
ALTER TABLE field.media_asset ADD COLUMN purged_at timestamptz NULL;

-- The outbox stores no message bodies: only a template name and ids or codes. Existing text bodies
-- are blanked; any still waiting are not sent.
UPDATE field.alert_message SET status = 'skipped', last_error = 'body redacted in field_0009'
    WHERE status = 'queued' AND body ? 'text';
UPDATE field.alert_message SET body = jsonb_build_object('render', 'redacted') WHERE body ? 'text';
ALTER TABLE field.alert_message ADD CONSTRAINT alert_message_no_text
    CHECK (NOT (body ? 'text') AND NOT (body ? 'markup') AND body ? 'render');
UPDATE field.job SET payload = payload - 'text' - 'markup',
                     status = CASE WHEN status = 'queued' THEN 'done' ELSE status END,
                     last_error = 'payload redacted in field_0009'
    WHERE kind = 'tg_edit_raw' AND (payload ? 'text' OR payload ? 'markup');
