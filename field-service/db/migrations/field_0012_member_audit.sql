-- field_0012: admin_audit records team changes made through the API: a member created (one row per
-- member, also for each CSV-imported row), a member changed (the fields that changed), and a CSV import
-- (one row per import, with the count). Names are not copied into the audit data.

ALTER TABLE field.admin_audit DROP CONSTRAINT admin_audit_action_check;
ALTER TABLE field.admin_audit ADD CONSTRAINT admin_audit_action_check
    CHECK (action IN ('pin_set', 'pin_changed', 'pin_locked', 'pin_unlocked', 'pin_reset', 'setting_changed',
                      'window_created', 'window_changed', 'window_deleted', 'statutory_head_set',
                      'label_promoted', 'keyword_added', 'keyword_removed',
                      'member_created', 'member_changed', 'members_imported'));
