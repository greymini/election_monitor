-- 0027: username for single-user deployment login.

ALTER TABLE app_user ADD COLUMN IF NOT EXISTS username TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS app_user_username_idx ON app_user (username) WHERE username IS NOT NULL;

COMMENT ON COLUMN app_user.username IS 'Login user id when AUTH uses APP_USERNAME; phone remains optional legacy.';
