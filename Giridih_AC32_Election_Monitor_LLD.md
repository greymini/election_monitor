# Giridih Assembly (AC-32) Election Monitor — Low-Level Design

**Version:** 1.0 (build-ready) · **Date:** 20 Sep 2026 · **Companion to:** HLD v0.1

---

## 0. Changes from HLD (orchestration + cost)

| Area | HLD said | LLD decision | Why |
|---|---|---|---|
| Job orchestration | "nightly crawl", unspecified | **Single `worker` container running APScheduler + idempotent Python jobs. No Airflow/Prefect.** | ~6 scheduled jobs, one machine. An orchestrator would cost more RAM than the whole app. |
| LLM usage | "Claude" generically | **Two-tier routing: Haiku 4.5 for classification, tagging and simple lookups; Sonnet 5 for analytical Q&A. Schema + knowledge cards in a cached system prompt. Batch API for all offline labelling.** | Cuts chatbot cost ~50–70% vs Sonnet-everywhere; offline work at half price. |
| Embeddings | "vector store" | **Local `intfloat/multilingual-e5-small` on CPU via sentence-transformers → pgvector.** | Free, Hindi-capable, ~120 MB. No embedding API bill. |
| OCR | Tesseract / Google Vision | **Three-stage: pdfplumber text layer → Tesseract (hin+eng) only for pages with no text layer → manual review queue. Google Vision removed from default path.** | Recent CEO PDFs are text-layer; expect <10% of pages to need OCR. |
| Maps | Mapbox/Leaflet | **Leaflet + free OSM/CARTO raster tiles. Booths as geocoded points; ward/panchayat polygons if shapefiles obtainable, else Voronoi around booth points.** | No Mapbox billing; polygons are a nice-to-have. |
| SQL from chatbot | "text-to-SQL" | **Model proposes SQL → app validates (read-only, allow-listed tables, `LIMIT` enforced, 5 s timeout) → app executes → results returned to model.** Model never touches the DB directly. | Safety and cost control (bad SQL fails fast, no retries billed). |
| Token spend | not covered | **Per-user daily budget, global monthly cap, per-request logging to `llm_usage`.** | Predictable bill. |

**Assumptions made because §13 HLD questions are open** (change if wrong): end user is a campaign/candidate team; Hindi-first UI with English toggle; self-hosted single VPS; Phase 0 includes downloading all source PDFs; ground team exists for caste survey.

---

## 1. Runtime topology

```
VPS (4 vCPU / 8 GB RAM / 80 GB SSD)  —  docker-compose
├── db        postgres:16 + postgis + pgvector          (2 GB RAM cap)
├── api       FastAPI (uvicorn, 2 workers)              (1 GB)
├── worker    APScheduler + jobs + Tesseract + e5-small (2.5 GB)
├── web       nginx serving React build + TLS (certbot) (128 MB)
└── volumes   pgdata/, raw/ (PDFs), ocr/ (text), backups/
```

No Redis, no message queue, no separate vector DB. Jobs write status to a `job_run` table; the API reads it for a status page.

---

## 2. Repository layout

