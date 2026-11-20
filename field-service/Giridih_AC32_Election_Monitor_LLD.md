# Jharkhand Election Monitor — Low-Level Design v2

**Version:** 2.0 · **Date:** 1 Oct 2026 · **Supersedes:** LLD v1.0 and its Amendments section
Filename kept for continuity. Where this document and the code disagree, check `DECISIONS.md` and `docs/METRICS.md`, then fix whichever is wrong.

---

## 0. What changed from v1.0

| Area | v1.0 | v2 (as built) | Reference |
|---|---|---|---|
| Scope | Giridih only | Six ACs, every table scoped by `ac_id` | MULTI_AC_EXPANSION_SPEC §2 |
| Booth ID | `B0147` | `32-B0147`, from a per-AC sequence, never from the PS number | Audit B3 |
| Elections | One `election` table | `election_event` (shared) + `election` (per-AC contest) | Spec §2.3 |
| Spatial | PostGIS geometry columns | lat/lon `double precision` + GeoJSON `jsonb`; PostGIS optional | D-006 (N4) |
| Metrics | Formulas in SQL views and Python separately | One source (`analytics/metric_sql.py`) generates the SQL functions; Python mirrors; three-way parity test | docs/METRICS.md |
| Party matching | Exact header text | `party_alias` + fuzzy name match with gap and first-name rules; abort on any unresolved column | §5.1 |
| Contest | Hardcoded JMM vs BJP | `ac_contest` per AC per event | §6 |
| Chatbot | Live, Haiku/Sonnet routing | Parked behind `CHAT_ENABLED=false`; imports isolated | Audit A5 |
| Auth | passlib | `bcrypt` directly; API refuses weak/placeholder `JWT_SECRET` | E1, N12 |
| Storage | Local disk | `common/storage` with local and S3 backends; roll PDFs refused remote storage (code guard + DB CHECK + preflight) | C3 |
| Map tiles | CARTO (now needs a key) | `VITE_TILE_URL`, default OpenStreetMap; tile-failure fallback | DECISIONS |
| Local stack | Docker Compose only | `scripts/dev_stack.py` on embedded PostgreSQL (pgserver), no Docker | FRONTEND_HARDENING §1 |
| Fixtures | Bundled in main chunk | Dynamic import only when `VITE_FIXTURES` is set | N9 |

## 1. Runtime

| Component | Technology |
|---|---|
| Database | PostgreSQL 16, extensions `vector` (required); `postgis`, `pg_trgm`, `unaccent` only where actually used (see migrations) |
| API | Python 3.11, FastAPI, psycopg 3 |
| Worker | APScheduler, optional service |
| Ingestion | pdfplumber, Tesseract (hin+eng) for image pages, jellyfish for matching |
| Frontend | React 18, Vite, TypeScript, Tailwind, Leaflet, Recharts, React Query, i18next |
| Tests | pytest (unit, SQL against embedded Postgres, e2e), Playwright (Chromium, or Edge via `channel: 'msedge'`) |

## 2. Repository layout (indicative)

```
api/          FastAPI app, routers (all data routes under /acs/{ac_number}/...)
analytics/    metric_sql.py (single source of formulas), metrics.py, scenario, caste_estimate, refresh
ingest/       fetchers, extract_pdf, parse_form20, parse_pslist, parse_roll, crosswalk, validate, geocode, load_csv
news/         crawl, dedupe, labelling
worker/       scheduler and job runner
chatbot/      parked
common/       text normalisation, storage, PII screen
db/           migrations (0001–0017), seeds (acs, blocks, areas, parties, party_alias, alliances, contests, knowledge cards)
scripts/      dev_stack.py, preflight.py, purge_roll_cache.py, endpoint_inventory.py
web/          frontend; e2e/ Playwright specs
docs/         METRICS.md, RUNBOOK.md, ENDPOINTS.md
```

## 3. Data model

### 3.1 Spine
`state` → `district` → `pc` → `ac` (`ac_number`, `name_en/hi`, `reservation`, `pc_id`, `verified`, `bypoll_due`); `ac_district` for ACs spanning districts.

