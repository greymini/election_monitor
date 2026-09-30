# Audit — Giridih AC-32 Election Monitor

**Audited:** 23 September 2026 · **Scope:** `giridih-monitor/` against `Giridih_AC32_Election_Monitor_HLD.md` v0.1 and `..._LLD.md` v1.0
**Method:** full read of all ~15,000 lines of first-party source. Frontend built (`npm run build`, clean). Test suite run (`pytest -q`, 96 passed

). Header-classification and label-parsing behaviour verified by executing the parser directly. No database was available, so SQL-level claims are derived from reading the DDL and views; where that leaves genuine doubt it is stated.

> This report is itself an output that should be reviewed for accuracy and completeness before decisions are taken on it. Every finding cites a file and line so it can be checked.

---

## 1. Summary

This is a carefully written, unusually well-documented codebase with a coherent architecture, and it is **not currently able to produce a correct booth-level result.** The skeleton is all there — 12 ordered migrations, ten materialized views, four ingestion parsers, a booth crosswalk with confidence scoring, a role-scoped FastAPI layer, and a React dashboard that builds clean and handles loading/error/empty states properly. The privacy design is real rather than decorative: the roll parser genuinely returns only counters, the schema has nowhere to put a voter, and there is a test that proves it.

But the pipeline from a Form 20 PDF to a number on screen is broken in several independent places, and the breakages are silent. Form 20 column headers never resolve to a party, so every candidate's votes collapse into a single unattributed bucket and the wide results view reports a 100% margin for a party that does not exist (C1, D3). The AC-total validation that the README calls "the gate to trust" compares against a key that can never match, so every load either aborts with a confusing error or is run with the check switched off (C2). Polling stations that match in the 0.65–0.85 review band get **no crosswalk row at all**, and every materialized view inner-joins that table, so those booths' votes vanish from every rollup without an error (B2) — and the check designed to catch exactly this reports ~100% healthy because the dropped rows are missing from its denominator too (C11). `election_roll_link` has no writer anywhere in the codebase, so the New Voters materialized view reports zero additions for every booth while a different screen reading the base table shows the real numbers (B1). `result_booth_meta.electors` is never written, so turnout is NULL everywhere it appears (B4).

Three concerns above all the rest. **First, nothing can be installed from a clean checkout**: `docker-compose.yml` pins `pgvector/pgvector:pg16`, which does not ship PostGIS, and migration 0001 opens with `CREATE EXTENSION postgis` (A1). **Second, the individual-voter guarantee is broken outside the parser**: `extract_pdf.py` writes every page's full text to a JSON cache on disk before any parser sees it, so complete electoral rolls — names, EPIC numbers, fathers' names, addresses — are persisted under `ocr/`, and `discard_raw()` then deletes the source PDF while leaving that cache in place (C3). **Third, the booth crosswalk — correctly identified in the HLD as the highest-risk object — has two data-corrupting defects** (B2, B3), one of which silently binds one election's booths onto another election's booths.

The system is roughly at the state the LLD calls end of S1, with S2–S5 scaffolded. It is not close to producing publishable numbers.

---

## 2. Repository map

```
giridih-monitor/
├── docker-compose.yml          db/api/worker/web on one host           PARTIAL (A1, A2)
├── .env.example                86 lines, matches what the code reads   LIVE
├── docker/
│   ├── Dockerfile.api          python:3.11-slim, non-root uid 10001    LIVE
│   ├── Dockerfile.worker       + tesseract hin/eng, poppler, pg_dump   PARTIAL (A3)
│   ├── Dockerfile.web          node build → nginx                      PARTIAL (A4)
│   └── nginx.conf              SPA + /api proxy + SSE passthrough      PARTIAL — no TLS (A2)
├── db/
│   ├── apply_migrations.py     forward-only runner, checksum drift warn LIVE
│   ├── migrations/0001-0012    extensions, geo, elections, rolls,      LIVE
│   │                           caste, local, news, ops, 10 MVs, grants
│   └── seed/                   blocks, 36 wards, 13 parties, 22        LIVE
│       │                       communities, 176 surnames, AC totals
│       ├── areas_panchayats.csv  empty template, deliberately          SCAFFOLDING (documented)
│       └── knowledge_cards/    6 curated cards                         LIVE
├── common/
│   ├── config.py               env-only settings, no pydantic dep      LIVE
│   ├── db.py                   psycopg3 pools, rw + readonly           LIVE
│   ├── jobs.py                 job_run bookkeeping ctx manager         LIVE (already_done dead)
│   ├── logging_setup.py        plain stdout logging                    LIVE
│   ├── textnorm.py             Devanagari norm, building canon, translit LIVE — best module here
│   └── similarity.py           Jaro-Winkler, jellyfish + fallback      LIVE (A7)
├── ingest/
│   ├── fetch_ceo.py            portal discovery + download, writes source_doc  LIVE
│   ├── fetch_sec.py            SEC discovery + hand-transcribed CSV loader     LIVE, undocumented
│   ├── extract_pdf.py          pdfplumber → text + lattice tables, cached      LIVE (C3)
│   ├── ocr_tesseract.py        hin+eng OCR for pages with no text layer        LIVE
│   ├── parse_form20.py         table-first, regex fallback, validation gate    BROKEN (C1, C2, C4-C6)
│   ├── parse_roll.py           counts-only roll/supplement scanner             PARTIAL (C7, C8)
│   ├── parse_pslist.py         booth backbone + anchor crosswalk               PARTIAL (B5)
│   ├── crosswalk.py            fuzzy PS matching across years                  BROKEN (B2, B3)
│   ├── geocode.py              Nominatim + district bbox + manual pin          LIVE
│   └── validate.py             6 cross-checks, review_queue writer             PARTIAL (C11, C12)
├── analytics/
│   ├── caste_estimate.py       surname × census × survey blend + confidence    PARTIAL (B8)
│   ├── scenario.py             transfer matrix + 500-draw Monte Carlo          LIVE (D5, D6)
│   ├── refresh.py              MV refresh in dependency order                  LIVE (D-note)
│   └── metrics.sql             analyst crib sheet, never executed              REFERENCE
├── news/
│   ├── crawl_rss.py            8 feeds, geo keyword filter, simhash dedupe     LIVE
│   ├── dedupe.py               url_hash + simhash Hamming                      LIVE
│   ├── label_batch.py          Batch API submit (Haiku)                        LIVE, needs API key
│   ├── label_collect.py        batch result → news_item                        LIVE, needs API key
│   └── embed.py                e5-small → pgvector                             LIVE
├── chatbot/                    router, tools, sqlglot guard, agent, budget     PARKED — see §7
├── api/
│   ├── main.py                 app, CORS, global error handler, /health        LIVE
│   ├── deps.py                 JWT, bcrypt, role + block scoping               LIVE (E1)
│   ├── booth_card.py           one booth, all sources, with caveats            LIVE
│   └── routers/                auth, data, news, chat, scenario, admin         LIVE
├── worker/
│   ├── scheduler.py            APScheduler, 10 cron jobs, IST                  LIVE
│   ├── jobs.py                 name → callable registry                        LIVE
│   ├── run.py                  manual single-job runner                        LIVE
│   └── ops.py                  backup, purge, usage report, supplement watch   PARTIAL (G1)
├── web/                        React 18 + Vite 6 + Tailwind + Leaflet + Recharts
│   ├── src/lib/api.ts          token, 401 handling, CSV download               LIVE
│   ├── src/lib/format.ts       en-IN grouping, confidence bands                LIVE
│   ├── src/lib/tokens.ts       CSS-var palette, diverging/sequential ramps     LIVE
│   ├── src/i18n/               i18next, hi default, en toggle, html lang       LIVE
│   ├── src/pages/              11 routes, lazy-loaded                          LIVE (F1-F4)
│   └── src/components/         Layout, DataTable, BoothDrawer, ChatPanel, …    LIVE
├── scripts/
│   ├── create_admin.py         bcrypt user bootstrap                           LIVE
│   └── demo_api.py             803-line synthetic server, clearly labelled     SCAFFOLDING
├── tests/                      96 tests, no DB required                        PARTIAL — see §H
└── docs/RUNBOOK.md             on-call procedures, review queue, restore drill LIVE, good
```

**Not a git repository.** There is no `.git` directory anywhere under `election_monitor/`. There is no version history, no branches, no ability to bisect a regression, and the question "are there secrets in git history?" is unanswerable because there is no history. For a system heading into a by-election with multiple operators, this is a real operational gap (G2).

---

## 3. Implemented vs intended

| Feature | Intended (HLD/LLD) | Actual | Status | Notes |
|---|---|---|---|---|
| Compose stack | db + api + worker + web, TLS via certbot | All four services defined; no TLS server block, no certbot | **diverged** | A1 blocks startup entirely; A2 means HTTP-only |
| Migrations | ordered, rebuildable | 12 numbered files, checksum-tracked, forward-only | **done** | No down-migrations (B7) |
| Form 20 parse | header detect, row regex, validation gate | Lattice-table path + regex fallback, both work; party attribution and AC check do not | **broken** | C1, C2 |
| Form 20 validation | Σcand+NOTA = total; Σrows = ECI total, tolerance 0 | Row arithmetic works; AC check cannot match its keys | **broken** | C2 |
| Booth crosswalk | stable `booth_uid`, jaro-winkler, 3 confidence bands, review queue | Scoring implemented and tested; the write path drops the review band and collides new UIDs | **broken** | B2, B3 |
| Roll parser | counts only, names discarded | True in the parser; false on disk | **diverged** | C3 |
| Roll additions/deletions | per booth per revision, SIR-aware | `roll_change` loads; `mv_new_voter_share` reads a table nothing writes | **broken** | B1 |
| Turnout | per booth, per area, on the map | `electors` never written by any loader | **missing** | B4 |
| Swing | Δ share between same-type elections | Correct between years; fabricated for the earliest year | **partial** | D2 |
| LS↔VS transfer | booth-level, floating vote | Views correct; needs an LS-2024 PS list + crosswalk that the runbook never mentions | **partial** | D4 |
| Caste estimate | booth-only, blended, confidence, greyed under 0.4 | Blend + confidence implemented and surfaced end-to-end | **done** | Census arm dead (B8); dict thin (C14) |
| Caste × vote overlay | scatter of community % vs party share | Crib-sheet SQL only; UI plots community % against a constant | **missing** | F4 |
| Ground survey (samikaran) | overrides inference, confidence 0.9 | Table exists; no endpoint, no CLI, no form | **missing** | B8 |
| Census 2011 demography | village/ward PCA feeds SC/ST | Table exists and is read; no loader | **missing** | B8 |
| Local elections | panchayat + GMC results, manual party tag | CSV loader in `fetch_sec.py`; absent from the runbook | **partial** | Undocumented |
| News pipeline | crawl, dedupe, Batch label, embed, search | All five implemented | **done** | Labelling needs an Anthropic key; without it `/news` is empty |
| Scenario engine | transfer matrix, Monte Carlo, P10/P50/P90 | Implemented, parameterised, honestly presented | **done** | D5, D6 |
| Booth priority | 4-factor weighted percentile score | Implemented, weights match the published formula | **done** | Two of four inputs are currently constant |
| API roles | admin / strategist / block, block-scoped | Enforced centrally in `deps.py`, applied per query | **done** | One bypass via chat (E2) |
| Auth | OTP or admin passwords | Password path works; OTP sender is a stub | **partial** | E7 |
| Dashboard | 11 modules, hi/en, Leaflet, Recharts, CSV export | All routes present, builds clean, i18n and Indian grouping correct | **done** | F1–F4 |
| Backups | nightly pg_dump + optional off-host | Local volume only | **partial** | G1 |
| Chatbot | two-tier routing, guarded SQL, cited answers | Implemented | **parked** | §7 |

---

## 4. Findings

### A. Structure and build

**A1 · Critical · `docker-compose.yml:7`, `db/migrations/0001_extensions.sql:5`**
The `db` service is `pgvector/pgvector:pg16`. That image is `postgres:16` plus pgvector; it does **not** contain PostGIS. Migration 0001 begins `CREATE EXTENSION IF NOT EXISTS postgis`. The migration file's own comment (line 2–3) says the image must be "pgvector/pgvector:pg16 **with postgis installed**, or postgis/postgis:16-3.4 with pgvector added" — neither is what compose pins.
*Consequence:* README step 3, `docker compose run --rm worker python -m db.apply_migrations --seed`, fails on the first statement with `ERROR: extension "postgis" is not available`. No table is created, no seed loads, nothing else in the README can run. A clean checkout cannot be brought up at all.
*Fix:* build a small image `FROM pgvector/pgvector:pg16` that `apt-get install postgresql-16-postgis-3`, and pin that. Add a compose healthcheck or a migration precondition that asserts both extensions exist.

