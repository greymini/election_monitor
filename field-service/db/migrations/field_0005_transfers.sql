-- field_0005: advances, acknowledgement, returns, reminders and exports (slice 1c).

-- New members can record their own entries unless told otherwise (was 'none').
ALTER TABLE field.member ALTER COLUMN finance_scope SET DEFAULT 'own';

-- ack_overdue: an advance or return still unacknowledged after 48 hours. Kept as history.
ALTER TABLE field.money_txn DROP CONSTRAINT money_txn_flagged_check;
ALTER TABLE field.money_txn ADD CONSTRAINT money_txn_flagged_check
    CHECK (flagged <@ ARRAY['large', 'cash_over_limit', 'sensitive_in_window', 'backdated', 'ack_overdue']::text[]);

-- txn_audit also records exports (no single txn) and scheduled-job actions (no member actor).
ALTER TABLE field.txn_audit ALTER COLUMN txn_id DROP NOT NULL;
ALTER TABLE field.txn_audit ALTER COLUMN actor_member_id DROP NOT NULL;
ALTER TABLE field.txn_audit ADD COLUMN ac_id text NULL;
ALTER TABLE field.txn_audit DROP CONSTRAINT txn_audit_action_check;
ALTER TABLE field.txn_audit ADD CONSTRAINT txn_audit_action_check
    CHECK (action IN ('void', 'reclassify', 'ack', 'dispute', 'ceiling', 'export', 'remind', 'flag'));
ALTER TABLE field.txn_audit ADD CONSTRAINT txn_audit_txn_unless_export
    CHECK (txn_id IS NOT NULL OR (action = 'export' AND ac_id IS NOT NULL));
ALTER TABLE field.txn_audit ADD CONSTRAINT txn_audit_actor_unless_job
    CHECK (actor_member_id IS NOT NULL OR action IN ('remind', 'flag'));

CREATE INDEX money_txn_pending_idx ON field.money_txn (to_member_id) WHERE status = 'pending_ack';
CREATE INDEX money_txn_created_by_idx ON field.money_txn (created_by);