```
giridih-monitor/
├── docker-compose.yml
├── .env.example                 # DB creds, ANTHROPIC_API_KEY, budgets
├── db/
│   ├── migrations/              # sqitch or plain numbered .sql
│   └── seed/                    # blocks, panchayats, wards, surname_dict.csv
├── ingest/
│   ├── fetch_ceo.py             # downloads Form 20, rolls, PS lists
│   ├── fetch_sec.py             # panchayat / GMC results
│   ├── extract_pdf.py           # pdfplumber → text; flags pages for OCR
│   ├── ocr_tesseract.py         # hin+eng OCR for flagged pages
│   ├── parse_form20.py          # → result_booth staging
│   ├── parse_roll.py            # mother roll + supplements → roll_snapshot / roll_change
│   ├── parse_pslist.py          # → booth + booth_crosswalk candidates
│   ├── crosswalk.py             # fuzzy match PS across years
│   └── validate.py              # sum checks, writes review_queue
├── analytics/
│   ├── metrics.sql              # materialized views (swing, transfer, priority)
│   ├── caste_estimate.py
│   ├── scenario.py
│   └── refresh.py               # REFRESH MATERIALIZED VIEW CONCURRENTLY ...
├── news/
│   ├── crawl_rss.py
│   ├── dedupe.py
│   ├── label_batch.py           # builds & submits Batch API job (Haiku)
│   ├── label_collect.py         # collects results → news_item
│   └── embed.py                 # e5-small → news_item.embedding
├── chatbot/
│   ├── router.py                # Haiku intent/complexity classifier
│   ├── tools.py                 # run_sql, search_news, get_booth_card, make_chart
│   ├── prompts/                 # system_cached.md, schema_doc.md, knowledge_cards/
│   ├── agent.py                 # tool loop (max 3 rounds)
│   ├── sql_guard.py             # sqlglot-based validation
│   └── budget.py                # per-user/global token accounting
├── api/                         # FastAPI routers: auth, booths, results, rolls, news, chat, admin
├── web/                         # React + Vite + Tailwind + Leaflet + Recharts + i18next
└── worker/
    └── scheduler.py             # APScheduler job table (see §7)
```

---

## 3. Database schema (DDL summary)