**A2 · High · `docker/nginx.conf:1-38`, `docker-compose.yml:104-109`**
Port 443 is published, a `certs:/etc/letsencrypt` volume is mounted, and `Dockerfile.web:12` exposes 443 — but `nginx.conf` has exactly one `server` block, `listen 80`, with no TLS directives, and there is no certbot service anywhere in compose. LLD §1 specifies "nginx serving React build + TLS (certbot)".
*Consequence:* the deployment is plain HTTP. JWTs, login passwords, booth-level strategy data and caste estimates all travel in cleartext over the public internet. The `certs` volume and the 443 mapping make it look as though TLS is configured when it is not.
*Fix:* add a certbot sidecar and a 443 server block with a 301 from 80, or terminate TLS at a reverse proxy in front and stop publishing 443 from this container.

**A3 · High (verify on a live host) · `docker/Dockerfile.worker:26`, `docker-compose.yml:80-82`**
The image runs `mkdir -p /models /data && chown -R worker /app /models /data`, then `USER worker` (uid 10002). Compose then mounts named volumes at `/data/raw`, `/data/ocr`, `/data/backups` — paths that do **not** exist in the image, so Docker creates the mountpoints itself.
*Consequence:* if Docker creates those directories root-owned (which is the common behaviour when the mountpoint is absent from the image), the worker cannot write to them, and every PDF download, every OCR cache write and every `ops.backup` fails with `PermissionError` on first run.
*What I'd check:* `docker compose run --rm worker sh -c 'id; touch /data/raw/x'`. If it fails, add `mkdir -p /data/raw /data/ocr /data/backups` before the `chown` in the Dockerfile.

**A4 · Medium · `docker/Dockerfile.web:6`; no `.dockerignore` at repo root or in `web/`**
The web build uses `context: ./web` and `COPY . .`. `web/node_modules` exists on this machine and there is no `.dockerignore` to exclude it.
*Consequence:* the host's `node_modules` — including any platform-native binaries built for Windows — is copied into the Linux build context, then `npm ci` runs on top of it. At best the build is slow and the context is hundreds of megabytes; at worst it picks up incompatible binaries. Also copies `web/dist` and `tsconfig.tsbuildinfo`, both stale.
*Fix:* add `web/.dockerignore` containing `node_modules`, `dist`, `*.tsbuildinfo`.

**A5 · Medium · `api/main.py:14`, `api/routers/chat.py:14`, `chatbot/sql_guard.py:23`**
There is no chatbot feature flag anywhere in the repo (grep for `CHAT_ENABLED`/`ENABLE_CHAT`/`FEATURE_` returns nothing in first-party code). `main.py` imports the `chat` router unconditionally, which imports `chatbot.agent` at module scope, which reaches `chatbot/sql_guard.py`'s top-level `import sqlglot`.
*Consequence:* `sqlglot` is a hard import-time requirement of the whole API. Remove it from `requirements-api.txt` and the dashboard stops booting. The chat feature cannot be turned off without editing code. See §7 for the full isolation answer.
*Fix:* one setting, `CHAT_ENABLED`, guarding `app.include_router(chat.router)`, and move `from chatbot.agent import ask` inside the handler.

**A6 · Medium · no version control**
No `.git` anywhere under `election_monitor/`.
*Consequence:* no history, no blame, no rollback, no review, no CI. Multiple people cannot work on this safely, and the audit question "any credential in git history" cannot be answered.
*Fix:* `git init`, commit, verify `.gitignore` (which is already correct) before the first push.

**A7 · Medium · `common/similarity.py:10-16`, `requirements-dev.txt:1`, `requirements-worker.txt:16`**
`jellyfish` is in the worker requirements but not in `requirements-api.txt`, which is all that `requirements-dev.txt` pulls in. Confirmed: `python -c "import jellyfish"` fails in the project `.venv`.
*Consequence:* all 96 tests — including the seven in `tests/test_crosswalk.py` that validate the 0.85 auto-accept and 0.65 review thresholds — run against the pure-Python fallback, while production runs `jellyfish`. The docstring's claim that the two "give the same answers either way" is untested and the two implementations differ in how the Winkler prefix boost is gated. The thresholds that decide which booths get matched are validated against code that never executes in production.
*Fix:* add `jellyfish` to `requirements-dev.txt`, and add a test that asserts both implementations agree to 3 decimal places over the crosswalk fixture set.

**A8 · Low · unused dependencies**
`requirements-worker.txt` declares `pandas==3.0.6`, `scipy==1.17.1`, `pypdf==6.19.0`, `rapidfuzz==3.14.6` — none is imported anywhere (verified by grep across all `.py`). `requirements-api.txt:13` declares `python-multipart`; there is no file upload or form endpoint. `web/package.json:22-24` declares `vega`, `vega-lite` and `vega-embed`; no `src/` file references them (the production build has no vega chunk).
*Consequence:* pandas + scipy alone add roughly 150 MB to a worker image that also pulls torch via `sentence-transformers`, on an 80 GB VPS with a 2.5 GB memory cap. The vega packages are ~4 MB of `node_modules` for a renderer that was never wired up (F5).
*Fix:* delete them. Keep `sentence-transformers`; `embed.py` needs it.

**A9 · Low · `.env.example:10-11, 26-27`; `common/config.py:140-147`**
`POSTGRES_HOST` and `POSTGRES_PORT` are documented but read by nothing — not by `config.py`, not by `docker-compose.yml`. Changing them has no effect; only `DATABASE_URL` matters. `SMS_API_KEY` and `SMS_SENDER_ID` are likewise never read (the sender is a stub, E7). `Prices.cache_write_multiplier` defaults to 1.25 in the dataclass but is not read from the environment in `get_settings()` and is absent from `.env.example`.
*Otherwise the env contract is clean:* every variable the first-party code reads is present in `.env.example`, and `POSTGRES_DB/USER/PASSWORD` and `HF_HOME` are correctly consumed by compose interpolation and the transformers library respectively.

**A10 · Low · `docker-compose.yml:17`**
`./db/migrations:/migrations:ro` is mounted into the `db` container and nothing ever reads it — migrations are applied from the worker. A dead mount that implies auto-migration on startup, which does not happen.

**A11 · Low · `web/index.html:8-13`**
Fonts (Noto Sans Devanagari, Inter) are fetched from `fonts.googleapis.com` at page load. Undocumented external dependency; sends a request to Google on every page view from a campaign tool; Devanagari rendering degrades to OS fallback if the CDN is blocked. `nginx.conf` sets no CSP. Self-host the two font families.

**A12 · Low · stubs**
No `TODO`, `FIXME`, `XXX`, or `NotImplementedError` anywhere in the codebase — genuinely clean. One bare `pass` at `chatbot/agent.py:126` (swallowed exception). `analytics/metrics.sql` is never executed (documented as a crib sheet). `scripts/demo_api.py` (803 lines) is synthetic scaffolding, clearly labelled and not referenced by compose, so it cannot be deployed by accident.

---

### B. Data model and migrations

Schema as implemented: 29 tables + 10 materialized views across `0001`–`0012`. Foreign keys and indexes are generally well chosen — `booth→area→block`, `candidate→election→party`, `result_booth→candidate`, partial indexes on `review_queue(status='open')` and `booth_crosswalk(confidence) WHERE NOT reviewed`, GIN on `news_item.issues/area_ids`, HNSW on both embedding columns, GIST on both geometry columns, and unique indexes on every MV so `CONCURRENTLY` is at least possible. Migrations are ordered by zero-padded filename, tracked in `schema_migration` with a checksum, and warn (correctly) if an applied file's contents change.

**B1 · Critical · `db/migrations/0010_mv_analytics.sql:66-74, 88`; `db/migrations/0004_rolls.sql:48`**
`mv_new_voter_share` computes its baseline date from `election_roll_link`. **No code anywhere writes to `election_roll_link`** — no loader, no seed CSV, no admin endpoint, no CLI. Grep across the repo returns only the `CREATE TABLE` and this one `SELECT`.
*Consequence:* the `base` CTE returns no rows, so `(SELECT revision_date FROM base)` is NULL, so `WHERE rr.revision_date > NULL` never matches, so the `changes` CTE is empty. Every booth gets `additions = 0`, `deletions = 0`, `new_voter_pct = 0.00`. The map's `new_voter_pct` metric, the booth card's `new_voters` block, and the 0.25 new-voter term in `mv_booth_priority` all silently read zero. Meanwhile `/rolls/changes` reads `roll_change` directly and shows the real additions — so the product displays two contradictory new-voter figures on two different screens, with no error and no validation check covering it. This is HLD "key focus" module 4.
*Fix:* populate `election_roll_link` (a seed row pointing VS-2024 at its roll revision is enough), and add a `validate.py` check that fails when `mv_new_voter_share` sums to zero while `roll_change` does not.

**B2 · Critical · `ingest/crosswalk.py:244-251`**
A station scoring between `REVIEW_FLOOR` (0.65) and `AUTO_ACCEPT` (0.85) writes **only** a `review_queue` row. No `booth_crosswalk` row is created. The docstring at line 14 states this as the intent ("nothing loaded"). But every materialized view reaches results through an **inner** join on that table — `0009_mv_results.sql:17` and `:61`.
*Consequence:* those polling stations' votes are dropped from `mv_result_booth_party`, `mv_booth_party_share`, `mv_result_booth_wide`, `mv_swing`, `mv_transfer_ls_vs`, `mv_volatility`, `mv_area_rollup` and `mv_booth_priority`. The HLD (§3) expects 10–20% of booths to need manual matching, so a VS-2019 comparison would be built on ~80–90% of the constituency while presenting itself as complete. Every 2019→2024 swing, every AC-level 2019 total, and every LS↔VS transfer figure derived from a partially-crosswalked election is wrong by the size of the missing set. Note the contrast: the `new` branch three lines below explicitly mints a booth "so its votes are not silently dropped from AC totals" — the review branch has precisely the bug that branch was written to avoid.
*Partial mitigation:* `validate.py:74-81` (`check_crosswalk_coverage`) does detect orphaned `result_booth` rows — but only if someone runs `ingest.validate` and reads the output, and the dashboard surfaces nothing.
*Fix:* write the `booth_crosswalk` row with its real (sub-threshold) confidence and `reviewed = false`, exactly as the `new` branch does. The confidence column and the `weak_crosswalks` counter in `/summary` already exist to carry the uncertainty; dropping the row destroys information instead of flagging it.

**B3 · Critical · `ingest/crosswalk.py:258` with `:333`**
`main()` calls `apply_matches(..., next_uid_start=0)` — hardcoded. New booth UIDs are minted as `f"B9{counter:03d}"` starting from `B9001` on **every invocation**.
*Consequence:* run `crosswalk --election VS-2019 --apply` and unmatched stations become B9001, B9002, …. Run `crosswalk --election VS-2014 --apply` next and the counter restarts at zero, producing B9001 again. `INSERT INTO booth … ON CONFLICT (booth_uid) DO NOTHING` (line 268) means the existing booth row survives, but the new `booth_crosswalk` row for VS-2014 now points at **B9001 — the booth created for a completely different VS-2019 polling station**. VS-2014 and VS-2019 votes from two unrelated stations are summed onto one `booth_uid` by `mv_result_booth_party`'s `GROUP BY`, and the resulting swing is meaningless. Silent: `ON CONFLICT DO NOTHING` on the crosswalk insert suppresses any error.
*Fix:* derive the start from `SELECT COUNT(*) FROM booth WHERE booth_uid LIKE 'B9%'`, or better, use a sequence. Add a uniqueness assertion over `(booth_uid, match_method='new')` per election.