### 3.2 Geography
`block(ac_id, …)`, `area(ac_id, block_id, kind = panchayat|ward, geojson jsonb)`, `booth(booth_uid PK, ac_id, area_id, ps_name, building, roll_part, lat, lon, geocode_conf, category, is_critical, …)`, `ps_list_entry` (per election), `booth_crosswalk(election_id, ps_number, booth_uid, confidence, reviewed)`, `booth_lineage(old_election_id, old_ps, new_booth_uid, kind = split|merge, weight)`.

### 3.3 Elections and results
`election_event(type VS|LS|PANCHAYAT|ULB, year, label)`, `election(event_id, ac_id, is_baseline)` with one baseline per AC, `party`, `party_alias(alias, party_id, script)`, `party_alliance(party_id, event_id, alliance)`, `ac_contest(ac_id, event_id, party_a, party_b)`, `candidate`, `pc_candidate` (LS), `result_booth`, `result_booth_meta(electors, total_valid, nota, rejected, source_doc, source_page)`, `result_ac_total` (published totals for the reconciliation gate). NOTA is a candidate row.

### 3.4 Rolls (counts only)
`roll_revision`, `roll_snapshot(booth_uid, electors, male, female, other, age bands, source_doc, source_page)`, `roll_change(additions, deletions, modifications, deletion reasons, …)`, `election_roll_link(election_id, revision_id)`.

### 3.5 Estimates and context
`caste_estimate(ac_id, booth_uid, community_id, est_pct, matched_pct, confidence, source, method_version)` including an explicit `UNMATCHED` bucket; `surname_dict`; `demography`; `area_indicator(area_id, indicator, period, value, unit, source)`.

### 3.6 Extended (tables present; data loaded by CSV as available)
`candidate_profile`, `local_office_holder`, `influencer` + `influencer_area`, `organisation`, `political_event`, `poll_day_turnout`, `ground_report`, `work_log`, `public_issue` + `issue_event`, `alert_rule`. Influencer, organisation and event data are strategist-and-above only.

### 3.7 Operations
`source_doc(sha256, storage location, parse_status new→extracted→parsed→validated→loaded|failed|drifted)`, `review_queue` (deduplicated on kind + ref), `job_run`, `app_user`, `llm_usage`.

### 3.8 Materialized views
14 views, all with `ac_id` leading, including `mv_result_booth_wide`, `mv_result_booth_candidate`, `mv_swing`, `mv_swing_vanished`, `mv_transfer_ls_vs`, `mv_new_voter_share`, `mv_booth_priority`, `mv_area_rollup`, `mv_ac_summary`. Refreshed `CONCURRENTLY` after every load (manually when no worker runs).

## 4. Metrics

Defined once in `analytics/metric_sql.py`; the SQL functions in migration 0015 are generated from it, and a test fails if they drift. Full definitions and NULL rules: `docs/METRICS.md`. Summary:

| Metric | Definition |
|---|---|
| valid_votes | Σ candidate votes including NOTA |
| turnout_pct | votes polled ÷ electors (electors from the linked roll snapshot, never Form 20) |
| share_pct | candidate votes ÷ valid_votes |
| margin | winner − runner-up among real candidates; NOTA never ranks; ranked by candidate, never party bucket |
| swing_pct | share now − share before, same booth via crosswalk; NULL if no reviewed link, no prior election, or unaggregated split/merge |
| new_voter_pct | additions in window ÷ electors at window end |
| floating_pct | Pedersen index, LS vs VS same year |
| priority_score | per-AC percentile blend of closeness, new voters, floating vote, volatility; missing inputs dropped and weights renormalised, `inputs_used` stored |

## 5. Ingestion

### 5.1 Form 20
Extract text (image pages via OCR) → detect candidate columns → resolve each column: party in brackets via `party_alias` first; otherwise fuzzy name match against that contest's candidates, accepted only if score ≥ 0.88, ahead of second place by ≥ 0.05, and the first-name part also matches. Any unresolved column aborts the load with the top-three guesses in the review queue. Unreadable cells go to the review queue, never become 0. Per booth: candidates + NOTA = total valid. Per AC: totals equal `result_ac_total` exactly. Stage → validate → promote in one transaction; re-runs are idempotent.