```sql
-- Geography
CREATE TABLE block        (block_id SMALLINT PK, name_en TEXT, name_hi TEXT, kind TEXT CHECK (kind IN ('rural','ulb')));
CREATE TABLE area         (area_id SERIAL PK, block_id SMALLINT REFERENCES block, kind TEXT CHECK (kind IN ('panchayat','ward')),
                           name_en TEXT, name_hi TEXT, geom geometry(MultiPolygon,4326) NULL);
CREATE TABLE booth        (booth_uid TEXT PK,          -- e.g. 'B0001' stable across years
                           area_id INT REFERENCES area, ps_name_en TEXT, ps_name_hi TEXT, building TEXT,
                           village_or_locality TEXT, geom geometry(Point,4326) NULL, geocode_conf REAL);
CREATE TABLE booth_crosswalk (election_id INT, ps_number INT, booth_uid TEXT REFERENCES booth,
                           match_method TEXT, confidence REAL, reviewed BOOLEAN DEFAULT false,
                           PRIMARY KEY (election_id, ps_number));

-- Elections & results
CREATE TABLE election     (election_id SERIAL PK, type TEXT CHECK (type IN ('VS','LS','PANCHAYAT','WARD')),
                           year SMALLINT, poll_date DATE, label TEXT);          -- 'VS-2024', 'LS-2024 (AC seg)'
CREATE TABLE party        (party_id SERIAL PK, abbr TEXT UNIQUE, name_en TEXT, name_hi TEXT, alliance_2024 TEXT);
CREATE TABLE candidate    (candidate_id SERIAL PK, election_id INT REFERENCES election, name_en TEXT, name_hi TEXT,
                           party_id INT REFERENCES party, is_winner BOOLEAN);
CREATE TABLE result_booth (election_id INT, ps_number INT, candidate_id INT, votes INT,
                           PRIMARY KEY (election_id, ps_number, candidate_id));
CREATE TABLE result_booth_meta (election_id INT, ps_number INT, electors INT, total_valid INT, nota INT,
                           rejected INT, tendered INT, postal INT, source_doc TEXT, source_page INT,
                           PRIMARY KEY (election_id, ps_number));

-- Rolls
CREATE TABLE roll_revision (revision_id SERIAL PK, revision_date DATE, label TEXT, is_post_sir BOOLEAN);
CREATE TABLE roll_snapshot (revision_id INT, booth_uid TEXT, electors INT, male INT, female INT, other INT,
                           age_18_19 INT, age_20_29 INT, age_30_39 INT, age_40_49 INT, age_50_59 INT, age_60p INT,
                           PRIMARY KEY (revision_id, booth_uid));
CREATE TABLE roll_change  (revision_id INT, booth_uid TEXT, additions INT, deletions INT, modifications INT,
                           add_18_19 INT, add_female INT, del_death INT, del_shifted INT, del_other INT,
                           PRIMARY KEY (revision_id, booth_uid));
-- NOTE: no table stores individual voter names. Parsers count and discard.

-- Demography & caste (aggregate only)
CREATE TABLE demography   (area_id INT, census_year SMALLINT, population INT, sc INT, st INT, literate INT,
                           main_workers INT, households INT, PRIMARY KEY (area_id, census_year));
CREATE TABLE community    (community_id SERIAL PK, name_en TEXT, name_hi TEXT, category TEXT); -- GEN/OBC/SC/ST/MUSLIM
CREATE TABLE caste_estimate (booth_uid TEXT, community_id INT, est_count INT, est_pct REAL, confidence REAL,
                           source TEXT CHECK (source IN ('surname','census','survey','blend')), updated_at TIMESTAMPTZ,
                           PRIMARY KEY (booth_uid, community_id, source));
CREATE TABLE surname_dict (surname_hi TEXT PK, surname_en TEXT, community_id INT, weight REAL, notes TEXT);

-- Local elections
CREATE TABLE local_result (election_id INT, seat_type TEXT, area_id INT, seat_name TEXT, winner TEXT,
                           runner_up TEXT, tagged_party_id INT NULL, tag_source TEXT, votes INT, margin INT);

-- News & ground
CREATE TABLE news_item    (news_id SERIAL PK, url TEXT UNIQUE, url_hash TEXT, title_hash TEXT, published DATE,
                           source TEXT, title TEXT, body TEXT, summary_hi TEXT, summary_en TEXT,
                           issues TEXT[], parties TEXT[], persons TEXT[], sentiment SMALLINT, -- -2..+2 per party in jsonb
                           sentiment_by_party JSONB, area_ids INT[], labelled_by TEXT, embedding vector(384));
CREATE INDEX ON news_item USING hnsw (embedding vector_cosine_ops);
CREATE TABLE ground_report (report_id SERIAL PK, reported_at TIMESTAMPTZ, booth_uid TEXT, reporter_id INT,
                           text TEXT, issues TEXT[], embedding vector(384));
CREATE TABLE knowledge_card (card_id SERIAL PK, slug TEXT UNIQUE, topic TEXT, body_hi TEXT, body_en TEXT,
                           sources TEXT[], last_reviewed DATE);

-- Ops
CREATE TABLE app_user     (user_id SERIAL PK, phone TEXT UNIQUE, name TEXT, role TEXT CHECK (role IN ('admin','strategist','block')),
                           block_id SMALLINT NULL, daily_token_budget INT DEFAULT 150000);
CREATE TABLE llm_usage    (id BIGSERIAL PK, ts TIMESTAMPTZ, user_id INT, model TEXT, purpose TEXT,
                           input_tokens INT, cached_tokens INT, output_tokens INT, cost_usd NUMERIC(8,5));
CREATE TABLE review_queue (id SERIAL PK, kind TEXT, ref TEXT, payload JSONB, status TEXT DEFAULT 'open', note TEXT);
CREATE TABLE job_run      (id SERIAL PK, job TEXT, started TIMESTAMPTZ, finished TIMESTAMPTZ, status TEXT, log TEXT);
```

**Materialized views** (refreshed by `analytics/refresh.py` after any load):
`mv_result_booth_wide` (one row per election × booth_uid with party columns), `mv_swing`, `mv_transfer_ls_vs`, `mv_new_voter_share`, `mv_booth_priority`, `mv_area_rollup`.

---

## 4. Ingestion pipelines