**B4 · High · `ingest/parse_form20.py:393-401`; `db/migrations/0003_elections.sql:48, 53`**
`result_booth_meta` has `electors` and `postal` columns. The only INSERT in the codebase (verified by grep) lists `total_valid, nota, rejected, tendered, source_doc, source_page` — **neither `electors` nor `postal`**. `Form20Row` has no electors field and `TAIL_LABELS` has no electors pattern. Nothing else writes the table.
*Consequence:* `electors` is NULL for every booth, so `mv_result_booth_wide.turnout_pct` (`0009:115`), `mv_area_rollup.electors`/`turnout_pct` (`0011:100, 111`), `mv_booth_priority.electors`/`turnout_pct`, `/summary.baseline.electors`, the Results table's electors and turnout columns, and the booth card's turnout are all NULL. Selecting "turnout_pct" on the map colours every booth grey. `MapExplorer.tsx:102` sizes markers by `p.electors ?? 400`, so every marker is the clamped minimum radius while the caption at line 139 claims "Marker size is the electorate". Turnout is an HLD module-1 headline metric and a module-2 choropleth option; it is structurally absent.
*Fix:* Form 20 does not print electors — take it from `roll_snapshot` via `election_roll_link` (see B1), or from the PS list. AC-level electors are already seeded in `result_ac_total` (`ac_totals.csv:6, 11`) and joined by nothing.

**B5 · High · `ingest/parse_pslist.py:217, 231-239`**
`load_anchor` mints `booth_uid = f"B{e.ps_number:04d}"` from the anchor list's PS number, and upserts with `ON CONFLICT (booth_uid) DO UPDATE SET area_id, ps_name_hi, building, village_or_locality, current_ps_number`.
*Consequence:* there **is** a stable booth identifier and older years **do** go through the crosswalk — the core design is right. But the identifier is minted from one year's PS numbering, so re-running `parse_pslist --anchor` against a newer PS list (very likely before a bypoll, after rationalisation) silently rebinds `B0147` to whatever station is number 147 in the new list. Every existing `booth_crosswalk`, `roll_snapshot`, `roll_change`, `caste_estimate` and `ground_report` row keyed on `B0147` now refers to a different physical booth. No warning, no guard.
*Fix:* refuse `--anchor` when `booth` is non-empty unless an explicit `--re-anchor` flag is given, and on re-anchor route the new list through `crosswalk.py` against the existing booths rather than overwriting them.

**B6 · Medium · `db/migrations/0002_geography.sql:59, 73`**
`booth_crosswalk.election_id` and `ps_list_entry.election_id` are plain `INT` with no `REFERENCES election(election_id)`, unlike every other election reference in the schema.
*Consequence:* a typo'd election id creates orphan crosswalk rows that no join will ever find and no constraint will reject; deleting an election leaves its crosswalk behind, and the next election to take that serial inherits it.
*Fix:* add the FKs with `ON DELETE CASCADE`, matching `result_booth`.

**B7 · Medium · `db/apply_migrations.py`**
Forward-only. No `down` files, no rollback path. Ordered: yes (zero-padded sort, `:43`). Repeatable: yes (`schema_migration` by filename, with a checksum-drift warning at `:63-68`). Rebuildable from scratch: yes, once A1 is fixed. Reversible: **no**.
*Consequence:* a bad migration on the VPS can only be undone by restoring a backup — which is itself local-only (G1).

**B8 · Medium · `db/migrations/0005_demography_caste.sql:5, 50`**
`demography` and `caste_survey` are read by `analytics/caste_estimate.py:207-210` and `:221` and written by **nothing**. There is no Census loader and no survey intake (no endpoint, no CLI, no form).
*Consequence:* `census_sc_pct`/`census_st_pct` are always NULL, so the census arm of the blend never executes (`caste_estimate.py:112-117` skips on None) and `census_recency` returns 0.0, capping achievable confidence at `0.6·coverage + 0.2·dict_quality` ≤ 0.8. `inputs.survey` is always None, so the 0.9-confidence survey override — the highest-quality caste input in the design, and LLD S3's acceptance criterion — can never be supplied. The caste module runs on surname inference alone.

**B9 · Medium · `db/migrations/0003_elections.sql:9`; `analytics/scenario.py:215`**
`is_baseline` is a plain boolean with no uniqueness constraint. `mv_booth_priority` defends itself with `ORDER BY year DESC LIMIT 1` (`0011:17`), but `load_baseline` does not — it joins `ON e.election_id = w.election_id AND e.is_baseline`.
*Consequence:* flag a second election as baseline and every booth appears twice in the scenario input, doubling all projected vote totals. Latent today (only VS-2024 is seeded) but one `UPDATE` away.
*Fix:* `CREATE UNIQUE INDEX ON election ((true)) WHERE is_baseline`.

**B10 · Low · `ingest/extract_pdf.py:150`, `ingest/fetch_ceo.py:125`**
`source_doc.parse_status` is set to `'new'` on download and `'extracted'` by `extract_pdf --register`, but no parser ever advances it to `'parsed'` or `'loaded'`, and `parsed_at` is never set. `register_source_doc` is called only from `extract_pdf`'s own `main()` with `--register`, which the README's Phase 0 never runs.
*Consequence:* `/admin/sources` shows every file stuck at `new`, so the audit trail cannot answer "has this PDF been loaded?".

**B11 · Low · `db/migrations/0004_rolls.sql:12`; `ingest/parse_roll.py:326-332`**
`roll_revision` has `UNIQUE (revision_date, is_mother)`, but the upsert's `ON CONFLICT` targets `(label)` only.
*Consequence:* loading two supplements dated the same day under different labels raises an unhandled `UniqueViolation` mid-load.

**Indexes.** Adequate for the data volume throughout. The only join column without an index is `booth.current_ps_number`, used by `parse_roll._booth_for_ps`'s fallback (`:314`) — irrelevant at ~350 rows. `caste_estimate` is filtered by `source` and `confidence` in `/caste` with no supporting index beyond the partial `WHERE source = 'blend'` one; also fine at this scale.

---

### C. Ingestion and data correctness

**C1 · Critical · `ingest/parse_form20.py:322-362`**
I ran the parser against representative Form 20 headers. Results:

```
EN header ['Serial No. of Polling Station','Sudivya Kumar',...,'Total of Valid Votes','No. of Rejected Votes','NOTA','Total','No. of Tendered Votes']
  → candidates ['Sudivya Kumar', 'Nirbhay Kumar Shahabadi', 'Navin Anand']
HI header ['क्रम सं','सुदिव्य कुमार',...,'कुल वैध मत','अस्वीकृत मत','नोटा','कुल','निविदत्त मत']
  → candidates ['सुदिव्य कुमार', 'निर्भय कुमार शाहाबादी', 'नवीन आनंद']

split_candidate_label('Sudivya Kumar')          → name='Sudivya Kumar',  party=None
split_candidate_label('सुदिव्य कुमार')            → name='सुदिव्य कुमार',   party=None
split_candidate_label('सुदिव्य कुमार (झामुमो)')    → name='सुदिव्य कुमार',   party='झामुमो'
```

Real Form 20 headers carry the candidate's name, not `Name (ABBR)`. So `party_text` is None and `resolve_candidates` inserts the candidate with `party_id = NULL`. In the one case where the PDF *does* carry a party, it carries the Hindi name — and `resolve_candidates:342-343` builds its lookup from `abbr` and `name_en` only, **never `party.name_hi`**, which is seeded for all 13 parties.
*Consequence:* every candidate loads unattributed. `mv_result_booth_party` (`0009:12`) groups on `COALESCE(c.party_id, -1)`, so all candidates in a booth collapse into a **single** `party_id = -1` row holding the summed votes. In `mv_result_booth_wide`: `jmm`, `bjp`, `ajsu`, `jlkm` are all 0; `others` holds 100% of the votes; the `ranked` CTE sees exactly one row per booth, so `winner_party_id = -1`, `winner_party = NULL`, `runner_votes = NULL`, and `margin_votes = votes_total` giving **`margin_pct = 100.00` for every booth in the constituency**. The Results page, the map, the area rollup, the transfer view, the priority score and the scenario baseline are all built on this.
*Fix:* three things — add `party.name_hi` (and a Devanagari abbreviation alias table) to the lookup in `resolve_candidates`; match the parsed column against the candidates already seeded from `ac_totals.csv` by fuzzy name, not exact string; and refuse to load when more than N% of columns resolve to no party rather than silently bucketing them.

**C2 · Critical · `ingest/parse_form20.py:405-424` with `:286-303`**
`published_ac_totals()` keys its dictionary as `f"{name_en} ({abbr})"` — literally `'Sudivya Kumar (JMM)'`, built from the seeded `ac_totals.csv`. `validate()` then looks that exact string up in `dict(zip(doc.candidate_columns, …))`, whose keys are the raw header cells shown above.
*Consequence:* `totals.get(name)` is always None, so validation emits "AC total given for 'Sudivya Kumar (JMM)' but no such column in the document" for every seeded candidate, `errors` is non-empty, and `main()` returns 1 with `NOT LOADING`. The Form 20 load fails out of the box with a misleading message. The only way forward is `--skip-ac-check` (`:435`), which disables the entire booth-sum-vs-published-total cross-check — the thing the README calls "the gate to trust". So the realistic outcome is that the operator turns off the most important validation in the system in order to get any data in at all.
*Fix:* match on party abbreviation and fuzzy candidate name rather than an exact concatenated string, and make an unmatched AC total a loud warning about *reconciliation* rather than a hard failure, while keeping the sum comparison itself a hard gate once matched.

**C3 · Critical (privacy) · `ingest/extract_pdf.py:97, 110-111`, `:47-50`; `ingest/parse_roll.py:389-399`**
`extract_document` writes every page's complete extracted text to `ocr/<stem>-<sha12>/page_NNNN.json` before any parser runs:

```python
cached.write_text(json.dumps(pt.to_dict(), ensure_ascii=False), encoding="utf-8")
```

`pt.to_dict()` (`:36-44`) includes `"text": self.text` — the entire page. `scan_pdf` in `parse_roll.py:281` calls this on roll PDFs like any other document. There is no roll-specific exclusion.
*Consequence:* the full electoral roll — every elector's name, EPIC number, father's or husband's name, house number and age — is written to disk in plaintext JSON and kept indefinitely. `discard_raw()` then deletes the source PDF when `RETAIN_RAW_ROLLS=false`, which is the default, **leaving the extracted personal data in place while destroying the auditable original**. This directly contradicts `README.md:24-26` ("the names, EPIC numbers and addresses are discarded in memory"), LLD §12 ("No individual voter records anywhere in DB, logs, or LLM prompts"), and the compliance posture the whole design rests on. `validate.py:129-149` checks only `information_schema` column names in four tables, so it cannot see this. `tests/test_roll_privacy.py` tests the parser functions in isolation and never touches the cache.
*Fix:* pass a `cache=False` (or `sensitive=True`) flag from `parse_roll` so roll pages are never written to disk; if caching is needed for re-runs, cache only the derived counters. Delete the existing `ocr/` contents for any roll already processed. Extend `validate.py` with a filesystem check that no file under `OCR_DIR` matches the EPIC regex, and add that to the RUNBOOK's compliance section.

**C4 · High · `ingest/parse_form20.py:162-165`**
```python
votes = [parse_int(c) or 0 for c in body[:n_cand]]
votes += [0] * (n_cand - len(votes))
```
`parse_int` returns None for an unparseable cell (`textnorm.py:28-35`). `None or 0` is 0. So a cell that OCR mangled, a merged cell, or a dash becomes **zero votes**, not an error. The comment at `:163-164` argues the arithmetic check will catch it — but `validate()`'s row check at `:259` is guarded by `if row.total_valid is not None`. If the same page damage also cost the `total_valid` cell, the check is skipped entirely and the row loads with silent zeros.
*Consequence:* exactly the failure this system must not have — booth 147 loads with 0 votes for a candidate and nothing complains. With C2 forcing `--skip-ac-check`, the AC-level backstop is gone too, so the error survives all the way to the dashboard.
*Fix:* distinguish "parsed as 0" from "could not parse"; send any row containing an unparseable numeric cell to `review_queue` unconditionally, regardless of whether `total_valid` survived.

**C5 · High · `ingest/parse_form20.py:365-402`**
The docstring says "Whole thing in one transaction." It is three. `with connection()` at `:369` does the `DELETE`s and commits on exit. `resolve_candidates` opens its own connection at `:339` and commits. A third opens at `:382` for the row inserts. psycopg_pool's `connection()` context manager commits on clean exit.
*Consequence:* if the insert loop fails partway — the `strict=True` zip at `:386`, a constraint violation, a dropped connection — the `DELETE FROM result_booth` has already committed and some fraction of the rows have been written. The election is left with partial results and no error state recorded. With `--replace`, a failed reload leaves the election emptier than it started.
*Fix:* open one connection at the top and pass the cursor down through `resolve_candidates` and the insert loop.

