-- field_0011: slice 4 register and the remaining backend items for the web screens.
--   * admin_audit records election-window, statutory-head and label-promotion changes
--   * report.title_source: a title derived from the note or transcript ('auto') never overrides
--     one a person typed ('person')
--   * exp_custom_label.review_listed_at: when the weekly label_review job last listed the label

ALTER TABLE field.admin_audit DROP CONSTRAINT admin_audit_action_check;
ALTER TABLE field.admin_audit ADD CONSTRAINT admin_audit_action_check
    CHECK (action IN ('pin_set', 'pin_changed', 'pin_locked', 'pin_unlocked', 'pin_reset', 'setting_changed',
                      'window_created', 'window_changed', 'window_deleted', 'statutory_head_set',
                      'label_promoted', 'keyword_added', 'keyword_removed'));

ALTER TABLE field.report ADD COLUMN title_source text NULL CHECK (title_source IN ('auto', 'person'));
UPDATE field.report SET title_source = 'person' WHERE title IS NOT NULL;   -- none expected; nothing set it before
ALTER TABLE field.report ADD CONSTRAINT report_title_source_pair CHECK ((title IS NULL) = (title_source IS NULL));

ALTER TABLE field.exp_custom_label ADD COLUMN review_listed_at timestamptz NULL;