### 4.1 PDF → text (all sources)
1. `extract_pdf.py`: pdfplumber per page; if `len(text.strip()) < 40` → flag page for OCR.
2. `ocr_tesseract.py`: `pdftoppm -r 300` → `tesseract page.png - -l hin+eng --psm 6`; store text + mean confidence.
3. Pages with OCR confidence < 70 → `review_queue(kind='ocr_page')`.

### 4.2 Form 20 parser (`parse_form20.py`)
- Detect header row (candidate names) via regex on the first table page; map columns → `candidate_id`.
- Row regex: `^\s*(\d{1,3})\s+((?:\d+\s+)+)(\d+)\s*$` → ps_number, per-candidate votes, total.
- **Validation:** Σ candidate votes + NOTA = total_valid per row; Σ rows = AC total published by ECI (tolerance 0). Any row failing → `review_queue(kind='form20_row')`. Nothing loads until the file passes.
- Store `source_doc`, `source_page` on every row (chatbot cites these).

### 4.3 Roll parser (`parse_roll.py`)
- Mother roll: parse header block per PS (PS number, name, part) then count entries by regex on EPIC pattern (`[A-Z]{3}\d{7}`), gender token (`पुरुष|महिला|अन्य`), and age. **Only counts are written**; the parsed names are held in memory and discarded.
- Surname extraction for caste: for each entry take the last token of the name field, look up `surname_dict`, increment community counters per booth. Written only as aggregates to `caste_estimate(source='surname')`.
- Supplement PDFs: sections `परिवर्धन / विलोपन / संशोधन` → `roll_change`. Deletion reason codes (E/S/Q…) → `del_death`, `del_shifted`, `del_other`.

### 4.4 Booth crosswalk (`crosswalk.py`)
1. Anchor year = latest PS list; assign `booth_uid` sequentially.
2. For each older election's PS list, compute `score = 0.5*jaro_winkler(building) + 0.3*jaro_winkler(village/ward) + 0.2*(same roll part ? 1 : 0)` after transliteration to Latin (indic-transliteration) and normalising `प्रा०वि०/प्राथमिक विद्यालय/PS/School` variants.
3. score ≥ 0.85 → auto-accept; 0.65–0.85 → `review_queue(kind='crosswalk')`; < 0.65 → new booth_uid (likely a genuine new/split booth).
4. Split detection: if two new PS map to one old with high scores → mark `match_method='split'`; comparisons then use summed votes.

### 4.5 Geocoding booths
- Nominatim (OSM, free, 1 req/s) on `building + village + "Giridih"`; fallback: centroid of area polygon; store `geocode_conf`. Manual pin-drop UI in admin for low confidence.

---

## 5. Caste estimation (`caste_estimate.py`)

```
for booth in booths:
    s = surname_counts(booth)          # from 4.3, coverage c_s = matched / total electors
    z = census_sc_st(booth.area)       # SC%, ST% from Census 2011 village/ward
    v = survey(booth)                  # optional booth in-charge sheet, coverage c_v ∈ {0,1}
    blend = v if c_v else (0.7*s + 0.3*z_adjusted)     # z only informs SC/ST buckets
    confidence = c_v*0.9 + (1-c_v)*(0.6*c_s + 0.2*(census_year_recency) + 0.2*dict_quality)
    write caste_estimate(source='blend')
```
- `surname_dict` seeded with ~300 Giridih-relevant surnames (Mahato→Kurmi, Yadav, Verma/Barnwal/Sahu→Baniya, Pandey/Mishra/Tiwari/Jha→Brahmin, Singh→ambiguous(weight 0.5 Rajput/Bhumihar), Ansari/Khan/Ahmad→Muslim, Turi/Dusadh/Rajwar/Bhuiyan→SC, Soren/Murmu/Hembrom/Marandi/Tudu→ST, etc.). Ambiguous surnames carry `weight < 1` and split across communities.
- UI always renders confidence as a band; any figure with confidence < 0.4 is greyed and labelled "अनुमान अपर्याप्त".

---

## 6. News pipeline (Haiku, Batch API)