**C6 · High · `ingest/parse_form20.py:354-360`; `db/migrations/0003_elections.sql:31`**
`resolve_candidates` upserts with `ON CONFLICT (election_id, name_en, party_id) DO UPDATE`. That unique constraint is declared with PostgreSQL's default NULLS DISTINCT semantics, and by C1 **every** Form 20 candidate has `party_id = NULL`.
*Consequence:* the conflict never fires. Every re-run of `parse_form20 --load` inserts a **fresh set of candidate rows**. Old `result_booth` rows are not deleted (unless `--replace`), so `mv_result_booth_party`'s `SUM(r.votes)` adds the old and new candidates together and **every booth's vote total doubles on the second run, triples on the third**. A re-parse after a parser fix — the most ordinary operation in this pipeline — corrupts the data.
*Fix:* declare the constraint `UNIQUE NULLS NOT DISTINCT (election_id, name_en, party_id)` (PG15+), or key candidates on `(election_id, column_index)` which is what the Form 20 actually identifies them by.

**C7 · High · `ingest/parse_roll.py:201-217`**
Electors are counted by EPIC matches per line (`:202`), but gender, age and name come from `_attrs_for`, which reads the same line and widens forward by one. Rolls are commonly laid out with the EPIC on one line and the name/relation/house/age/gender block on following lines, or in three columns where the attributes do not align with the EPIC count.
*Consequence:* when the layout does not cooperate, `genders[k]` is out of range, `_gender_bucket(None)` returns `"other"`, and **every elector is counted as gender "other"** while `add_age(None)` returns immediately so **all six age bands stay 0**. The elector total is right; the entire gender and age breakdown is silently zero. Nothing validates that `male + female + other == electors` with a plausible split, or that the age bands sum to the elector count — `validate.py` has no roll-composition check at all. HLD module 4 specifies an age-band split and gender ratio as deliverables.
*Fix:* add a `validate.py` check asserting `age_18_19 + … + age_60p` is within a tolerance of `electors` and that `other / electors < 0.02`, and make `scan_mother_roll` raise (or queue) when the attribute count for a page group falls far short of the EPIC count.

**C8 · Medium · `ingest/parse_roll.py:233-241` with `:282-298`**
`scan_pdf` groups pages by the PS number in each page header and calls `scan_supplement` separately per group, with `section` starting at None each time. `scan_supplement` skips every line until it sees a `परिवर्धन`/`विलोपन`/`संशोधन` heading (`:240-241`).
*Consequence:* if a supplement prints the section heading once at the front and then runs through many polling stations, only the first PS group sees the heading. Every subsequent group scans with `section is None` and **records 0 additions, 0 deletions, 0 modifications** — a parse failure that produces zeros, not an error. The `--dry-run` summary would show a suspiciously low total but nothing fails.
*Fix:* carry the section state across groups in `scan_pdf`, and refuse to load a supplement whose additions total is zero for more than a threshold share of sections.

**C9 · Medium · `ingest/parse_roll.py:73-76, 258-266`**
`reason = _deletion_reason(window)` is computed once per line and applied to all `n_entries` on that line. The patterns include bare `\bE\b` and `\bS\b`.
*Consequence:* a line carrying three deletions with different reason codes attributes all three to the first match. `\bS\b` matches the `S` in `S/O`, which appears in most roll lines, so deletions will be systematically over-classified as "shifted". `del_death` / `del_shifted` / `del_other` are unreliable, which matters because HLD §4 makes deletions a first-class metric under SIR.
*Fix:* parse the reason code per entry from its own column; drop the single-letter patterns or anchor them to a reason-code field.

**C10 · Medium · `ingest/crosswalk.py:193-209` with `:89-104`**
`anchor_stations()` builds `Station` objects without `roll_part` (the `booth` table does not carry it), so `old.roll_part is not None and new.roll_part is not None` is always False.
*Consequence:* the `W_PART = 0.20` roll-part term in the documented LLD §4.4 formula **never contributes**. Every score is `(0.5·building + 0.3·place) / 0.8`, i.e. 62.5% building / 37.5% place. The docstring at `:5-8` and the README both advertise the three-component formula. The renormalisation (well reasoned in the comment at `:79-87`) is doing all the work, and matching relies entirely on two text fields.
*Fix:* carry `roll_part` onto `booth` at anchor time and include it in `anchor_stations()`.

**C11 · Medium · `ingest/validate.py:82-88`**
`crosswalk_quality` computes `COUNT(*) FILTER (WHERE confidence >= 0.85) / COUNT(*)` over `booth_crosswalk`. By B2, review-band stations have **no row in that table**.
*Consequence:* they are absent from both numerator and denominator. Given 300 stations where 250 auto-accept and 50 land in the review band, the check reports 100% auto-matched against the 90% target — while 17% of the constituency has been dropped from every view. The one check aimed at crosswalk health is defeated by the crosswalk bug.
*Fix:* compute the denominator from `ps_list_entry` for that election, not from `booth_crosswalk`.

**C12 · Medium · `ingest/validate.py:106-126`**
`check_roll_continuity` joins consecutive `roll_revision` rows and flags booths present in one snapshot but absent from the next. Supplement revisions write `roll_change` rows only — never `roll_snapshot` (`parse_roll.py:349-377` branches on `supplement`).
*Consequence:* every supplement revision has zero snapshot rows, so **every booth from the prior mother roll is reported missing**. The check fails loudly and constantly on correct data, which trains operators to ignore validate output — the opposite of what a validation gate is for.
*Fix:* restrict the window to `WHERE is_mother`.

**C13 · Medium · `ingest/parse_roll.py:389-399`**
`discard_raw` deletes the source PDF by default. Combined with C3, the auditable original is destroyed while the extracted personal data is retained.
*Consequence:* you cannot re-parse to verify a count or fix a parser bug, and you cannot prove to a regulator what the source said — but you are still holding the personal data.
*Fix:* fix C3 first; then consider keeping the raw PDF with restrictive permissions, which is what LLD §12 actually describes ("stored in `raw/` with filesystem permissions to `worker` only").

**C14 · Medium · `db/seed/surname_dict.csv`; `analytics/caste_estimate.py:58-62, 267`**
The dictionary holds 176 rows against the LLD §5 target of ~300, and the distribution is badly skewed relative to Giridih's politics: 26 Muslim, 19 Brahmin, 18 Baniya, but only **6 Kurmi (Mahato)** and 5 Yadav. Kurmi/Mahato is the single most electorally consequential community in the HLD's analysis (the JLKM/Jairam Mahato dynamic). `_as_pct` normalises over matched tokens only, and `refresh()` then extrapolates: `est_count = electors × pct / 100`.
*Consequence:* unmatched surnames are dropped, so percentages are shares of the matched subset presented as shares of the booth. Where dictionary coverage is uneven by community — as it is — the extrapolation is biased, systematically understating Kurmi share and overstating the well-covered communities, and then multiplying that bias by the full electorate to produce an `est_count`. The confidence score captures *how much* was matched but not *that the matched sample is unrepresentative*.
*Fix:* expand the Kurmi/Yadav/SC spellings toward parity, and either report `est_pct` explicitly as "share of matched surnames" or model the unmatched residual as its own bucket rather than assuming it distributes proportionally.

**C15 · Medium · traceability**
`result_booth_meta` records `source_doc` and `source_page` per row (`parse_form20.py:400`), and `ps_list_entry` records both (`parse_pslist.py:166`) — good, and surfaced through `mv_result_booth_wide` and the booth card. But **`roll_snapshot` and `roll_change` record neither**: `db/migrations/0004_rolls.sql` has no source columns beyond `roll_revision.source_doc`, which `parse_roll.load()` never populates (`:326-332` omits it).
*Consequence:* a suspicious additions figure for a booth cannot be traced to a document and page. `roll_revision.source_doc` is NULL for every revision.

**C16 · Low · `ingest/crosswalk.py:245-251, 279-285`**
Both the review and new branches `INSERT INTO review_queue` with no dedupe. Running `crosswalk --apply` twice doubles the queue; the RUNBOOK explicitly instructs re-running after fixes.

**C17 · Low · `ingest/parse_form20.py:216-222`**
Pages that produced *some* rows via the table path are excluded from the regex fallback (`covered` is a page-number set). A page whose table extraction returned half its rows never gets a second pass; the missing PS numbers surface only through the gap check at `:275-284`.

**Idempotency, by loader:**

| Loader | Re-run behaviour |
|---|---|
| `extract_pdf` | **Clean** — content-addressed cache by sha256, skips work |
| `parse_form20` | **Corrupts** — duplicate candidates, votes double (C6) |
| `parse_pslist` | Clean for `ps_list_entry` and `booth` upserts; **dangerous** on re-anchor (B5) |
| `crosswalk` | Crosswalk rows upsert safely and respect `reviewed=true`; **review_queue duplicates** (C16); **UID collisions across elections** (B3) |
| `parse_roll` | **Clean** — upserts on `(revision_id, booth_uid)`, single transaction |
| `caste_estimate` | **Clean** — full recompute, upsert on the natural key |
| `load_seed` | **Clean** — upserts throughout; `result_ac_total` uses delete-then-insert with a documented reason |
| `news.crawl` | **Clean** — `url_hash` + simhash + `ON CONFLICT (url) DO NOTHING` |
| `news.label_batch` | **Clean** — `pending()` filters `batch_id IS NULL`. Narrow window: a crash between `batches.create` (`:106`) and the `UPDATE` (`:108`) pays for a batch that will be resubmitted |
| `analytics.refresh` | **Clean** |

---

### D. Analytics correctness

#### Worked example: margin %, by hand against the published 2024 result

HLD §1.1 gives VS-2024: JMM 94,042 · BJP 90,204 · JLKM 10,787 · NOTA 2,004 · margin 3,838 = **1.85%**.

Reconstructing the ECI's denominator: 94,042 / 0.453 = 207,598 total valid votes, and 3,838 / 207,598 = **1.849%** ✓. So the published convention is margin as a share of **total valid votes, NOTA included**.

Now the code. `mv_result_booth_wide:114`:
```sql
margin_pct = ROUND(100.0 * (w.votes - COALESCE(ru.votes,0)) / NULLIF(pv.votes_total,0), 2)
```
where `votes_total` sums `mv_result_booth_party`, which is built from `result_booth` — the candidate columns only. I verified by execution (§C1) that **NOTA is always classified as a tail value, never a candidate column**, in both the English and Devanagari header layouts. It therefore never becomes a `candidate` row, never enters `result_booth`, and never enters `votes_total`. The `NOTA` party seeded in `parties.csv:13` and the `FILTER (WHERE pa.abbr = 'NOTA')` bucket at `0009:77` are dead code.

So the code computes `votes_total` = 207,598 − 2,004 = **205,594**, and

  margin_pct = 100 × 3,838 / 205,594 = **1.867%**  vs the published **1.849%**

**D1 · Critical · `db/migrations/0009_mv_results.sql:96, 106, 114, 115`**
The same row exposes three mutually inconsistent totals:
- `votes_counted` = `pv.votes_total` = 205,594 (candidates only)
- `total_valid` = `COALESCE(m.total_valid, pv.votes_total)` = 207,598 (from Form 20's printed total, which includes NOTA — confirmed by `validate.py:57`'s own arithmetic `SUM(votes) + nota = total_valid`)
- `nota` = `COALESCE(pv.nota_votes, m.nota, 0)` = 2,004, sourced from the meta fallback

*Consequence:* `margin_pct` and `turnout_pct` divide by the **smaller** total while the row displays the larger one, so the constituency's headline margin comes out at 1.87% where the ECI publishes 1.85%. At booth level the same 1% relative error applies to every margin, every turnout and every `share_pct` in `mv_booth_party_share`, and it propagates into `mv_swing` (a difference of two slightly-wrong shares), `mv_floating_vote` (Pedersen index of those shares) and `mv_booth_priority`. Separately, a user reading a row cannot reconcile it: `jmm + bjp + … + others + nota ≠ votes_counted`, off by exactly the NOTA count. The CSV export has the same defect.
*Fix:* decide one denominator — "total valid votes including NOTA", matching ECI — and use it everywhere. Simplest route: load NOTA as a real candidate row with `party_id = NOTA` so it flows through `result_booth` naturally, which also revives the existing `nota_votes` pivot. Then add a `validate.py` check asserting the AC-level `margin_pct` reproduces the published figure to 2 decimal places.

