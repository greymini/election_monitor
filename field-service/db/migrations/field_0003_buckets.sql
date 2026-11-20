-- field_0003: expense buckets, keywords, custom labels, election windows (LLD §3.1, §3.2; LLD 0020).
-- Seed rows (LLD §4.1) are applied by pulse/seed.py after migrations, idempotently.

CREATE TABLE field.exp_bucket (
    bucket_id      integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ac_id          text    NULL,                      -- NULL = shared preset
    parent_id      integer NULL REFERENCES field.exp_bucket (bucket_id),
    code           text    NOT NULL UNIQUE,
    name_hi        text    NOT NULL,
    name_en        text    NOT NULL,
    statutory_head text    NULL,                      -- set by the election agent; seeds leave it NULL
    sensitive      boolean NOT NULL DEFAULT false,    -- warns in election window
    ask_reach      boolean NOT NULL DEFAULT false,    -- prompt for people reached
    active         boolean NOT NULL DEFAULT true,
    sort           integer NOT NULL DEFAULT 0,
    CONSTRAINT exp_bucket_not_own_parent CHECK (parent_id IS DISTINCT FROM bucket_id)
);
CREATE INDEX exp_bucket_parent_idx ON field.exp_bucket (parent_id);

-- two levels only: a parent must itself be top-level, and a bucket with children cannot get a parent
CREATE FUNCTION field.exp_bucket_two_levels() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.parent_id IS NOT NULL THEN
        IF EXISTS (SELECT 1 FROM field.exp_bucket WHERE bucket_id = NEW.parent_id AND parent_id IS NOT NULL) THEN
            RAISE EXCEPTION 'exp_bucket: only two levels allowed (parent % is itself a sub-head)', NEW.parent_id
                USING ERRCODE = 'check_violation';
        END IF;
        IF EXISTS (SELECT 1 FROM field.exp_bucket WHERE parent_id = NEW.bucket_id) THEN
            RAISE EXCEPTION 'exp_bucket: bucket % has sub-heads and cannot become one', NEW.bucket_id
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER exp_bucket_two_levels BEFORE INSERT OR UPDATE OF parent_id ON field.exp_bucket
    FOR EACH ROW EXECUTE FUNCTION field.exp_bucket_two_levels();

-- keyword is stored normalised (pulse/common/text.py); script = deva | latn
CREATE TABLE field.exp_keyword (
    keyword   text          NOT NULL CHECK (keyword <> ''),
    script    text          NOT NULL CHECK (script IN ('deva', 'latn')),
    bucket_id integer       NOT NULL REFERENCES field.exp_bucket (bucket_id),
    weight    numeric(4, 2) NOT NULL DEFAULT 1 CHECK (weight > 0),
    PRIMARY KEY (keyword, bucket_id)
);

CREATE TABLE field.exp_custom_label (
    ac_id              text        NOT NULL,
    label_norm         text        NOT NULL CHECK (label_norm <> ''),
    count              integer     NOT NULL DEFAULT 1 CHECK (count > 0),
    last_seen          timestamptz NOT NULL DEFAULT now(),
    promoted_bucket_id integer     NULL REFERENCES field.exp_bucket (bucket_id),
    PRIMARY KEY (ac_id, label_norm)
);

CREATE TABLE field.ac_election_window (
    window_id      integer        GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ac_id          text           NOT NULL,
    event_id       text           NULL,               -- core election_event id, validated at merge
    starts_on      date           NOT NULL,
    ends_on        date           NOT NULL,
    ceiling_amount numeric(12, 2) NULL CHECK (ceiling_amount > 0),
    cash_limit     numeric(12, 2) NULL CHECK (cash_limit > 0),
    CONSTRAINT ac_election_window_dates CHECK (ends_on >= starts_on)
);
CREATE INDEX ac_election_window_ac_idx ON field.ac_election_window (ac_id, starts_on);

-- windows of one AC must not overlap (a trigger avoids needing the btree_gist extension)
CREATE FUNCTION field.ac_election_window_no_overlap() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('field.ac_election_window:' || NEW.ac_id));
    IF EXISTS (SELECT 1 FROM field.ac_election_window w
               WHERE w.ac_id = NEW.ac_id AND w.window_id <> NEW.window_id
                 AND w.starts_on <= NEW.ends_on AND NEW.starts_on <= w.ends_on) THEN
        RAISE EXCEPTION 'ac_election_window: overlaps an existing window for AC %', NEW.ac_id
            USING ERRCODE = 'exclusion_violation';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER ac_election_window_no_overlap BEFORE INSERT OR UPDATE ON field.ac_election_window
    FOR EACH ROW EXECUTE FUNCTION field.ac_election_window_no_overlap();
