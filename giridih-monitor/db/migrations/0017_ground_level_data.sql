-- 0017: the ground-level tables the candidates and local-politics pages read
-- (spec 3.1, 3.4).
--
-- Scoped deliberately narrowly. The spec's extended-data migration also covers
-- the influencer registry, the public-issue log, work_log, area_indicator,
-- poll_day_turnout and alert_rule; those belong with Track B, which builds the
-- loaders and role gates they need. What is here is exactly what the two pages
-- added in this change query, because an endpoint that selects from a table
-- nobody created is a 500 at runtime, and the SQL linter only reads migrations.
--
-- Everything in these tables is **declared, transcribed or judged**, never
-- counted, and the columns say which. That distinction is the point:
--
--   * `candidate_profile` holds affidavit data. Assets and criminal cases are as
--     declared by the candidate, not as established by a court, and `source`
--     records where the transcription came from.
--   * `local_office_holder.tagged_party_id` is somebody's judgement. Panchayat
--     polls in Jharkhand are contested without party symbols, so a party
--     against a mukhiya is an inference - the HLD lists that subjectivity as a
--     named risk and requires the source of the tag to be recorded, which is
--     what `tag_source` is for. NULL means untagged, which is not the same
--     claim as independent.
--   * `political_event.effect_sign` is an assessment by whoever logged the
--     event. It is stored next to `source` so a reader can weigh it.
--
-- No transaction control: apply_migrations wraps each file in one transaction.

CREATE TABLE candidate_profile (
    candidate_id      INT PRIMARY KEY REFERENCES candidate(candidate_id) ON DELETE CASCADE,
    incumbent         BOOLEAN,
    contests_prior    SMALLINT CHECK (contests_prior >= 0),
    wins_prior        SMALLINT CHECK (wins_prior >= 0),
    prev_party_id     INT REFERENCES party(party_id),
    -- Derived from prev_party_id rather than asserted independently, but stored
    -- so a query does not have to re-derive it: "stood for a different party
    -- last time" is the single most useful filter on this table.
    turncoat          BOOLEAN,
    deposit_forfeited BOOLEAN,
    -- Rupees, as integers. A candidate's declared assets run to crores, so this
    -- is BIGINT; NUMERIC would be false precision on a self-declared figure.
    assets_declared   BIGINT CHECK (assets_declared >= 0),
    liabilities       BIGINT CHECK (liabilities >= 0),
    criminal_cases    SMALLINT CHECK (criminal_cases >= 0),
    criminal_serious  SMALLINT CHECK (criminal_serious >= 0),
    education         TEXT,
    age               SMALLINT CHECK (age BETWEEN 18 AND 120),
    profession        TEXT,
    affidavit_url     TEXT,
    myneta_id         TEXT,
    tcpd_id           TEXT,
    source            TEXT,
    fetched_at        TIMESTAMPTZ,
    CONSTRAINT serious_within_total
        CHECK (criminal_serious IS NULL OR criminal_cases IS NULL
               OR criminal_serious <= criminal_cases),
    CONSTRAINT wins_within_contests
        CHECK (wins_prior IS NULL OR contests_prior IS NULL
               OR wins_prior <= contests_prior)
);

COMMENT ON TABLE candidate_profile IS
    'Affidavit and secondary-source data about a candidate. Every figure is as '
    'declared by the candidate or transcribed from a public record; none is '
    'established by this system. source records the transcription origin.';

COMMENT ON COLUMN candidate_profile.criminal_cases IS
    'Cases declared in the affidavit. Pending cases are allegations, not '
    'convictions, and must be presented as declared.';

CREATE TABLE local_office_holder (
    id              SERIAL PRIMARY KEY,
    ac_id           INT NOT NULL REFERENCES ac(ac_id) ON DELETE CASCADE,
    area_id         INT REFERENCES area(area_id) ON DELETE SET NULL,
    office          TEXT NOT NULL CHECK (office IN ('mukhiya', 'ward', 'panchayat_samiti',
                                                    'zila_parishad', 'mayor', 'chairperson')),
    name            TEXT NOT NULL,
    tagged_party_id INT REFERENCES party(party_id),
    -- Required whenever a party is tagged: an unsourced inference about
    -- somebody's politics is not something this system should hold.
    tag_source      TEXT,
    term_start      DATE,
    term_end        DATE,
    notes           TEXT,
    CONSTRAINT tag_needs_a_source
        CHECK (tagged_party_id IS NULL OR tag_source IS NOT NULL),
    CONSTRAINT term_ends_after_it_starts
        CHECK (term_end IS NULL OR term_start IS NULL OR term_end >= term_start)
);
CREATE INDEX local_office_holder_ac_idx   ON local_office_holder (ac_id);
CREATE INDEX local_office_holder_area_idx ON local_office_holder (area_id);

COMMENT ON COLUMN local_office_holder.tagged_party_id IS
    'A manual affiliation tag, not a published fact: panchayat polls are '
    'party-less. NULL means untagged, which is not the same as independent.';

CREATE TABLE organisation (
    id                 SERIAL PRIMARY KEY,
    ac_id              INT NOT NULL REFERENCES ac(ac_id) ON DELETE CASCADE,
    name               TEXT NOT NULL,
    kind               TEXT NOT NULL,
    community_id       INT REFERENCES community(community_id),
    alignment_party_id INT REFERENCES party(party_id),
    alignment_source   TEXT,
    notes              TEXT,
    UNIQUE (ac_id, name)
);
CREATE INDEX organisation_ac_idx ON organisation (ac_id);

CREATE TABLE political_event (
    id              SERIAL PRIMARY KEY,
    ac_id           INT NOT NULL REFERENCES ac(ac_id) ON DELETE CASCADE,
    area_id         INT REFERENCES area(area_id) ON DELETE SET NULL,
    booth_uid       TEXT REFERENCES booth(booth_uid) ON DELETE SET NULL,
    occurred_on     DATE NOT NULL,
    kind            TEXT NOT NULL CHECK (kind IN ('rally', 'padyatra', 'defection',
                                                  'protest', 'incident', 'scheme_launch',
                                                  'court_order', 'other')),
    title           TEXT NOT NULL,
    detail          TEXT,
    actors          TEXT[],
    effect_party_id INT REFERENCES party(party_id),
    -- -1, 0 or +1. An assessment by whoever logged the event, stored beside
    -- `source` so a reader can weigh it rather than read it as a measurement.
    effect_sign     SMALLINT CHECK (effect_sign BETWEEN -1 AND 1),
    source          TEXT,
    news_id         INT REFERENCES news_item(news_id) ON DELETE SET NULL,
    created_by      INT REFERENCES app_user(user_id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT effect_needs_a_party
        CHECK (effect_sign IS NULL OR effect_party_id IS NOT NULL)
);
CREATE INDEX political_event_ac_idx   ON political_event (ac_id, occurred_on DESC);
CREATE INDEX political_event_area_idx ON political_event (area_id);