**D2 · High · `db/migrations/0010_mv_analytics.sql:15-16`**
```sql
ROUND(s.share_pct - COALESCE(LAG(s.share_pct) OVER w, 0), 2) AS swing_pct
```
For the earliest election of a given type at a given booth there is no preceding row, so `LAG` is NULL and the `COALESCE` substitutes 0.
*Consequence:* the first loaded election reports a swing equal to the party's **entire vote share** — a fabricated +38.3 point BJP "swing" in VS-2014 at every booth. `swing_votes` is likewise the full vote count. `prev_election_id` is NULL and would reveal this, but nothing in the API, the views or `metrics.sql` filters on it, and `mv_swing` is on the chatbot's SQL allow-list. The same applies to any booth created by a split, whose earlier year genuinely has no predecessor.
*Fix:* emit NULL rather than 0 when `LAG` is NULL, and only zero-fill when the party genuinely contested the prior election with zero votes.

**D3 · High · `db/migrations/0009_mv_results.sql:82-86, 107-114`**
The `ranked` CTE ranks rows of `mv_result_booth_party`, whose `party_id` is `COALESCE(c.party_id, -1)`. That `-1` bucket is the **sum of all unattributed candidates** in the booth, not one candidate.
*Consequence:* wherever independents or unresolved columns exist, the combined bucket competes for winner and runner-up. Eight independents on 500 votes each become a single 4,000-vote "party" that outranks a winner on 3,000, producing `winner_party = NULL` and a margin measured against a candidate who does not exist. Under C1 this is the universal case, not an edge case. There is no `party` row with id −1, so the `LEFT JOIN party` at `:124` yields NULL and the UI renders "—" as the winner.
*Fix:* rank only rows with a real `party_id`, or preserve `candidate_id` granularity in the ranking CTE so independents compete individually.

**D4 · Medium · `db/migrations/0011_mv_priority.sql:6-11`**
`mv_floating_vote` is the Pedersen index over `mv_transfer_ls_vs`, which `FULL OUTER JOIN`s LS and VS of the same year. When a year has results for only one of the two poll types, every party's delta equals its full share in the one poll that exists, so `SUM(ABS(delta))/2 = 100/2`.
*Consequence:* **every booth reports `floating_pct = 50.00`** — a striking and entirely artificial number, displayed on the map (`MapExplorer.tsx:22`), in `/transfer`, on the booth card and in the priority table. The README's Phase 0 (`README.md:71-101`) never mentions loading a PS list and crosswalk for `LS-2024 (AC seg)`, so this is the *expected* first state, not a corner case. Because the value is uniform, `PERCENT_RANK` flattens to 0 and the priority score loses its 0.20 floating-vote term silently.
*Fix:* emit NULL when only one poll type exists for a year, and have `validate.py` warn when an LS election has results but no crosswalk.

**D5 · Medium · `analytics/scenario.py:181`**
```python
winner=jmm if point_margin >= 0 else bjp
```
`contest` is hardcoded to `("JMM", "BJP")` at `:58` and never overridden by the API.
*Consequence:* a scenario that moves most of the vote to JLKM or AJSU still reports JMM or BJP as the winner. The `votes` dict returned alongside it would show the real leader, so the response contradicts itself, and `Scenario.tsx:76` renders "{winner} ahead" with the wrong party and the wrong colour.
*Fix:* take the argmax of `point_totals` excluding NOTA, and keep `contest` only for choosing which pair the margin is measured between.

**D6 · Medium · `analytics/scenario.py:150-157`**
Monte-Carlo noise multiplies each destination share by `(1 ± noise)` and then calls `_normalised_row`, which rescales the row back to sum 1.
*Consequence:* for any party with no explicit transfer row — i.e. an identity row `{party: 1.0}` — the perturbation is **exactly cancelled** by the renormalisation. Only multi-destination rows carry real uncertainty. The frontend always sends `jlkm_to_bjp` (`Scenario.tsx:34`), so in practice the JLKM row is the sole source of transfer variance: with JLKM on ~10,787 votes and a 50/50 split at ±5%, that is roughly ±540 on the margin, plus ±192 from the turnout multiplier scaling the 3,838 base. The resulting P10–P90 band of roughly ±700 looks empirically derived but is almost entirely an artifact of one arbitrary `noise=0.05` constant applied to one row.
*Credit where due:* `noise`, `draws`, `turnout_multiplier`, `sympathy_swing`, `new_voter_turnout` and the whole transfer matrix are **parameters, not hardcoded constants** (`ScenarioBody`, `scenario.py:14-27`); the response echoes every assumption back (`:185-192`); the disclaimer is prominent and accurate; and `Scenario.tsx:83-84` explicitly warns when the range crosses zero. This is the most honestly presented module in the codebase. The problem is narrow: the band's *width* is not meaningful, and it is displayed next to a point estimate as though it were.
*Fix:* apply noise in log-odds or to the source total before renormalising, and label the band as a sensitivity range at the stated noise level rather than a confidence interval.

**D7 · Medium · `api/routers/data.py:231-232`**
```sql
LEFT JOIN roll_snapshot s ON s.booth_uid = c.booth_uid AND s.revision_id = c.revision_id
```
`c` is `roll_change`, written only for supplements; `roll_snapshot` is written only for mother rolls (`parse_roll.py:349`), and `is_mother = not supplement` means a single `revision_id` can never be both.
*Consequence:* `s.electors` is always NULL, so `additions_pct` and `deletions_pct` are **always NULL** and the `electors` column is always blank on the Voters page (`Voters.tsx:67, 69, 71`). This is HLD module 4's "share of the electorate" figure. Note it uses a *different* denominator from `mv_new_voter_share` (`0010:106`), which correctly uses the latest snapshot — so the two views of the same metric disagree by construction, and both are currently unusable (this one NULL, the other zero per B1).
*Fix:* join the most recent mother-roll snapshot at or before the change revision's date.

**D8 · Low · `analytics/caste_estimate.py:112-121`**
`_rescale_category` sets the SC/ST members to the blended target, then lines 119-121 renormalise the whole distribution to 100.
*Consequence:* the rescaling is partly undone. Surname SC = 10%, census SC = 30% → target = 0.7·10 + 0.3·30 = 16%; after rescaling the total is 106, and renormalising gives SC = 15.1%, not 16%. The documented formula is not quite what is computed. Currently moot because no census data can be loaded (B8).

**D9 · Low · `db/migrations/0010_mv_analytics.sql:5-18`**
A party that contested in the earlier election but not the later one produces no row in the later election, so its negative swing never appears. JVM is exactly this case (merged into BJP in 2020). A 2019→2024 swing table will show BJP's gain with no corresponding JVM collapse. The header comment documents the opposite direction only.

**Denominators — where they are right.** `mv_booth_party_share.share_pct`, `mv_area_rollup.jmm_pct/bjp_pct`, and `mv_result_booth_wide.margin_pct` all consistently use votes counted, and `mv_new_voter_share.new_voter_pct` correctly uses electors, matching the HLD definition. Division by zero is guarded with `NULLIF` everywhere it matters (`0009:39, 114, 115`; `0010:106, 107`; `0011:111-113`; `data.py:231-232`; `metrics.sql:49, 68`). The booth priority weights sum to exactly 1.0 and match the formula string the API returns (`0011:70-81` vs `data.py:396-397`). These are genuinely careful. The problems are the three inconsistent totals in D1 and the two inputs that are currently constant.

---

### E. API layer

| Method | Path | Auth | Role | Block-scoped | Notes |
|---|---|---|---|---|---|
| GET | `/health` | none | — | — | DB probe only, no detail leaked |
| POST | `/auth/login` | none | — | — | phone + password → JWT. No rate limit (E4) |
| POST | `/auth/otp` | none | — | — | 202 always; sender is a stub (E7) |
| POST | `/auth/verify` | none | — | — | ≤5 attempts, hashed code, `compare_digest` |
| GET | `/auth/me` | JWT | all | — | |
| GET | `/summary` | JWT | all | **no** | AC-wide headline + data_health to block users (E8) |
| GET | `/booths` | JWT | all | yes (`a.block_id`) | GeoJSON; ignores `election_label` (F2) |
| GET | `/booths/{uid}/card` | JWT | all | yes (post-build 403) | caste included iff `sees_caste` |
| GET | `/results/{label}/booths` | JWT | all | yes (`w.block_id`) | `?format=csv`; no pagination |
| GET | `/results/{label}/areas` | JWT | all | yes | `SELECT *` from the rollup MV |
| GET | `/rolls/changes` | JWT | all | yes (`a.block_id`) | `additions_pct` always NULL (D7) |
| GET | `/rolls/revisions` | JWT | all | n/a | |
| GET | `/caste` | JWT | **strategist+** | n/a (role-gated) | confidence + disclaimer in payload |
| GET | `/transfer` | JWT | **strategist+** | n/a | |
| GET | `/local-results` | JWT | all | yes (fails closed on NULL area) | |
| GET | `/priority` | JWT | all | yes | `limit` capped at 500 |
| GET | `/areas` | JWT | all | yes | blocks + areas + elections + parties |
| GET | `/news` | JWT | all | no (AC-wide, appropriate) | vector rank when `q` given |
| GET | `/news/issues` | JWT | all | no | |
| POST | `/ground-reports` | JWT | all | — | free text ≤4000 chars (E5) |
| GET | `/ground-reports` | JWT | all | yes (fails closed) | |
| POST | `/chat` | JWT | all | **no** | SSE; see E2 |
| POST | `/chat/sync` | JWT | all | **no** | |
| POST | `/scenario` | JWT | **strategist+** | n/a | |
| GET/POST | `/admin/review-queue`, `/admin/crosswalk`, `/admin/surnames` | JWT | **admin** | — | |
| GET | `/admin/usage`, `/admin/jobs`, `/admin/sources` | JWT | **admin** | — | |

**SQL injection: none found.** Every f-string in `api/routers/` and `analytics/scenario.py` interpolates only fixed clause literals built in code (`"a.block_id = %s"`, `"w.election_label = %s"`); all user values go through psycopg's `%s` parameters. `metric` in `/booths` is checked against an allow-list *and* a `^[a-z_]{3,32}$` pattern and is then used only as a Python dict key, never in SQL. `format` and `source` are constrained by regex. `db/apply_migrations.py:98` correctly uses `pgsql.Literal` for the role password. I looked specifically for string-concatenated values and found none.

**E1 · High · `api/deps.py:83-84` with `api/main.py:28-29`**
`jwt.decode(creds.credentials, settings.jwt_secret, …)`. `jwt_secret` defaults to `""` (`config.py:66`). On startup, a missing secret produces `log.error("JWT_SECRET is not set - every authenticated request will fail")` and the app **starts anyway**. The claim in that message is wrong: `make_token` refuses to mint (`:69-70`), but HS256 verification with an empty key is valid HMAC, so decoding succeeds.
*Consequence:* if `JWT_SECRET` is left unset, anyone can sign their own token offline with an empty key and `{"sub": "1", "role": "admin"}`. `current_user` re-reads the role from the database (good — a forged role claim alone is useless), but `sub=1` is the admin created by `create_admin.py` in README step 4. Full admin access to booth results, caste estimates and the review queue. `.env.example:18` also ships the placeholder `change-me-64-random-hex`, which an operator may well leave in place.
*Fix:* refuse to start when `jwt_secret` is empty, shorter than 32 bytes, or equal to the placeholder. Raise in the `lifespan` handler rather than logging.
*What I'd verify:* python-jose is not installed in the dev venv so I could not execute this. Confirm with `jwt.decode(jwt.encode({"sub":"1"}, "", algorithm="HS256"), "", algorithms=["HS256"])`.

**E2 · Medium · `chatbot/tools.py:188-192` (via `POST /chat`, open to all roles)**
`tool_get_booth_card` calls `build_booth_card(booth_uid)` with `include_caste` defaulting to True (`booth_card.py:12`), and applies no block filter.
*Consequence:* `/chat` is available to every role. A block-role user — from whom the caste module is deliberately hidden (`deps.py:49-51`, LLD §12) and who is confined to their own block everywhere else — can ask the assistant for any booth card in the constituency and receive its community estimates. The role check exists on `/caste` and the 403 exists on `/booths/{uid}/card`, but the chat path reaches the same builder with neither. Gated behind the parked chatbot, so it is not live today.
*Fix:* thread the caller's `User` through `ask()` into the tool dispatch, and apply `sees_caste` and `scoped_block_id` there.

**E3 · Medium · `api/routers/chat.py:50-53`**
The SSE error event returns `"detail": str(exc)[:200]`. Everywhere else, `main.py:56-63` correctly logs the exception and returns a flat "Internal error" — no stack traces, no DB detail. This one path bypasses that.
*Consequence:* a psycopg error surfaces its message — which can include table and column names and fragments of SQL — to the browser.

