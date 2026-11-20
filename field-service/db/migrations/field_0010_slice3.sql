-- field_0010: slice 3. Transcripts (machine and human), issues (local stand-in for the core's
-- public_issue), impact follow-ups, last-seen for team activity, the weekly digest.

-- Transcripts (LLD §6). One row per voice or audio note of a report; field.report.transcript_hi
-- holds the done ones joined in order. The machine text is PII-masked before it is stored. A human
-- correction is kept apart and preferred for display. A category or urgency read from the
-- transcript is only a suggestion: it never changes the report by itself.
CREATE TABLE field.report_transcript (
    report_id  uuid          NOT NULL REFERENCES field.report (report_id),
    asset_id   uuid          NOT NULL REFERENCES field.media_asset (asset_id),
    status     text          NOT NULL CHECK (status IN ('queued', 'done', 'failed')),
    text       text          NULL,
    confidence numeric(4, 3) NULL CHECK (confidence BETWEEN 0 AND 1),
    engine     text          NULL,
    converted  boolean       NULL,              -- 16 kHz mono conversion ran (false: original passed)
    error      text          NULL,
    created_at timestamptz   NOT NULL DEFAULT now(),
    done_at    timestamptz   NULL,
    PRIMARY KEY (report_id, asset_id)
);
ALTER TABLE field.report ADD COLUMN transcript_human    text        NULL;
ALTER TABLE field.report ADD COLUMN transcript_human_by uuid        NULL REFERENCES field.member (member_id);
ALTER TABLE field.report ADD COLUMN transcript_human_at timestamptz NULL;
ALTER TABLE field.report ADD COLUMN suggested_category_id integer NULL REFERENCES field.report_category (category_id);
ALTER TABLE field.report ADD COLUMN suggested_urgency     text    NULL CHECK (suggested_urgency = 'urgent');
ALTER TABLE field.report ADD COLUMN suggested_source      text    NULL CHECK (suggested_source = 'transcript_rule');

-- Issues: the long-running thing ("water supply in panchayat A1"); a report is one observation of
-- it. Stand-in for the core's public_issue / issue_event; report.issue_id and money_txn.issue_id
-- stay plain uuids (no foreign key) as before, validated by pulse/reports/issues.py.
CREATE TABLE field.issue_counter (
    ac_id   text    PRIMARY KEY,
    last_no integer NOT NULL
);
CREATE TABLE field.issue (
    issue_id    uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    issue_no    text        NOT NULL UNIQUE,                 -- e.g. 32-I-000004
    ac_id       text        NOT NULL,
    title       text        NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
    category_id integer     NULL REFERENCES field.report_category (category_id),
    block_id    text        NULL,
    area_id     text        NOT NULL,
    status      text        NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'closed')),
    opened_by   uuid        NOT NULL REFERENCES field.member (member_id),
    created_at  timestamptz NOT NULL DEFAULT now(),
    closed_at   timestamptz NULL,
    closed_by   uuid        NULL REFERENCES field.member (member_id),
    close_note  text        NULL,
    CONSTRAINT issue_closed_fields CHECK ((status = 'closed') = (closed_at IS NOT NULL AND closed_by IS NOT NULL))
);
CREATE INDEX issue_ac_area_idx ON field.issue (ac_id, area_id, status);
CREATE TABLE field.issue_event (
    event_id        bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    issue_id        uuid        NOT NULL REFERENCES field.issue (issue_id),
    at              timestamptz NOT NULL DEFAULT now(),
    actor_member_id uuid        NULL REFERENCES field.member (member_id),
    kind            text        NOT NULL CHECK (kind IN ('opened', 'renamed', 'report_linked', 'report_unlinked', 'closed')),
    report_id       uuid        NULL REFERENCES field.report (report_id),
    note            text        NULL,
    data            jsonb       NULL
);
CREATE INDEX issue_event_issue_idx ON field.issue_event (issue_id, at);
CREATE INDEX report_issue_idx ON field.report (issue_id) WHERE issue_id IS NOT NULL;
CREATE INDEX money_txn_report_idx ON field.money_txn (report_id) WHERE report_id IS NOT NULL;
CREATE INDEX money_txn_issue_idx ON field.money_txn (issue_id) WHERE issue_id IS NOT NULL;

-- Impact follow-up (LLD §7): for ASSIST and WORKS expenses, ask the member who entered it whether
-- the work was done. One ask, one reminder; an unanswered follow-up stays NULL (never "no").
CREATE TABLE field.txn_followup (
    txn_id         uuid        PRIMARY KEY REFERENCES field.money_txn (txn_id),
    ac_id          text        NOT NULL,
    member_id      uuid        NOT NULL REFERENCES field.member (member_id),
    due_at         timestamptz NOT NULL,
    asked_at       timestamptz NULL,
    reminded_at    timestamptz NULL,
    answer         text        NULL CHECK (answer IN ('done', 'partial', 'not_done')),
    answered_at    timestamptz NULL,
    photo_asset_id uuid        NULL REFERENCES field.media_asset (asset_id),
    cancelled_at   timestamptz NULL,                 -- the entry was voided or moved out of ASSIST / WORKS
    created_at     timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT txn_followup_answer_pair CHECK ((answer IS NULL) = (answered_at IS NULL)),
    CONSTRAINT txn_followup_reminded_after_ask CHECK (reminded_at IS NULL OR asked_at IS NOT NULL)
);
CREATE INDEX txn_followup_due_idx ON field.txn_followup (due_at) WHERE cancelled_at IS NULL AND answer IS NULL;

-- Team activity: when a linked member last sent the bot anything.
ALTER TABLE field.tg_link ADD COLUMN last_seen_at timestamptz NULL;

-- New outbox kinds, alert trigger and per-AC setting.
ALTER TABLE field.alert_message DROP CONSTRAINT alert_message_kind_check;
ALTER TABLE field.alert_message ADD CONSTRAINT alert_message_kind_check
    CHECK (kind IN ('alert', 'escalation', 'unsorted', 'assignee', 'feedback', 'digest', 'nudge', 'ack_request',
                    'notice', 'followup', 'digest_weekly'));
ALTER TABLE field.alert_rule DROP CONSTRAINT alert_rule_trigger_check;
ALTER TABLE field.alert_rule ADD CONSTRAINT alert_rule_trigger_check
    CHECK (trigger IN ('report_filed', 'report_unacked', 'report_unsorted', 'daily_digest', 'weekly_digest',
                       'balance_idle'));
ALTER TABLE field.ac_setting DROP CONSTRAINT ac_setting_key_check;
ALTER TABLE field.ac_setting ADD CONSTRAINT ac_setting_key_check
    CHECK (key IN ('large_entry', 'tail_threshold', 'pin_limit', 'autofile_minutes', 'ack_remind_hours',
                   'ack_flag_hours', 'media_retention_days', 'void_retention_days', 'chat_cleanup_hours',
                   'followup_days'));
