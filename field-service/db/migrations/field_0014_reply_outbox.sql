-- field_0014: bot replies that could not be sent when the member acted (pulse/bot/outgoing.py).
-- The user's input is committed first; a reply Telegram refused for now (429, network, 5xx) is queued
-- here as kind 'reply' and drawn from the database when it is finally sent, saying it was already saved.
-- Like every outbox row, the body holds only a template name and ids (alert_message_no_text).

ALTER TABLE field.alert_message DROP CONSTRAINT alert_message_kind_check;
ALTER TABLE field.alert_message ADD CONSTRAINT alert_message_kind_check
    CHECK (kind IN ('alert', 'escalation', 'unsorted', 'assignee', 'feedback', 'digest', 'nudge', 'ack_request',
                    'notice', 'followup', 'digest_weekly', 'reply'));

-- a chat's replies go out in order: "is an earlier reply of this member still waiting?"
CREATE INDEX alert_message_reply_queued_idx ON field.alert_message (recipient_member_id, alert_id)
    WHERE kind = 'reply' AND status = 'queued';