**E4 · Medium · `api/routers/auth.py:55-70`**
No rate limiting, lockout or backoff on `/auth/login`. `auth_otp` has a 5-attempt cap but the password path has nothing.
*Consequence:* unlimited online password guessing against an internet-facing endpoint (over plain HTTP, per A2). Also `request_otp` inserts an unbounded number of `auth_otp` rows. Minor timing side channel: `user is None or not verify_password(...)` short-circuits, so unregistered phones answer measurably faster, enabling enumeration despite the identical message.

**E5 · Medium · `api/routers/news.py:110-122`**
`POST /ground-reports` accepts 4,000 characters of free text from any authenticated user and stores it verbatim; `news/embed.py:72-86` then embeds it into pgvector, and it becomes retrievable through `search_news`.
*Consequence:* this is the only free-text ingress in the system and it has no PII screening. A booth in-charge pasting voter names, phone numbers or EPIC numbers into a report puts exactly the data class the design forbids into the database and the vector index. The docstring at `:114-115` asserts reporters "write about places and conditions, not about named individuals" — an assumption, not a control. `validate.py:129-149` does not inspect `ground_report`. To the system's credit, `giridih_ro` has no grant on `ground_report` (`0012:24-49`), so the chatbot's raw SQL cannot read it.
*Fix:* regex-screen submissions for EPIC and phone patterns and reject with a clear message; add a `validate.py` check that scans `ground_report.text`.

**E6 · Low · no pagination**
`/results/{label}/booths`, `/results/{label}/areas`, `/rolls/changes`, `/caste`, `/transfer`, `/local-results`, `/areas` all return the full result set. `/transfer` is ~350 booths × ~10 parties ≈ 3,500 rows; `/rolls/changes` with no revision filter returns every revision × every booth. Bounded by the small dataset, so low impact, but every one of these loads its whole table into memory and serialises it. No N+1 patterns anywhere — the queries are well-shaped single statements.

**E7 · Low · `api/routers/auth.py:125-132`**
`_send_sms` never sends anything; it logs and returns. `SMS_API_KEY`/`SMS_SENDER_ID` are in `.env.example` but read by nothing. `verify_otp` does not check `auth_mode`.
*Consequence:* setting `AUTH_MODE=otp` produces a system nobody can log into, with `{"sent": true}` returned to the caller.

**E8 · Low · `api/routers/data.py:36-72`**
`/summary` applies no block filter; it returns AC-wide election totals, baseline party votes, and `data_health` (open review count, weak crosswalk count) to block-role users. Arguably intended for headline numbers; the review-queue counts are an internal signal that a block in-charge has no need for.

**E9 · Low · `api/routers/data.py:32`**
`Content-Disposition` is built from `election_label`, an unsanitised path parameter. Starlette/h11 will reject genuinely malformed header values, so this is not an injection in practice, but user input reaches a response header unfiltered.

---

### F. Frontend

The build is clean: `tsc -b && vite build` succeeds with no errors or warnings, 794 modules, sensible manual chunks (react 181 kB, charts 406 kB, map 154 kB — all lazy). Routes are code-split (`App.tsx:9-18`). Every page has explicit `Loading`, `ErrorState` and `Empty` components (`States.tsx`), and the empty states name the exact CLI command needed to fix the gap — genuinely good. i18n is properly wired: i18next with Hindi as the default, English on a toggle, `<html lang>` kept in step so the Devanagari font stack applies (`i18n/index.ts:28-31`, `index.css:9-13`). Numbers use `Intl.NumberFormat('en-IN')`, so 3,04,898 renders with correct lakh grouping (`format.ts:7`). API base is `import.meta.env.VITE_API_BASE ?? '/api'` — relative and configurable, no hardcoded localhost in the production path (the localhost proxy is confined to `vite.config.ts:10`, dev only). **No secrets, keys or tokens anywhere in `web/src` or the built bundle.**

**F1 · High · `web/src/pages/MapExplorer.tsx:52-57` with `lib/tokens.ts:59-65`**
```tsx
const colourFor = (value: number | null) =>
  spec.diverging
    // margin_pct is unsigned in the view; sign it by who won so the ramp can
    // read as "JMM lead" on one side and "BJP lead" on the other.
    ? divergingColor(value, spec.max)
```
The comment states the requirement and the code does not implement it. `margin_pct` from `mv_result_booth_wide:114` is `winner − runner`, always ≥ 0. `divergingColor` maps −saturateAt → index 0, 0 → index 5, +saturateAt → index 10, so an always-positive input only ever reaches indices 5–10.
*Consequence:* the map's default and most important view uses **half the diverging ramp**. Every booth renders somewhere between neutral grey and strong "BJP lead" red, regardless of who actually won. A JMM-held booth on a 12-point margin and a BJP-held booth on a 12-point margin are the same colour. The `DivergingLegend` below it (`:79`) tells the reader the two arms mean opposite things. This is the primary visual the strategy team will look at. `winner_party` is already in `properties` (`:31`), so the fix is one line.
*Fix:* `divergingColor(p.winner_party === 'JMM' ? -value : value, spec.max)`, generalised to whichever pair the contest is between.

**F2 · Medium · `api/routers/data.py:75-135`, `web/src/pages/MapExplorer.tsx:17-23`**
`/booths` accepts `election_label`, echoes it into `meta` at `:134`, and **never uses it in the query** — all metrics come from `mv_booth_priority`, which is baseline-only by construction (`0011:16-29`). The map UI offers a metric selector and nothing else: no year, no block, no area.
*Consequence:* HLD module 2 specifies filters for "Block, panchayat/ward, metric, year". Three of the four do not exist, and the year parameter that does exist is silently ignored — a caller passing `election_label=VS-2019` gets 2024 numbers back with `"election_label": "VS-2019"` in the response metadata.

**F3 · Medium · `web/src/pages/MapExplorer.tsx:102, 139`**
`radius = max(4, min(11, sqrt((p.electors ?? 400) / 40)))`. Per B4, `electors` is always NULL, so every marker computes `sqrt(10) = 3.16` → clamped to 4.
*Consequence:* all markers are the same minimum size while the caption asserts "Marker size is the electorate". The map states something false about the data.

**F4 · Medium · `web/src/pages/Caste.tsx:42-51, 118`**
The scatter sets `y: 0` for every point and hides the Y axis. It plots estimated community share against a constant.
*Consequence:* this is a one-dimensional strip plot, not the "scatter: e.g. Kurmi % vs JLKM+AJSU %" that HLD module 5 specifies. The caption at `:134-137` carefully warns the reader how to interpret a relationship with vote share — but vote share is never on the chart. The ecological correlation exists as SQL in `metrics.sql:67-78` and is exposed by no endpoint. The caste × vote overlay, one of three "key focus" modules, is not built.
*To its credit:* confidence is carried end to end — the API returns it (`data.py:273`), the table renders it, and values under 0.4 are greyed and labelled `अनुमान अपर्याप्त` (`:64-66`). The frontend does **not** receive bare numbers.

**F5 · Low · `web/package.json:22-24`, `web/src/components/ChatPanel.tsx:41, 79, 85`**
`vega`, `vega-lite` and `vega-embed` are declared and never imported. `ChatPanel` collects `chart` SSE events into `charts: unknown[]` and never renders them.
*Consequence:* `make_chart` (`chatbot/tools.py:201-207`) produces Vega-Lite specs that reach the browser and are discarded. Half-wired, but harmless — it does not break the build (confirmed) or any migration.

**F6 · Low · `web/src/lib/api.ts:23`**
The JWT is stored in `localStorage`, readable by any script on the origin. Standard trade-off for an SPA; worth noting given a 12-hour token TTL (`config.py:69`) and no TLS (A2).

**Console errors on the main routes:** not observed directly — that needs a running API. The build produces no TypeScript or bundler diagnostics, all queries are guarded by `isLoading`/`isError`, and a failed fetch renders `ErrorState` with a retry rather than a blank page, so a backend outage degrades correctly. **Booths with no geocode** are filtered out of the map (`:47-50`) and surfaced as a counted warning with the remediation command (`:84-89`) — a good pattern.

---

### G. Operations and security

**Secrets: clean.** `.gitignore` correctly excludes `.env`, `.env.local`, `raw/**`, `ocr/**`, `backups/**` and `pgdata/`. `.env.example` contains only obvious placeholders (`change-me-strong`, `change-me-64-random-hex`, `change-me-ro`) and no live credential. No API key, token or password appears anywhere in the tracked source, and nothing is embedded in the frontend bundle. **Git history cannot be checked because there is no git repository** (A6).

**G1 · High · `worker/ops.py:19-51`; `docker-compose.yml:115-121`**
`ops.backup` writes `pg_dump | gzip` to the `backups` Docker volume and prunes past `BACKUP_RETENTION_DAYS`. That volume lives on the same host as `pgdata`. LLD §7 specifies "pg_dump → volume **+ optional S3/B2**"; the off-host half is not implemented, and `docs/RUNBOOK.md:76-77` acknowledges it in prose ("Copy backups off the host as well — a VPS failure takes the volume with it") without providing a mechanism.
*Consequence:* there is effectively no disaster recovery. A VPS loss, a volume corruption or a `docker compose down -v` takes the database and all 14 days of backups together. For a system whose Phase 0 represents weeks of manual PDF reconciliation, that is the single most expensive thing that can go wrong. The restore drill is documented (`RUNBOOK.md:64-74`) but has never been run and is not scripted or tested.
*Fix:* add a B2/S3 upload step to `backup()` (a few lines with the existing `httpx` dependency, or `rclone` in the worker image), and turn the drill into `scripts/restore_drill.sh` so it can be run and its date recorded.

**G2 · Medium · `worker/ops.py:34-37`**
```python
subprocess.run(["pg_dump", "--no-owner", "--no-acl", settings.database_url], ...)
```
The full connection URI, password included, is passed as `argv[3]`.
*Consequence:* the database password is visible in the container's process table to anything that can read `/proc` — any other process in that container, and `docker top`. Also likely to land in any process-level monitoring.
*Fix:* pass `PGPASSWORD` in `env=` and give `pg_dump` the host/port/db/user separately, or write a `.pgpass`.

