-- field_0008: triage, alerts and the outbox (LLD §5.3 lifecycle, §5.4; CLAUDE.md: field.alert_rule
-- instead of the core's alert_rule). Rule seeds come from pulse/seed.py (db/seeds/alert_rules.json).

ALTER TABLE field.member ADD COLUMN is_coordinator boolean NOT NULL DEFAULT false;   -- any number per AC

ALTER TABLE field.report ADD COLUMN escalation_level smallint NOT NULL DEFAULT 0 CHECK (escalation_level BETWEEN 0 AND 2);
ALTER TABLE field.report ADD COLUMN escalated_at timestamptz NULL;     -- set after the last escalation round
CREATE INDEX report_unacked_urgent_idx ON field.report (filed_at) WHERE status = 'new' AND urgency = 'urgent';

-- closing photos are kept apart from what the reporter sent
ALTER TABLE field.report_media ADD COLUMN role text NOT NULL DEFAULT 'report' CHECK (role IN ('report', 'closing'));

-- Who is told what, and when (LLD §5.4): data, not code.
CREATE TABLE field.alert_rule (
    rule_id       integer     GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code          text        NOT NULL UNIQUE,
    trigger       text        NOT NULL CHECK (trigger IN ('report_filed', 'report_unacked', 'report_unsorted',
                                                          'daily_digest', 'balance_idle')),
    condition     jsonb       NOT NULL DEFAULT '{}'::jsonb,    -- e.g. {"urgency": "urgent", "restricted": false}
    priority      integer     NOT NULL DEFAULT 100,            -- lower wins when several report_filed rules match
    recipients    text[]      NOT NULL,
    fallback      text[]      NOT NULL DEFAULT '{}',           -- used when recipients resolve to nobody
    after_minutes integer     NULL CHECK (after_minutes > 0),
    at_local_time time        NULL,                            -- Asia/Kolkata
    include_media boolean     NOT NULL DEFAULT true,
    active        boolean     NOT NULL DEFAULT true,
    description   text        NULL,
    CONSTRAINT alert_rule_recipient_roles CHECK (
        recipients <@ ARRAY['principal', 'coordinators', 'reporter_parent', 'next_level_up', 'finance_all', 'self']::text[]
        AND fallback <@ ARRAY['principal', 'coordinators', 'reporter_parent', 'next_level_up', 'finance_all', 'self']::text[])
);

-- Every message sent through the job queue (alerts and every other notification), so cards can be
-- edited later (first acknowledgement wins) and undelivered messages are on record.
CREATE TABLE field.alert_message (
    alert_id            bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ac_id               text        NOT NULL,
    recipient_member_id uuid        NOT NULL REFERENCES field.member (member_id),
    kind                text        NOT NULL CHECK (kind IN ('alert', 'escalation', 'unsorted', 'assignee', 'feedback',
                                                             'digest', 'nudge', 'ack_request', 'notice')),
    rule_code           text        NULL,
    report_id           uuid        NULL REFERENCES field.report (report_id),
    txn_id              uuid        NULL REFERENCES field.money_txn (txn_id),
    body                jsonb       NOT NULL,       -- {"text", "markup"} or {"render": "report_card", ...}
    status              text        NOT NULL DEFAULT 'queued'
                        CHECK (status IN ('queued', 'sent', 'no_link', 'blocked', 'failed', 'skipped')),
    chat_id             bigint      NULL,
    message_id          bigint      NULL,
    is_media            boolean     NOT NULL DEFAULT false,   -- sent as a photo: edit the caption, not the text
    local_date          date        NULL,                     -- Asia/Kolkata day, for once-a-day messages
    job_id              bigint      NULL REFERENCES field.job (job_id),
    created_at          timestamptz NOT NULL DEFAULT now(),
    sent_at             timestamptz NULL,
    last_error          text        NULL
);
CREATE INDEX alert_message_report_idx ON field.alert_message (report_id) WHERE report_id IS NOT NULL;
CREATE INDEX alert_message_chat_msg_idx ON field.alert_message (chat_id, message_id);
CREATE INDEX alert_message_recipient_kind_idx ON field.alert_message (recipient_member_id, kind, created_at DESC);

-- Possible duplicates found when a report is filed or categorised (same area and category, 24 h).
CREATE TABLE field.report_duplicate (
    report_id    uuid        NOT NULL REFERENCES field.report (report_id),
    suggested_id uuid        NOT NULL REFERENCES field.report (report_id),
    reason       text        NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (report_id, suggested_id),
    CONSTRAINT report_duplicate_not_self CHECK (report_id <> suggested_id)
);