1. **Crawl (every 4 h):** RSS/HTML for Prabhat Khabar Giridih, Dainik Bhaskar Giridih, Hindustan Giridih, Dahad India, Giridih-tagged Google News RSS, ~10 X/Twitter handles via Nitter/RSS bridge. Keep only items matching a geo keyword list (गिरिडीह, पीरटांड, पारसनाथ, मधुबन, ward/panchayat names…).
2. **Dedupe:** `url_hash` exact + `title_hash` (simhash, Hamming ≤ 3). Duplicates never reach the LLM.
3. **Label (nightly 01:00, Batch API, `claude-haiku-4-5-20251001`):** one request per article, JSON-only output:
   `{summary_hi, summary_en, issues[], parties[], persons[], sentiment_by_party{}, area_names[]}`. Allowed `issues` enum: water, roads, electricity, health, education, employment/migration, mining/coal, Parasnath/Marang Buru, law-and-order, welfare-schemes, corruption, candidate/organisation, alliance, other.
4. **Collect (07:00):** parse batch results, map `area_names` → `area_ids` via alias table, write `news_item`.
5. **Embed:** e5-small (`query:`/`passage:` prefixes) → `embedding`.

Cost: ~100 articles/day × ~1,800 in + 300 out tokens on Haiku at batch rates ≈ **$0.17/day ≈ $5/month**.

---

## 7. Scheduler (`worker/scheduler.py`, APScheduler)

| Job | Cron | Idempotency key |
|---|---|---|
| `news.crawl` | `0 */4 * * *` | url_hash |
| `news.label_batch` | `0 1 * * *` | batch_id stored in job_run |
| `news.label_collect` + `news.embed` | `0 7 * * *` | news_id null-labelled |
| `ceo.check_new_supplement` | `0 6 * * *` | file sha256 |
| `analytics.refresh` | after any load; also `30 7 * * *` | — |
| `ops.backup` (pg_dump → volume + optional S3/B2) | `0 3 * * *` | date |
| `ops.usage_report` (token spend summary → admin) | `0 8 * * *` | date |

Jobs are plain functions; any can be run manually: `python -m worker.run news.crawl`.

---

## 8. Chatbot orchestration

```
user msg ──► budget.check(user)  ── over budget ──► polite refusal + admin ping
        │
        ▼
   router.py  (Haiku, ~300 in / 20 out tokens, cached prompt)
        │  returns {intent: lookup|analysis|news|knowledge|smalltalk|blocked, lang: hi|en|hinglish}
        │
        ├─ blocked (individual voter / individual caste / out-of-scope) ──► fixed refusal text
        ├─ smalltalk / knowledge ──► Haiku, cards only
        ├─ lookup (single table, one metric) ──► Haiku + tools
        └─ analysis / news ──► Sonnet 5 + tools
                     │
                     ▼
              agent.py tool loop (max 3 rounds, 20 s wall clock)
                 tools: run_sql | search_news | get_booth_card | make_chart
                     │
                     ▼
              answer with citations [booth_uid, election, source_doc:page | news url]
              → llm_usage row written with cost
```

### 8.1 Prompt caching layout (system prompt, in order)
1. Role, rules, refusal policy, citation format — ~1.5k tokens
2. Schema doc (tables, columns, enums, party abbreviations, area names in hi/en) — ~4k tokens
3. Knowledge cards digest (alliances, timelines, 2024 headline numbers) — ~3k tokens
4. `cache_control: {"type": "ephemeral"}` breakpoint here
5. Conversation (last 6 turns) + tool results

Cache hit rate should exceed 90% in a live session; cached input bills at ~10% of base rate.