**G3 · Medium · logging**
`common/logging_setup.py:17-23` configures a single plain-text stdout handler with a fixed format. No JSON, no request id, no correlation id, no log rotation configured anywhere (Docker's default json-file driver will grow unbounded without a `logging:` block in compose).
*Consequence:* an incident during polling week is debugged by grepping unstructured text across three containers with no way to correlate a user request to the queries it ran.
*On the positive side:* I found **no logging of sensitive data**. `_send_sms` logs only the last four digits and never the code (`auth.py:129, 132`). The roll parser logs only counts (`parse_roll.py:428-438`). `job_run.log` mirrors WARNING+ records (`common/jobs.py:66-75`) and is exposed via `/admin/jobs`, but the warnings the parsers emit contain building names and PS numbers, not personal data. `llm_prompt_log` is purged on a 7-day schedule as designed.

**G4 · Medium · job failure surfacing**
Jobs are idempotent (see the table in §C) and every one can be run manually — `python -m worker.run <name>`, backed by a shared registry so the manual path runs exactly what the scheduler runs (`worker/jobs.py:81-92`). Failures are recorded: `job_context` marks the row `failed`, captures the traceback, and `run_job` keeps the scheduler alive (`:107-109`). `/admin/jobs` exposes it. That is all good. What is missing is **notification** — `usage_report` "warns admin" with a `log.warning` (`ops.py:94`) and nothing else. There is no email, SMS or webhook anywhere.
*Consequence:* a failed nightly `ops.backup` or `analytics.refresh` is discovered only when someone opens `/admin`. The RUNBOOK marks both of those "**Urgent**".

**G5 · Low · `common/jobs.py:89-96`**
`already_done()` implements the LLD §7 idempotency-key check and **is never called**. `label_batch.main` records an `idempotency_key` in `job_run.meta` (`:124`) that nothing reads — and the scheduler calls `submit()` directly, bypassing even that. Actual idempotency comes from each job's own query predicates, which is fine, but the declared mechanism is dead.

**G6 · Low · `scripts/demo_api.py:29-31`**
`allow_origins=["*"]` together with `allow_credentials=True` — a combination browsers reject, and unsafe if it ever worked. Not referenced by `docker-compose.yml`, clearly labelled synthetic, and cannot be deployed by accident, so the practical risk is nil.

**Dependency vulnerabilities.** Pins are recent and deliberate (both requirements files carry a "checked against PyPI on 2026-09-20" note). `python-jose 3.5.0` is past the fixes for CVE-2024-33663 (algorithm confusion) and CVE-2024-33664 (JWT bomb) — good. The one to watch is **`passlib==1.7.4`**, which has had no release since 2020 and is effectively unmaintained; with `bcrypt` 4.1+ it emits a version-detection error internally, and it is the library standing between your password hashes and an attacker. Consider moving to `pwdlib` or calling `bcrypt` directly. No other obviously vulnerable pin.

---

### H. Testing

96 tests, all passing, in 0.44 s, with no database required. They are well written — real Devanagari fixtures, meaningful assertions, and names that state the invariant rather than the method.

**What exists and what it actually asserts:**

| File | n | Asserts |
|---|---|---|
| `test_parse_form20.py` | 9 | Header classification (EN + HI), lattice-table parse, row arithmetic, duplicate/missing PS detection, AC-total mismatch detection, regex fallback, candidate label split |
| `test_crosswalk.py` | 7 | Scoring across spelling variants, part-qualifier tolerance, building-type cap, sub-floor rejection, band assignment, split detection, runner-up capture |
| `test_roll_privacy.py` | 6 | Counts correct; **no full name or EPIC survives parsing**; only surnames aggregate, as counts; relatives' surnames excluded; supplement sections; supplement keeps nothing personal |
| `test_caste_estimate.py` | 8 | Survey override and its confidence; percentages sum to 100; census pulls ST toward the published figure; "other" bucket fallback; coverage→confidence monotonicity; no-surname case; recency decay |
| `test_scenario.py` | 9 | Status quo reproduces the 2024 margin; JLKM transfer flips the seat; sympathy moves votes; turnout scales the margin; new-voter default split; percentiles bracket the point; seed reproducibility; row normalisation; variance reporting |
| `test_sql_guard.py` | 13 | Writes, multi-statement, non-allow-listed tables, other schemas, subquery tables, file/network functions, unknown functions all rejected; LIMIT injection/clamping; **allow-list matches the GRANT list** |
| `test_dedupe.py` | 7 | URL hash normalisation, simhash near-duplicates, signed-64 round trip |
| `test_textnorm.py` | 6 | Devanagari digits, building canonicalisation across year variants, room/part stripping, inherent-vowel transliteration |

**The gap is structural: there is not one test that touches SQL.** Every metric the product displays — swing, margin, turnout, transfer, floating vote, priority, area rollup — is computed in `0009`–`0011`, and none of it is exercised. `test_scenario.py` is thorough about the projection arithmetic and then reads its input from `mv_result_booth_wide`, which nothing verifies. Every Critical and High finding in §B and §D lives in that untested layer. Likewise, no `load()` function in any parser is tested, so C5 (partial writes) and C6 (duplicate candidates on re-run) were invisible to the suite.

**Highest-value missing tests, ranked:**

1. **Materialized views against a hand-built fixture.** Insert three booths with known votes across two elections, refresh, and assert `margin_pct`, `turnout_pct`, `share_pct`, `swing_pct` and `priority_score` to the decimal — including the AC-level margin reproducing the published 1.85%. This catches D1, D2, D3, B1 and B4 in one test. Needs a throwaway Postgres (testcontainers or a compose service), which is the only reason the suite currently avoids it.
2. **Crosswalk write path, not just scoring.** Assert that a 0.70-scoring station still produces a `booth_crosswalk` row (B2), and that crosswalking two elections in sequence never reuses a `B9xxx` id (B3). Both are pure-Python with a fake cursor.
3. **Form 20 end-to-end against a known booth.** Take one real Giridih Form 20 page, load it, and assert the booth's per-party votes and the AC total. This is the test the README implies exists and the one that would have caught C1 and C2 immediately.
4. **Re-run idempotency, per loader.** Load the same fixture twice and assert row counts and vote sums are unchanged. Catches C6 directly.
5. **Privacy assertion beyond the parser.** Extend `test_roll_privacy.py` to run `scan_pdf` against a temp directory and assert that no file written under `OCR_DIR` matches the EPIC regex or contains a fixture name. Catches C3 — the current tests pass while the data is on disk.
6. **Roll composition invariants.** `male + female + other == electors`, age bands summing within tolerance. Catches C7.
7. **API contract tests** with a fake DB layer: that a block-role token cannot read another block's booths through `/booths`, `/results`, `/rolls/changes`, `/priority` or `/chat` (E2).
8. **`jellyfish` vs fallback agreement** over the crosswalk fixtures (A7).

---

## 5. Data correctness verdict

**No. I would not trust a booth-level margin or swing figure produced by this code today, and I would not trust the AC-level totals either.**

Concretely, with the code as it stands:

- **Margin is wrong at every booth.** Form 20 columns never resolve to a party (C1), so `mv_result_booth_wide` reports `margin_pct = 100.00` with a NULL winner for every booth. Even after C1 is fixed, the denominator excludes NOTA while the row's own `total_valid` includes it, putting the AC margin at 1.87% against the published 1.85% (D1).
- **Swing is wrong or missing.** The earliest loaded election reports a swing equal to each party's full share (D2). Stations in the 0.65–0.85 crosswalk band are absent from every view, so any multi-year comparison is computed over an unknown subset with no indication (B2) — and the check meant to catch that reports healthy (C11). Crosswalking a second historic election reuses booth ids from the first (B3).
- **Turnout does not exist.** `electors` is never written (B4).
- **New-voter share reads zero** in the map, booth card and priority score while the Voters table shows real numbers (B1, D7).
- **A re-parse corrupts what is already loaded** (C6), and a failed load leaves partial data behind (C5).
- **The validation gate that should have caught most of this either cannot run or has been switched off** to get any data in at all (C2, C4).

What *is* trustworthy: the Devanagari normalisation and building canonicalisation (`common/textnorm.py`) is the strongest module in the repo and well tested; the crosswalk **scoring** is sound even though its write path is not; the roll **counting** of electors is correct even where gender and age are not; the caste blend's confidence arithmetic matches its specification and is surfaced honestly end to end; and the scenario engine is correct arithmetic on stated assumptions, correctly presented — it is just reading a poisoned baseline.

The shortest path to a trustworthy booth-level margin is C1 → C2 → B2 → D1 → B4, then a real Form 20 loaded end to end and reconciled by hand against the published AC totals. Nothing should be published before `ingest.validate --strict` exits zero with the AC check **on**.

---

## 6. Privacy and compliance verdict

**Individual voter data: the design is right, the implementation leaks it to disk. One Critical finding.**

I searched specifically for voter names, EPIC numbers, addresses, relatives' names and phone numbers reaching the database, logs, caches, temp files, exports and API responses.

- **Database: clean.** No table has a column capable of holding an individual voter. `roll_snapshot` and `roll_change` are counters only (`0004_rolls.sql`). `caste_estimate` is `(booth_uid, community_id, count, pct, confidence, source)` — aggregate by construction, with the primary key enforcing it (`0005:33`). `write_surname_estimates` (`caste_estimate.py:145-182`) converts the surname histogram to community totals and persists nothing else; the histogram itself never leaves memory. `parse_roll.RollCounts` is a dataclass of integers plus a `Counter`, and I traced every consumer.
- **API responses: clean.** No endpoint returns anything below booth level. `/caste` and the booth card both carry explicit disclaimers.
- **Logs: clean.** Roll parsing logs only aggregates (`parse_roll.py:428-438`). The OTP path logs the last four digits of a phone and never the code (`auth.py:129`).
- **Exports: clean.** CSV exports serialise the same booth-level rows.
- **Disk: NOT clean — Critical.** `ingest/extract_pdf.py:97` writes every page's full text to `ocr/<stem>-<sha12>/page_NNNN.json`, and `parse_roll.scan_pdf` (`:281`) routes roll PDFs through it like any other document. The complete electoral roll — names, EPIC numbers, fathers'/husbands' names, house numbers, ages — is persisted in plaintext JSON and retained indefinitely. `discard_raw()` (`:389-399`) then deletes the source PDF by default, so the system destroys the auditable original and keeps the personal data. This contradicts `README.md:24-26`, LLD §12, and the DPDP-Act reasoning in HLD §5. **`ocr/` is correctly in `.gitignore`, so nothing is committed** — the exposure is on the VPS filesystem and in any backup or snapshot of that volume.
- **One unguarded ingress.** `POST /ground-reports` (E5) accepts arbitrary free text from any authenticated user, stores it verbatim, and embeds it for vector search. Nothing screens it for personal data and no validation check inspects the table.
- **The stated structural check is narrower than advertised.** `validate.py:129-149` inspects `information_schema` column names across four tables. It cannot see file contents, `review_queue.payload`, `ground_report.text` or `news_item.body`. `README.md:26` describes it as asserting "the schema has nowhere to put one", which is accurate for what it does; the RUNBOOK's compliance section (`:106-113`) presents the pair of checks as sufficient, which they are not.

**Required before any roll is processed on the production host:** suppress the page cache for roll documents, purge any existing `ocr/` content derived from rolls, and add a filesystem scan for EPIC patterns to `validate.py` and to the RUNBOOK's compliance checklist.

**Secrets and credentials: clean.** No credential, key or token is present in the repository. `.gitignore` covers `.env`, `raw/`, `ocr/`, `backups/`, `pgdata/`. `.env.example` holds only placeholders. The frontend bundle contains no secret. Git history cannot be examined because the project is not under version control at all (A6) — which is itself the finding.

**Against the stated compliance controls:** aggregate-only storage ✅ (in the DB) / ❌ (on disk). Role-scoped access ✅ with one bypass through the parked chat path (E2). No individual caste attribution ✅. Raw rolls deleted after parse ✅ but counter-productively (C13). Prompt bodies purged after 7 days ✅. Query logging ✅ via `llm_usage`. Access over TLS ❌ (A2).

---

## 7. Chatbot isolation check

Answering only the three questions asked.

**(a) Is chatbot code cleanly isolated behind a feature flag? — No. There is no feature flag at all.**
Grep for `CHAT_ENABLED`, `ENABLE_CHAT`, `CHATBOT_ENABLED` or `FEATURE_` returns nothing in first-party code, and `common/config.py` has no such setting. `api/main.py:14` imports the `chat` router unconditionally and mounts it at `:80`. `api/routers/chat.py:14` does `from chatbot.agent import ask` at module scope, which pulls in `chatbot.tools` → `chatbot.sql_guard` → a top-level `import sqlglot` (`sql_guard.py:23`). The chat feature cannot be disabled without editing source.

**(b) Does any chatbot dependency, API key requirement or DB object block the non-chatbot system from starting? — One dependency does; the API key and the DB objects do not.**

- **Dependency: yes, `sqlglot` is a hard import-time requirement of the entire API.** Because of the import chain above, removing `sqlglot==30.18.0` from `requirements-api.txt` makes `uvicorn api.main:app` fail at startup with `ModuleNotFoundError`, taking down the dashboard. It is currently declared, so the shipped configuration works — but the coupling means you cannot strip the chatbot from the API image. (`anthropic` is *not* a blocker: `chatbot/llm.py:39` imports it lazily inside `get_client()`.)
- **API key: no.** `get_client()` is `lru_cache`d and raises `RuntimeError("ANTHROPIC_API_KEY is not set")` only when first called at request time (`llm.py:37-44`). Startup, `/health` and every dashboard endpoint are unaffected by a missing key. `/chat` returns a handled error. One consequence worth flagging as it is not obviously chat-related: **news labelling shares the key**, and `/news` filters on `labelled_at IS NOT NULL` (`news.py:30`), so without a key the News page is permanently empty even though crawling works.
- **DB objects: no.** Migration `0012_roles_grants.sql` creates the `giridih_ro` role and its GRANTs. It runs as part of the normal migration set, has no dependency on chatbot code, and fails nothing if the chatbot is never used. `apply_migrations.set_readonly_password` warns and continues when `READONLY_DB_PASSWORD` is unset (`:90-92`), and `common/db.get_readonly_pool` falls back to `DATABASE_URL` when `READONLY_DB_URL` is absent (`:41`). No table, view or index exists solely for the chatbot.

**(c) Dead or half-wired chatbot code that would break a build or migration? — None breaks anything. Two pieces are half-wired.**

- `chatbot/tools.py:201-207` (`make_chart`) returns a Vega-Lite spec; `api/routers/chat.py:66-67` streams it as a `chart` SSE event; `web/src/components/ChatPanel.tsx:41, 79, 85` collects it into `charts: unknown[]` and never renders it. `vega`, `vega-lite` and `vega-embed` are in `web/package.json:22-24` and imported by no source file. The chart path is complete up to the last step and then drops the output. Confirmed harmless: the frontend build succeeds and produces no vega chunk.
- `common/jobs.py:89-96` (`already_done`) is never called; `news/label_batch.py:124` records an `idempotency_key` that nothing reads.

Neither affects a build or a migration. `pytest -q` passes 96/96 including 13 `sql_guard` tests and 4 router tests, and `npm run build` is clean.

**One security note in scope as an API-layer issue rather than a chatbot-quality issue:** `/chat` is open to all roles, and `tool_get_booth_card` (`tools.py:188-192`) calls `build_booth_card(booth_uid)` with caste included and no block filter, bypassing the role and block restrictions enforced on `/caste` and `/booths/{uid}/card`. See E2. Not live while the feature is parked, but it should be fixed before it is unparked.

---

## 8. External dependencies detected

| Service | Used by | Configured at | Credentials | Notes |
|---|---|---|---|---|
| **Anthropic API** | `chatbot/llm.py:44`; `news/label_batch.py:106`; `news/label_collect.py` | `ANTHROPIC_API_KEY` (`.env.example:30`) | **Required** | Parked for chat. Still required for news labelling, without which `/news` is empty |
| **CEO Jharkhand portal** | `ingest/fetch_ceo.py` (discovery + download) | `CEO_JHARKHAND_BASE` (`.env.example:74`) | None | Scraped, not an API. "verify before first run" per the template |
| **SEC Jharkhand portal** | `ingest/fetch_sec.py` | `SEC_JHARKHAND_BASE` (`.env.example:75`) | None | Discovery only; results are transcribed to CSV by hand |
| **Nominatim (OSM)** | `ingest/geocode.py:27` | `NOMINATIM_USER_AGENT`, `NOMINATIM_RPS` | None | Hardcoded URL; 1 req/s; results bounded by a Giridih bbox (`:31`) |
| **CARTO basemap tiles** | `web/src/pages/MapExplorer.tsx:95` | Hardcoded | None | `basemaps.cartocdn.com/light_all` — no config, no fallback |
| **Google Fonts** | `web/index.html:8-13` | Hardcoded | None | Undocumented (A11); Devanagari degrades if blocked |
| **Google News RSS** | `news/crawl_rss.py` via `news_sources.csv` | `db/seed/news_sources.csv:2-6` | None | 5 Giridih/Pirtand/Parasnath/bypoll queries |
| **Prabhat Khabar / Dainik Bhaskar / Hindustan RSS** | same | `news_sources.csv:7-9` | None | Jharkhand state feeds, geo-filtered client-side |
| **Hugging Face Hub** | `news/embed.py:33` | `EMBED_MODEL`, `HF_HOME=/models` | None | Downloads `intfloat/multilingual-e5-small` on first use — needs network at first run |
| **PostgreSQL 16 + PostGIS + pgvector** | everything | `DATABASE_URL`, `READONLY_DB_URL` | Required | **PostGIS is not in the pinned image — A1** |
| **Tesseract (hin+eng) + poppler** | `ingest/ocr_tesseract.py` | `TESSERACT_LANGS` | None | In the worker image; local binary, not a service |
| **SMS provider (MSG91/Textlocal)** | `api/routers/auth.py:125` | `SMS_PROVIDER`, `SMS_API_KEY`, `SMS_SENDER_ID` | Would be required | **Not implemented** — stub only (E7) |
| **Let's Encrypt / certbot** | intended by LLD §1 | `certs` volume exists | Would be required | **Not implemented** (A2) |
| **S3 / Backblaze B2** | intended by LLD §7 | — | Would be required | **Not implemented** (G1) |

Only one credential is genuinely required to run the system as built: `ANTHROPIC_API_KEY`, and only for chat and news labelling. Everything on the data path — CEO/SEC portals, Nominatim, CARTO, RSS feeds, Hugging Face — is unauthenticated, which is a good property for a system like this and matches the LLD's cost model.

---

## 9. Gaps against the design, ranked by by-election impact

The bypoll deadline is ~6 March 2027 (`data.py:20`), so there are roughly five months. Ranked by how hard each blocks that timeline.

1. **Nothing can be installed** (A1). Until the PostGIS image is fixed, Phase 0 cannot begin. Blocks everything.
2. **Form 20 cannot be loaded correctly** (C1, C2, C4, C5, C6). This is Phase 0's primary deliverable and LLD S0's acceptance criterion ("VS-2024 + LS-2024 booth results load with zero validation errors"). Nothing downstream is meaningful without it.
3. **The booth crosswalk corrupts multi-year comparison** (B2, B3, C11). LLD S0's second criterion is "≥90% booths auto-crosswalked to 2019". The scoring can probably meet it; the write path cannot be trusted, and the metric that would tell you is broken.
4. **Turnout does not exist** (B4) and **new-voter share reads zero** (B1). Two of HLD's three "key focus" modules are non-functional. Module 4 is LLD S1's acceptance criterion.
5. **No TLS** (A2). A campaign tool carrying booth-level strategy and caste estimates should not be on plain HTTP before it has external users.
6. **Roll personal data persists to disk** (C3). Must be fixed before the first real roll is processed — not before the by-election, but before the next ingestion run.
7. **No off-host backup and an untested restore** (G1). LLD S5 requires the restore drill to pass. The cost of losing Phase 0's manual reconciliation work is weeks.
8. **Census demography has no loader** (B8). Degrades the caste blend's accuracy and caps its confidence; HLD module 5 is Phase 2.
9. **Ground survey has no intake** (B8). The highest-confidence caste input cannot be supplied. LLD S3's acceptance criterion ("survey form") is unmet.
10. **Caste × vote overlay not built** (F4). HLD module 5 specifies the scatter; only a strip plot exists and the ecological regression is crib-sheet SQL.
11. **Map filters incomplete** (F2) and **the margin ramp is one-sided** (F1). Module 2 is the primary daily-use view.
12. **LS-2024 load path undocumented** (D4). The LS↔VS split is called "the single most important dynamic to model" (HLD §1.1); the README's Phase 0 never says how to load it, and the resulting uniform `floating_pct = 50%` looks like data rather than absence.
13. **Local elections load path undocumented.** `fetch_sec.py --load-csv` works and appears in no runbook. HLD module 7, Phase 3.
14. **No notification on job failure** (G4) and **no version control** (A6). Both compound every other risk during polling week.
15. **Surname dictionary at 176 of ~300 entries, unevenly distributed** (C14). Directly caps caste confidence and biases it against the most important community.

---

## 10. Prioritised remediation list

Effort is for one engineer who knows this codebase.

| # | Fix | Findings | Effort | Unblocks |
|---|---|---|---|---|
| 1 | Build a `postgres:16 + postgis + pgvector` image and pin it; add an extension precondition check to `apply_migrations` | A1 | **2 h** | Everything. Nothing else can be tested until this is done |
| 2 | Resolve Form 20 columns to parties: add `party.name_hi` + a Devanagari abbreviation alias table to `resolve_candidates`; fuzzy-match parsed columns onto the candidates seeded from `ac_totals.csv`; abort when resolution rate is below a threshold | C1, C2, D3 | **1–2 d** | Correct party columns, winner, margin, share, swing, transfer, rollup, scenario baseline — most of the product |
| 3 | Load NOTA as a real candidate; make one denominator ("valid votes incl. NOTA") authoritative across `mv_result_booth_wide`, `mv_booth_party_share` and `mv_area_rollup`; add a validate check that the AC margin reproduces 1.85% | D1 | **half day** | Margin and share figures that reconcile with ECI |
| 4 | Write a `booth_crosswalk` row for review-band matches with real confidence and `reviewed=false`; fix `next_uid_start`; base `crosswalk_quality` on `ps_list_entry` | B2, B3, C11 | **half day** | Trustworthy multi-year swing; no silently dropped booths |
| 5 | Populate `result_booth_meta.electors` from the roll snapshot / PS list; seed `election_roll_link` | B4, B1 | **half day** | Turnout everywhere; new-voter share in map, booth card and priority; two map metrics and marker sizing |
| 6 | Suppress the page cache for roll documents; purge existing roll-derived `ocr/` content; add an EPIC-pattern filesystem scan to `validate.py` and the RUNBOOK | C3, C13 | **half day** | The aggregate-only guarantee, in fact and not just in the schema |
| 7 | Make `parse_form20.load()` one transaction; fix candidate uniqueness (`NULLS NOT DISTINCT` or key on `column_index`); treat an unparseable cell as an error rather than 0 | C4, C5, C6 | **half day** | Safe re-parsing — the most common operation in Phase 0 |
| 8 | Refuse to start on a missing/placeholder/short `JWT_SECRET`; add login rate limiting; stop leaking `str(exc)` on the chat SSE path | E1, E3, E4 | **2–3 h** | Closes the auth bypass |
| 9 | Add TLS: certbot sidecar + 443 server block + 80→443 redirect | A2 | **half day** | Safe external access |
| 10 | Off-host backup (B2/S3) in `ops.backup`; script the restore drill; move the password out of `pg_dump`'s argv | G1, G2 | **half day** | Disaster recovery; LLD S5 criterion |
| 11 | Sign `margin_pct` by winner before the diverging ramp; add year/block/area filters to the map; honour `election_label` in `/booths` | F1, F2 | **half day** | The primary daily-use view reads correctly |
| 12 | `git init`, first commit, verify `.gitignore`, push to a private remote | A6 | **1 h** | Review, rollback, CI, multi-operator work |
| 13 | Fix roll composition: carry section state across PS groups; per-entry deletion reasons; add `male+female+other == electors` and age-band checks to `validate.py` | C7, C8, C9 | **1 d** | Gender and age-band splits in module 4 |
| 14 | Restrict `check_roll_continuity` to mother rolls; NULL out `swing_pct` and `floating_pct` where there is no comparator | C12, D2, D4 | **2–3 h** | Validation output people will actually read; no fabricated swing or uniform 50% floating vote |
| 15 | Census demography loader + ground-survey intake (endpoint + minimal form) | B8 | **2 d** | Caste blend's census arm and its 0.9-confidence path; LLD S3 criterion |
| 16 | Materialized-view test fixture against a throwaway Postgres, covering margin/turnout/swing/priority to the decimal | §H.1 | **1–2 d** | Regression safety for the entire analytics layer |
| 17 | Crosswalk write-path and loader idempotency tests | §H.2, §H.4 | **half day** | Prevents recurrence of items 4 and 7 |
| 18 | Form 20 end-to-end test against one real Giridih page with hand-verified numbers | §H.3 | **half day** | The acceptance test LLD S0 actually calls for |
| 19 | Expand `surname_dict` toward 300 with parity across Kurmi/Yadav/SC; report `est_pct` as share of matched or model the residual | C14 | **1 d** | Less biased caste estimates; higher confidence scores |
| 20 | Caste × vote scatter and an `/caste/correlation` endpoint from `metrics.sql:67-78` | F4 | **half day** | HLD module 5 as specified |
| 21 | Chatbot feature flag; move `chatbot` imports inside handlers; role- and block-scope `get_booth_card` | A5, E2 | **2–3 h** | Chat can be switched off; closes the role bypass before unparking |
| 22 | Housekeeping: drop pandas/scipy/pypdf/rapidfuzz/python-multipart/vega; add `web/.dockerignore`; add `jellyfish` to dev requirements; self-host fonts; fix worker volume ownership; add FKs on `booth_crosswalk`/`ps_list_entry`; unique index on `is_baseline` | A4, A7, A8, A10, A11, A3, B6, B9 | **1 d** | Smaller images, faster builds, tests that match production |
| 23 | Document the LS-2024 and local-election load paths in the README Phase 0 sequence | §9.12, §9.13 | **1 h** | The LS↔VS module, which is the most important analysis in the brief |
| 24 | Job-failure notification (email or webhook) from `job_context`; structured logging with a request id; compose log rotation | G3, G4 | **half day** | Failures surface during polling week without someone watching `/admin` |

**Roughly 3 weeks of focused work to a system whose booth-level numbers can be trusted**, with items 1–7 (about one week) being the difference between "produces wrong election numbers silently" and "produces correct ones". Items 1–5 should be done in order; they are sequentially dependent.

---

*Prepared by static review and local execution of the build and test suite. No live database or deployed instance was available, so findings marked "verify" need confirmation against a running stack. Review this report for accuracy and completeness before acting on it.*
