-- 0019: review items that are not about one constituency
--
-- 0018 made review_queue.ac_id NOT NULL so the per-AC admin page could filter
-- on it. Three writers have no single AC to give, and their inserts then
-- failed:
--
--   * ocr_page      - a low-confidence OCR page, queued while a document is
--                     being extracted, before (or without) knowing its AC.
--                     ingest/ocr_tesseract.py swallowed the error, so the page
--                     went unreviewed and was loaded anyway.
--   * area_alias    - a place name from a news item (news/label_collect.py).
--   * roll_section  - a new roll supplement found on the CEO portal
--                     (worker/ops.check_new_supplement), which is portal-wide.
--
-- NULL now means "not about one constituency". The admin API lists these items
-- on every AC's review page and lets an admin resolve them from any of them.

ALTER TABLE review_queue ALTER COLUMN ac_id DROP NOT NULL;

COMMENT ON COLUMN review_queue.ac_id IS
    'The constituency the item is about. NULL for items that are not about one '
    '(an OCR page before its AC is known, a news place name, a portal-wide '
    'supplement); the admin API shows those on every AC.';