### 8.2 Tools
- `run_sql(sql)`: `sql_guard.py` parses with sqlglot → rejects anything not `SELECT`, any table outside allow-list (`mv_*`, `booth`, `area`, `election`, `candidate`, `party`, `roll_snapshot`, `roll_change`, `caste_estimate`, `local_result`, `demography`), injects `LIMIT 500`, runs as `readonly_role` with `statement_timeout=5s`. Returns CSV text (≤ 8k tokens) + row count.
- `search_news(query, from, to, area_ids?, issues?)`: e5 embed → pgvector cosine top-k 8, filtered by date/area → returns id, date, source, title, summary_hi.
- `get_booth_card(booth_uid)`: precomputed JSON (results all years, roll, new voters, caste blend, priority score).
- `make_chart(spec)`: returns a Vega-Lite spec; frontend renders it. No image generation.

### 8.3 Model IDs and settings
- Router / tagging / lookup: `claude-haiku-4-5-20251001`, `temperature 0`, `max_tokens 800`.
- Analysis: `claude-sonnet-5`, `temperature 0`, `max_tokens 1500`.
- Both with `tools` defined once; tool descriptions live inside the cached block.

### 8.4 Budget controls (`budget.py`)
- `daily_token_budget` per user (default 150k ≈ 40 analysis questions); block-role users 60k.
- Global monthly cap from `.env` (`LLM_MONTHLY_CAP_USD=120`); at 80% the router downgrades all `analysis` to Haiku and warns admin; at 100% chatbot is read-only (dashboard unaffected).
- Every response shows a small "≈ ₹x" cost chip to admins only.

---

## 9. API (FastAPI, JWT, role-scoped)

| Method | Path | Role | Notes |
|---|---|---|---|
| POST | `/auth/otp`, `/auth/verify` | — | phone OTP (MSG91/Textlocal ~₹0.2/SMS) or admin-issued passwords to avoid SMS cost |
| GET | `/summary` | all | headline numbers, bypoll countdown |
| GET | `/booths?area_id&election_id&metric` | all (block users filtered to own block) | GeoJSON FeatureCollection |
| GET | `/booths/{booth_uid}/card` | all | cached JSON, TTL until next refresh |
| GET | `/results/{election_id}/booths` | all | wide table; `?format=csv` for export |
| GET | `/rolls/changes?revision_id&area_id` | all | new/deleted voters |
| GET | `/caste?area_id&min_conf` | strategist, admin | aggregates only |
| GET | `/transfer?from=LS-2024&to=VS-2024` | strategist, admin | |
| GET | `/local-results?election_id` | all | |
| GET | `/news?from&to&issue&area_id&q` | all | vector search when `q` given |
| POST | `/ground-reports` | all | from field form |
| POST | `/chat` (SSE stream) | all | router + agent |
| GET/POST | `/admin/review-queue`, `/admin/crosswalk`, `/admin/surnames`, `/admin/cards`, `/admin/usage` | admin | |
| POST | `/scenario` | strategist, admin | body: turnout multipliers, transfer %s, sympathy swing → projected margin range |

---

## 10. Frontend (React + Vite)

- **Routing:** `/` overview · `/map` · `/results/:electionId` · `/voters` · `/caste` · `/transfer` · `/local` · `/news` · `/factors` · `/scenario` · `/admin/*`.
- **State:** TanStack Query (server cache), URL-synced filters (block, area, election, metric) so views are shareable.
- **Map:** Leaflet, CARTO Positron tiles; booth circle markers sized by electors, coloured by selected metric (diverging scale for swing/margin); click → side drawer `BoothCard`.
- **Charts:** Recharts for fixed views; `vega-embed` for chatbot-generated charts.
- **i18n:** i18next, `hi` default, `en` toggle; numerals rendered in Indian grouping (94,042 → ९४,०४२ optional).
- **Chat panel:** docked right, SSE streaming, citations rendered as chips that deep-link to `/results/...#booth_uid` or the news item.
- **Mobile:** all views responsive; `/voters` and `/booths/:uid/card` optimised for block in-charges on phones.
- **Export:** CSV on every table; PNG on every chart (client-side).

---

## 11. Scenario engine (`analytics/scenario.py`)

