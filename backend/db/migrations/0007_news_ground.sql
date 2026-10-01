-- 0007: news, ground reports and curated knowledge cards

CREATE TABLE news_source (
    source_id   SERIAL PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    kind        TEXT NOT NULL CHECK (kind IN ('rss', 'html', 'google_news', 'x')),
    url         TEXT NOT NULL,
    lang        TEXT NOT NULL DEFAULT 'hi',
    is_active   BOOLEAN NOT NULL DEFAULT true,
    last_ok_at  TIMESTAMPTZ,
    last_error  TEXT
);

CREATE TABLE news_item (
    news_id            SERIAL PRIMARY KEY,
    url                TEXT NOT NULL UNIQUE,
    url_hash           TEXT NOT NULL,
    title_hash         TEXT,
    simhash            BIGINT,
    published          DATE,
    fetched_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    source             TEXT,
    title              TEXT,
    body               TEXT,
    summary_hi         TEXT,
    summary_en         TEXT,
    issues             TEXT[],
    parties            TEXT[],
    persons            TEXT[],
    sentiment          SMALLINT CHECK (sentiment BETWEEN -2 AND 2),
    sentiment_by_party JSONB,
    area_ids           INT[],
    area_names_raw     TEXT[],
    labelled_by        TEXT,
    labelled_at        TIMESTAMPTZ,
    batch_id           TEXT,
    embedding          vector(384)
);
CREATE INDEX news_item_published_idx  ON news_item (published DESC);
CREATE INDEX news_item_urlhash_idx    ON news_item (url_hash);
CREATE INDEX news_item_simhash_idx    ON news_item (simhash);
CREATE INDEX news_item_issues_idx     ON news_item USING gin (issues);
CREATE INDEX news_item_areas_idx      ON news_item USING gin (area_ids);
CREATE INDEX news_item_embed_idx      ON news_item USING hnsw (embedding vector_cosine_ops);
CREATE INDEX news_item_unlabelled_idx ON news_item (news_id) WHERE labelled_at IS NULL;

CREATE TABLE ground_report (
    report_id   SERIAL PRIMARY KEY,
    reported_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    booth_uid   TEXT REFERENCES booth(booth_uid),
    area_id     INT REFERENCES area(area_id),
    reporter_id INT,
    text        TEXT NOT NULL,
    issues      TEXT[],
    sentiment   SMALLINT CHECK (sentiment BETWEEN -2 AND 2),
    embedding   vector(384)
);
CREATE INDEX ground_report_booth_idx ON ground_report (booth_uid);
CREATE INDEX ground_report_embed_idx ON ground_report USING hnsw (embedding vector_cosine_ops);

CREATE TABLE knowledge_card (
    card_id       SERIAL PRIMARY KEY,
    slug          TEXT NOT NULL UNIQUE,
    topic         TEXT NOT NULL,
    title_hi      TEXT,
    title_en      TEXT,
    body_hi       TEXT,
    body_en       TEXT,
    sources       TEXT[],
    last_reviewed DATE,
    in_prompt     BOOLEAN NOT NULL DEFAULT true
);