### 5.2 Polling-station list and crosswalk
`parse_pslist --anchor` mints booth IDs for the current list and refuses if the AC already has booths unless `--re-anchor`, which goes through the crosswalk. Crosswalk score = 0.5 building + 0.3 place + 0.2 roll part; ≥ 0.85 auto, 0.65–0.85 written unreviewed and queued, < 0.65 new booth; splits and merges recorded in `booth_lineage`.

### 5.3 Rolls
Counts only; page text is never cached. Composition checks (male + female + other = electors, age bands within tolerance) fail the load. Supplement section state carries across booth groups; deletion reasons per entry. `ingest.validate --privacy` scans disk and every text/jsonb column (including materialized views) for EPIC, mobile and Aadhaar patterns, and the roll load refuses to run on a dirty disk.

### 5.4 Scrapers
Config-driven sources, structure fingerprints for drift detection, sha256 content addressing, polite retries, raw documents archived permanently.

## 6. Analytics

- **Community estimate:** surname match + Census SC/ST blend; SC/ST rescaled to target, then only non-SC/ST rescaled; unmatched kept as `UNMATCHED`, never extrapolated; religion as a separate aggregate bucket.
- **Scenario:** per AC and contest pair, baseline election, transfer matrix on alliances, winner = argmax excluding NOTA, noise applied before renormalising, output labelled "sensitivity range".
- **LS segment:** the PC Form 20 is filtered to this AC's booths; candidates shared via `pc_candidate`.

## 7. API

- Data routes under `/acs/{ac_number}/…`; legacy single-AC routes return 308 redirects.
- `GET /config` returns `chat_enabled`, the AC list and version; `GET /health` returns 503 when the database is unreachable.
- Role checks on every route; block users are scoped by (AC, block) in the query itself.
- Generic error bodies; no stack traces.
- Full route list: `docs/ENDPOINTS.md` (generated by `scripts/endpoint_inventory.py`).

## 8. Frontend

- Constituency switcher; the AC should be in every page URL (N13, in progress).
- Grouped navigation (Overview, Results, Voters & community, Politics & news, Analysis, Admin).
- Data operations (health, commands, review queue) on Admin only.
- Every figure shows provenance (source page, "estimate" with confidence, or "fixture"); NULL as "—" with a reason.
- Map: configurable tiles, colour ramp in the contest pair's party colours (lightness-separated for colour-blind readability), fit to markers, "no data" in the legend, tile-failure fallback.
- Hindi default, English toggle, Indian digit grouping, proper plurals, a test that fails on any missing translation key.

## 9. Security and privacy

JWT with enforced strong secret; bcrypt password hashing; login rate limit (E4, open); roles enforced in queries; PII screen on all free-text inputs; roll PDFs local-only; privacy scan of disk and database; no secrets in the repo.

## 10. Deployment

| Environment | Database | API | Frontend |
|---|---|---|---|
| Local | `dev_stack.py` (embedded Postgres) or Docker Compose | uvicorn | Vite dev server |
| Hosted demo | Supabase (Session pooler, SSL, `vector` enabled) | Railway (`$PORT`, `/health`) | Vercel (`VITE_API_BASE`) |

Hosted-deployment changes (vercel.json, CORS allow-list, pooler-safe psycopg settings, mock-data loader for any `DATABASE_URL`) are tracked in `NEXT_STEPS.md` Step 3.

## 11. Testing

pytest unit tests (metrics and NULL rules, parsers, crosswalk, privacy), SQL tests and e2e tests against embedded PostgreSQL, a route × role contract test over every API route, and Playwright browser specs. Current counts and open items: `UAT_READINESS.md` and `PROGRESS.md`.

## 12. Open items at time of writing

See `PROGRESS.md` for the live list. Notable: `/summary` and `/booths/{uid}/card` fail against a real database after migration 0015 (recorded as strict xfail); constituency not yet in page URLs (N13); no real Form 20 loaded yet; panchayat seeds missing for all ACs (N10); five ACs unverified.