Inputs (per block or per booth cluster): turnout multiplier `t`, transfer matrix `T` from 2024 parties (JMM, BJP, JLKM, others, NOTA) to bypoll options, sympathy swing `s` (±% applied to JMM from BJP), new-voter allocation vector `n` (share of `additions` to each option, default proportional to booth 2024 shares).

```
votes_bypoll[p] = Σ_booths ( t · (Σ_q votes2024[q] · T[q→p]) + additions · n[p] )
margin = votes[JMM] − votes[BJP]
```
Run 500 Monte-Carlo draws with ±5% noise on `T` and `t` → report P10/P50/P90 margin and the 20 booths whose outcome variance is highest.

---

## 12. Security & compliance

- Rolls: raw PDFs stored in `raw/` with filesystem permissions to `worker` only; not served by API; deleted after parse if `RETAIN_RAW_ROLLS=false`.
- No individual voter records anywhere in DB, logs, or LLM prompts. `parse_roll.py` has a unit test asserting no name/EPIC strings persist.
- Chatbot refusal list (router `blocked`): individual voter lookup, individual caste, EPIC numbers, addresses, phone numbers.
- Block-role users see only their block; caste module hidden from block role.
- All LLM calls logged (purpose, tokens, cost) — no prompt bodies stored beyond 7 days.
- Nightly encrypted `pg_dump`; restore drill once before bypoll announcement.

---

## 13. Cost model (monthly, steady state)

| Item | Estimate |
|---|---|
| VPS 4 vCPU / 8 GB (Hetzner CPX31-class or Indian equivalent) | $15–25 |
| Domain + TLS | ~$1 |
| Object backup (Backblaze B2, 10 GB) | <$1 |
| News labelling — Haiku, Batch (§6) | ~$5 |
| Chatbot — router on Haiku (~200 calls/day) | ~$1 |
| Chatbot — lookups on Haiku (~120/day, cached) | ~$3 |
| Chatbot — analysis on Sonnet 5 (~80/day, ~6k cached-in + 3k fresh-in + 800 out) | ~$40–55 |
| OCR (Tesseract) / embeddings (e5-small) / maps (OSM) | $0 |
| SMS OTP (optional) | $0–10 |
| **Total** | **≈ $65–100 / month**, hard-capped by `LLM_MONTHLY_CAP_USD` |

One-off Phase 0: ~15k PDF pages through pdfplumber/Tesseract on the same VPS — no external cost, ~3–4 h compute.

Rates used: Haiku 4.5 $1/$5, Sonnet 5 $2/$10 per MTok; cache reads ≈10% of input rate; Batch 50% off. Re-check platform.claude.com/docs pricing before finalising budget.

---

## 14. Build order and acceptance criteria

| Sprint | Build | Done when |
|---|---|---|
| S0 (wk 1–2) | compose stack, migrations, `fetch_*`, `extract_pdf`, `parse_form20`, `parse_pslist`, `crosswalk` | VS-2024 + LS-2024 booth results load with zero validation errors; ≥90% booths auto-crosswalked to 2019 |
| S1 (wk 3–4) | `parse_roll`, `roll_change`, `mv_*`, API core, map + results + voters pages | New-voter share visible per booth/panchayat/ward; CSV export works |
| S2 (wk 5) | chatbot router + tools + budget, chat panel | 30 golden Hindi/English questions answered with correct SQL and citations; blocked list enforced |
| S3 (wk 6–7) | surname dict, `caste_estimate`, caste page, survey form | Confidence bands render; no individual-level data path exists (test passes) |
| S4 (wk 8–9) | transfer view, local elections load, news pipeline live | Nightly batch runs 3 days unattended; news page shows tagged items |
| S5 (wk 10–12) | scenario engine, knowledge cards, roles, backups, load older Form 20s (2009/2014) | Restore drill passes; usage report shows spend within cap |

---

*All figures and derived estimates produced by this system must be reviewed for accuracy and completeness before any decision relies on them.*
