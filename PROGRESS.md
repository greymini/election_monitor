# PROGRESS.md — build plan and status

**Session start:** 30 Sep 2026 · **Governing document:** `CLAUDE_CODE_MASTER_PROMPT.md`
**Status of this file:** plan approved 30 Sep 2026. Track A in progress.

**Operator decisions taken at approval:**
1. **DB gates:** proceed without a database. Build and unit-test everything, write `tests/e2e/`,
   and record every DB-dependent gate as `NOT VERIFIED HERE` with the exact operator command.
   `UAT_READINESS.md` line 1 will therefore be `READY WITH CAVEATS` at best.
2. **Check-ins:** run all of Track A, then report once. `PROGRESS.md` is updated as work lands so
   the state is inspectable at any time.

> This file is the checklist for the whole session. It is updated at every commit.
> Like every other output of this system, it must be reviewed by a human for accuracy
> and completeness before it is relied on.

---

## 0. State detection (master prompt §1)

Run before any edit. Findings, all verified by command, not assumed:

| Check | Result |
|---|---|
| `git log --oneline -30` | **fatal: not a git repository.** No `.git` anywhere under `election_monitor/`. Audit finding A6 confirmed. No history to build on, nothing committed, no prior work to preserve. |
| `PROGRESS.md` | absent (this file is new) |
| `UAT_READINESS.md`, `UAT_SCRIPT.md`, `DECISIONS.md` | absent |
| Migrations ≥ `0013` | absent. Migrations are `0001`–`0012` exactly as the audit describes. |
| `scrapers/`, `common/pii.py`, `docker/Dockerfile.db`, `scripts/purge_roll_cache.py`, `scripts/uat_seed.sh`, `scripts/uat_users.py`, `scripts/bench.py`, `web/.dockerignore`, `tests/e2e/` | all absent |
| `docs/METRICS.md`, `docs/CSV_FORMATS.md` | absent (only `docs/RUNBOOK.md` exists) |
| `pytest -q` | **96 passed in 0.48s** — matches the audit's baseline |
| `npm run build` | **clean**, built in 22.9 s, no TS or bundler diagnostics |
| `raw/` | empty except `.gitkeep`. **No real Form 20 PDF is present.** |
| `ocr/` | empty except `.gitkeep`. No roll text on disk today, so C3 has not yet leaked anything on this machine. |

**Conclusion: none of the fix prompt has been done.** This is the codebase exactly as audited.
Every audit ID is open. Nothing to avoid redoing.

### Two audit claims the auditor could not execute — now verified

- **E1 confirmed.** `jwt.encode({'sub':'1','role':'admin'}, '', algorithm='HS256')` mints, and
  `jwt.decode(token, '', algorithms=['HS256'])` returns `{'sub': '1', 'role': 'admin'}`. The
  empty-secret bypass is real, not theoretical.
- **A7 confirmed.** `import jellyfish` fails in `.venv`. All 96 tests — including the seven that
  validate the 0.85/0.65 crosswalk thresholds — run against the pure-Python fallback while
  production runs `jellyfish`. `pdfplumber` is also absent from `.venv`.

---

## 1. Environment constraints — what can and cannot be proved in this session

This matters more than anything else in the plan, so it is stated first and bluntly.

| Tool | Available | Consequence |
|---|---|---|
| Python 3.11.9 + `.venv` | yes | unit tests, parsers, static checks all runnable |
| Node 24 / npm 11 | yes | frontend builds and typechecks runnable |
| `git` 2.55 | yes | version control can be established (A6 fixable) |
| Network (pypi 200, `ceo.jharkhand.gov.in` 200) | yes | dependencies installable; the CEO portal answers, so a real Form 20 may be obtainable |
| **Docker / Docker Compose** | **NO — not installed, daemon unreachable** | `docker compose build` and `docker compose up -d` **cannot be run**. Gate A-1 (clean-clone boot), the A-3 "against a real Postgres" requirement, and Gate A-4 (`uat_seed.sh` on a fresh volume) cannot be *executed* here. |
| **PostgreSQL (any local server)** | **NO** — no `psql`, no `pg_dump`, no service | Migrations cannot be applied. Materialized views cannot be refreshed. The e2e tests the master prompt requires in `tests/e2e/` can be *written* but not *run*. |
| Administrator rights | **no** (`IsInRole(Administrator)` = False) | Docker Desktop and a PostgreSQL+PostGIS+pgvector server cannot be installed by me. |

**What this does to the definition of "Track A green".** The master prompt's gates are written
against a live compose stack. I can build everything they ask for and verify every layer that does
not need a database — parsers, resolution logic, metric formulas against hand-computed fixtures,
API contracts with a faked DB layer, the frontend, the migration SQL's internal consistency. I
cannot honestly report PASS on any gate whose proof is "ran against a real Postgres".

So unless this is resolved, `UAT_READINESS.md` line 1 will read **READY WITH CAVEATS** at best, and
every DB-dependent acceptance check will be recorded as `NOT VERIFIED HERE — no database in the
build environment`, with the exact command the UAT operator must run, rather than PASS. I will not
claim a check passes on the strength of reading the SQL.

**This is the one thing I need a decision on before Track A** — see §6.

---

## 2. Documents, layout and precedence

- Governing order (master prompt §0): this prompt → `MULTI_AC_EXPANSION_SPEC.md` → `claude_code_fix_and_expand_prompt.md` → LLD → HLD → existing code.
- **Layout conflict found.** The fix prompt requires `AUDIT_REPORT.md` and `MULTI_AC_EXPANSION_SPEC.md`
  to sit together at "the repo root". They do not: the spec, HLD, LLD, `RUN.md` and the master prompt
  are at `election_monitor/`; the audit, `README.md`, `docs/`, `docker-compose.yml`, `.gitignore` and
  all code are at `election_monitor/giridih-monitor/`. Neither directory satisfies the precondition.
  **Chosen resolution:** `git init` at `election_monitor/` (the true repo root, so the design documents
  are versioned alongside the code); `giridih-monitor/` stays the *application* root where compose,
  `.env` and code live; session and report documents (`PROGRESS.md`, `DECISIONS.md`, `UAT_SCRIPT.md`,
  `UAT_READINESS.md`) sit at `election_monitor/` beside the master prompt and `RUN.md`; no existing
  document is moved. To be recorded in `DECISIONS.md`.
- **Commit-gate substitution.** The master prompt requires `docker compose build` green before each
  commit. Not possible (§1). Substituted pre-commit gate: `pytest -q` green + `npm run build` green +
  migration SQL parsed and statement-linted by a script I add + `.env`/secret check. Recorded in
  `DECISIONS.md`; every commit message will say which gate ran.

---

## 3. Track A — UAT-critical. Strictly ordered, must be green before any Track B work.

### A-0 · Baseline under version control (prerequisite, not in the master prompt)
| # | Task | Status |
|---|---|---|
| A-0.1 | `git init` at `election_monitor/`; root `.gitignore`; `.gitattributes` forcing LF on `*.sh`/`*.sql`/`Dockerfile*`/`*.yml` | **done** `1b0efed` |
| A-0.2 | Verify no secret staged before the first commit | **done** — 166 files, no credential patterns, `.env` excluded |
| A-0.3 | Commit `chore: baseline before audit fixes` | **done** `1b0efed` |
| A-0.4 | `jellyfish` + `pdfplumber` in dev requirements; parity test (A7) | **done** `760a244` — found a real 0.12 divergence |

### A-1B · Deployment topology (operator request, 30 Sep — inserted here because it touches the same files A-3 rewrites)
| # | Task | Status |
|---|---|---|
| A-1B.1 | `common/storage.py`: per-kind backends, `local` + S3-compatible, roll kinds refused a remote backend as an invariant | **done** `33a51d3` |
| A-1B.2 | `0013_source_doc_storage.sql`: `storage_backend`/`storage_key`, `roll_docs_stay_local` CHECK, `parse_status` lifecycle, `status_changed_at/by` | **done** `33a51d3` |
| A-1B.3 | `scripts/lint_sql.py` — the substituted commit gate (D-002) | **done** `33a51d3` |
| A-1B.4 | Worker-less API: topology tests, `/config`, honest `/health` (503), `/knowledge-cards`, Factors and Overview read from the DB | **done** `f7e5f73` |
| A-1B.5 | Laptop ingestion: `ingest/documents.py` (`--doc`/`--key`), `fetch_ceo` writes through storage, `scripts/preflight.py` | **done** `252d6bd` |
| A-1B.6 | `RUN.md` reconciliation: it documents `--ac` on every loader, which does not exist until A-2; and it must say `analytics.refresh` is an operator step with no worker | todo |
| A-1B.7 | Compose: a worker-less profile, and `docker/Dockerfile.db` (folded into A-1.5) | todo |
| **Gate A-1B** | 33 topology + 45 storage + 20 document tests green; API imports with all worker deps blocked | **PASS for the static half**; DB and S3 round-trips `NOT VERIFIED HERE` |

### A-1 · Safety (master prompt §2 A-1; fix prompt Phase 0)
Audit IDs: **E1, C3, C13, A1, A3, A5, E3, E4, G2, A6**
| # | Task | Status |
|---|---|---|
| A-1.1 | **Write the failing test first:** empty-key and placeholder HS256 tokens are rejected; see it fail; then fix. Lifespan raises when `JWT_SECRET` is empty, < 32 bytes, or equals any `.env.example` placeholder (E1) | todo |
| A-1.2 | `extract_pdf.extract_document(cache: bool)`; `parse_roll` passes `cache=False`; roll page text never touches disk (C3) | todo |
| A-1.3 | `RETAIN_RAW_ROLLS` defaults **true**; raw roll PDFs kept under `raw/` at 0700; document why the original is the audit trail (C13) | todo |
| A-1.4 | `scripts/purge_roll_cache.py` + `python -m ingest.validate --privacy`: filesystem EPIC scan of `OCR_DIR`, `raw/` text sidecars, temp dir, log dir; DB scan of every `text`/`jsonb` column for EPIC, 10-digit mobile `(?<!\d)[6-9]\d{9}(?!\d)`, 12-digit Aadhaar `(?<!\d)\d{4}\s?\d{4}\s?\d{4}(?!\d)` | todo |
| A-1.5 | `docker/Dockerfile.db` from `pgvector/pgvector:pg16` + `postgresql-16-postgis-3`; pinned in compose; `apply_migrations` precondition asserting both extensions (A1) | todo |
| A-1.6 | `mkdir -p /data/raw /data/ocr /data/backups` before `chown` in `Dockerfile.worker` (A3) | todo |
| A-1.7 | `CHAT_ENABLED` (default false) guards `include_router(chat.router)`; every `chatbot.*` import moved inside handlers; test that `api.main` imports with the `chatbot` package removed and with `sqlglot`/`anthropic` absent (A5) | todo |
| A-1.8 | `GET /config` → `{chat_enabled, acs[], version, build_time}`; frontend reads it once at boot and hides `ChatPanel` accordingly | todo |
| A-1.9 | Rate-limit `/auth/login` (5/min per phone and per IP, in-process); constant-time path for an unknown phone (E4) | todo |
| A-1.10 | Chat SSE error path returns flat "Internal error" (E3) | todo |
| A-1.11 | `pg_dump` credentials via `PGPASSWORD` env, not argv (G2) | todo |
| **Gate A-1** | clean clone → `.env` with generated secret → `docker compose build && up -d` → `/api/health` ok; API refuses the placeholder secret; privacy scan clean; `api.main` imports without `chatbot` | **partly blocked — compose half needs Docker (§1)** |

### A-2 · Multi-AC spine (spec §2; fix prompt Phase 1)
Audit IDs closed structurally: **B3, B6, B9, B11**
| # | Task | Status |
|---|---|---|
| A-2.1 | `0013_multi_ac.sql`: `state`, `district`, `pc`, `ac`, `ac_district`, `election_event`, restructured `election`, `party_alliance`, `party_alias`; `ac_id NOT NULL` on every table in spec §2.2; FKs on `booth_crosswalk.election_id` / `ps_list_entry.election_id` (B6); `one_baseline_per_ac` (B9); `roll_revision` conflict target fixed (B11) | todo |
| A-2.2 | `booth_uid = f"{ac_number}-B{seq:04d}"` from per-AC sequence `booth_seq_{ac_number}`, never derived from PS number (closes B3 structurally) | todo |
| A-2.3 | Data migration of existing Giridih rows `B0147 → 32-B0147`, every FK rewritten in **one** transaction, row counts asserted before/after with `RAISE EXCEPTION` on mismatch | todo |
| A-2.4 | `ac` seed: `ac_number, name_en, name_hi, reservation, district(s), pc_number, verified, bypoll_due`. Giridih `verified=true` only for already-reconciled facts; the other five `verified=false`. **No constituency fact invented; no seeded number "corrected" from memory.** | todo |
| A-2.5 | `result_ac_total` seeded for 2019 and 2024 VS for all six ACs from spec §1 where a number is given, **empty (not zero)** where it is not, `source='spec-unverified'` | todo |
| A-2.6 | `party_alias.csv` (Devanagari + Latin + ECI + TCPD spellings) and `party_alliance.csv` per event (2009/2014/2019/2024 VS and LS) — this is what makes C1 go away properly | todo |
| A-2.7 | Rebuild all 10 MVs with `ac_id` leading in `GROUP BY` and in every unique index; add `mv_ac_summary` | todo |
| A-2.8 | API: every data route under `/acs/{ac_number}/…`; old Giridih routes kept as 308 redirects to `/acs/32/…` for one release; block scoping becomes `(ac_id, block_id)`; `/acs` lists ACs from `mv_ac_summary` | todo |
| A-2.9 | Frontend: AC switcher in `Layout` with `?ac=` in the URL and persisted; "unverified" badge from `ac.verified`; `/compare` page over `/acs` | todo |
| **Gate A-2** | `/acs` lists six ACs; switching AC changes every page; Giridih data identical before/after migration (snapshot test on `mv_result_booth_wide` counts and totals) | **blocked on a database (§1)** |

### A-3 · Giridih numbers correct (master prompt §2 A-3 and §3; fix prompt Phase 2)
Audit IDs: **C1, C2, C4–C12, C15–C17, B1, B2, B4, B5, B10, B11, D1–D9**
| # | Task | Status |
|---|---|---|
| A-3.1 | Obtain a **real** Giridih VS-2024 Form 20 (`fetch_ceo`; portal answered 200 on a HEAD check). If unobtainable, fall back to a recorded fixture and mark every affected check `PARTIAL — fixture only` on line 1 of `UAT_READINESS.md` | todo |
| A-3.2 | §3.1 Form 20 party resolution: header normalisation → `party_alias` bracket lookup → Jaro-Winkler ≥ 0.88 on transliterated Latin against that election's `result_ac_total` candidates → NOTA as a real candidate row → independents as `IND` with distinct `candidate_id`, ranked per candidate → **any unresolved column aborts the load** with a review-queue item carrying the raw header and top-3 guesses with scores (C1, C2, D3) | todo |
| A-3.3 | §3.2 canonical metrics implemented **once** — one Python module plus the SQL views that call it, no formula duplicated. All 14 rows of the table including every NULL rule. One denominator everywhere: valid votes **incl. NOTA** (D1) | todo |
| A-3.4 | `ac_contest(ac_id, event_id, party_a, party_b)` drives signed margin and scenario. **JMM/BJP hardcoded nowhere** | todo |
| A-3.5 | C4 unparseable numeric cell → review queue unconditionally, never 0; C5 whole load in one transaction, stage → validate → promote; C6 candidate uniqueness on `(election_id, column_index)` so re-parsing cannot double votes; C17 regex fallback also runs on pages whose table path returned fewer rows than expected | todo |
| A-3.6 | §3.3 crosswalk: review-band rows **written** with real confidence and `reviewed=false` (B2); UIDs from the per-AC sequence (B3); `crosswalk_quality` denominator = `ps_list_entry` (C11); `check_no_dropped_booths`; score includes `roll_part` carried onto `booth` and `ps_list_entry` (C10); split/merge detection into `booth_lineage`, swing computed on the lineage group and flagged in the UI; crosswalk editor accept/reject/reassign/split/merge with an audit row each; `review_queue` inserts deduped on `(kind, ref)` (C16); `--anchor` refuses a non-empty `booth` for that AC without `--re-anchor`, and re-anchor routes through the crosswalk (B5) | todo |
| A-3.7 | B1/B4/D7: `election_roll_link` seeded and written; `result_booth_meta.electors` populated from the linked roll snapshot (fallback PS list, **never** Form 20); `/rolls/changes` joins the latest mother roll at or before the change revision | todo |
| A-3.8 | Roll composition: supplement section state carried across PS groups (C8); per-entry deletion reason, single-letter patterns dropped (C9); attribute-vs-EPIC count mismatch raises or queues (C7); checks `male+female+other == electors`, age bands sum within tolerance, `other/electors < 0.02` — **fail the load, not warn**; `check_roll_continuity` over mother rolls only (C12); `source_doc`/`source_page` on `roll_snapshot` and `roll_change` (C15) | todo |
| A-3.9 | D2 `swing_pct`/`swing_votes` NULL when there is no prior election; D9 negative swing rows emitted for parties that vanished; D4 `floating_pct` NULL when only one poll type exists that year, and validate warns when an LS election has results but no crosswalk | todo |
| A-3.10 | §3.4 caste: D8 rescale fixed (SC/ST to target, then scale only non-SC/ST members); explicit `UNMATCHED` bucket, `est_pct` over all electors, separate `matched_pct`, no proportional extrapolation of the residual; religion bucket with its own confidence; `ac_id` + `method_version` on `caste_estimate`; surname dictionary expanded toward ~300 with Kurmi/Mahato, Yadav, SC (Turi, Rajwar, Bhuiyan, Dusadh, Ravidas, Baitha), ST (Santhal, Munda, Oraon, Bedia) in both scripts, every row with `source` and `weight`, ambiguous surnames split (C14) | todo |
| A-3.11 | §3.5 scenario: per AC per contest pair; baseline = that AC's `is_baseline`; transfer matrix on the **target event's alliances**; winner = argmax excluding NOTA (D5); noise applied to source totals and transfer rows **before** renormalisation, output labelled "sensitivity range at noise=x" (D6); inputs echoed verbatim; response carries `baseline_source` and the AC's `verified` flag | todo |
| A-3.12 | §3.6 `parse_form20 --type LS`: read the PC-level Form 20, take only the target AC's polling stations by AC header section, write an `election` row for the AC segment, candidates shared via `pc_candidate` | todo |
| A-3.13 | B10: parsers advance `source_doc.parse_status` through `new → extracted → parsed → validated → loaded \| failed \| drifted` with a timestamp and actor at each transition | todo |
| A-3.14 | `validate.py` recomputes headline AC metrics from base tables and asserts equality with the MV values **and** the published ECI values (Giridih 2024 `margin_pct` = 1.85) | todo |
| A-3.15 | `docs/METRICS.md` — §3.2 table verbatim plus the SQL location of each metric | todo |
| **Gate A-3** | 7 automated checks in `tests/e2e/` against a real Postgres: zero-error Form 20 load with every column resolved; AC totals equal ECI to the vote; `margin_pct` = 1.85; re-run leaves MVs byte-identical (`md5(string_agg(...))`); one hand-verified booth from the PDF; `check_no_dropped_booths` for VS-2024 and VS-2019; `mv_new_voter_share` additions = `SUM(roll_change.additions)`; turnout non-NULL for every booth with a linked roll; privacy scan clean after a roll load | **tests writable, not runnable here (§1)** |

### A-4 · UAT scaffolding (master prompt §2 A-4)
| # | Task | Status |
|---|---|---|
| A-4.1 | `scripts/uat_seed.sh` — idempotent: migrations, seeds, Giridih loads (from `raw/` if present, else fixtures), refresh, validate. One command from empty volume to UAT state | todo |
| A-4.2 | `scripts/uat_users.py` — one user per role (`admin`, `strategist`, `block` scoped to Pirtand), one-time passwords printed | todo |
| A-4.3 | `UAT_SCRIPT.md` — numbered human test cases, each with steps / expected result / pass-fail box, covering: login per role and what each role can and cannot see; AC switching; Giridih overview vs ECI; booth table filter/sort/export; booth-card source-page link opening the right PDF page; map colours signed by winner and the filters; voters page; caste confidence greying and block-role denial; crosswalk review queue end to end; honest "not loaded" states on the other five ACs; news stream; an alert rule firing; a deliberately bad upload being rejected | todo |
| A-4.4 | Walk the cases via the API and headless Playwright where practical; record results in `UAT_READINESS.md` | blocked on a running stack (§1) |
| **Gate A-4** | `uat_seed.sh` completes on a fresh volume and cases 1–N pass | **blocked on Docker (§1)** |

**Tag `uat-1-candidate` when Track A is green.**

### Tests the master prompt §5 requires (written alongside the work above, not after)
Security · Privacy · Form 20 · Metrics (every row of §3.2 incl. every NULL rule) · Crosswalk
(bands, split/merge lineage, per-AC UID sequence never collides, `--anchor` refusal) · Roll
(composition, supplement section state, per-entry deletion reason) · Multi-AC (same PS number in
two ACs never collides, every route rejects a missing/unknown AC, MVs partitioned by AC) ·
Scrapers · Scenario · Performance.

---

## 4. Track B — next layer. **Not started until Track A is tagged.**

| # | Item | Audit IDs | Status |
|---|---|---|---|
| B-1 | Scraper layer (spec §5): `scrapers/base.py` `Fetcher`, `sources.yaml` schema, tag-path-sequence fingerprint with `drifted` status + alert, `parse_status` lifecycle, recorded fixtures per source with a parse test each, watchers per active AC | — | not started |
| B-2 | Live news (spec §4): 30-min crawl, per-AC keyword sets, hourly Haiku micro-batches (optional key; unlabelled + keyword-tagged without one), strict pydantic label schema, YouTube/Telegram behind flags and off by default, `alert_rule` + `notify.py` (Telegram/webhook/log), job failures routed through it | G4 | not started |
| B-3 | Extended ground data (spec §3, §6): `0014_extended_data.sql`, CSV loaders + `docs/CSV_FORMATS.md`, optional MyNeta/TCPD/Mission Antyodaya/Census parsers, shared `common/pii.py` on every free-text write path, role gates, encrypted admin-only contact field with 90-day purge, read-only endpoints and "coming soon" placeholders | E5, B8 | not started |
| B-4 | UI (spec §7): 13 pages in the given order, each with loading/error/empty-with-CLI-command states, Hindi default, `en-IN` grouping, AC-aware URLs | F1, F2, F3, F4, F5 | not started |
| B-5 | Performance: the five p95 targets, pre-built GeoJSON blob per AC per metric set with `ETag`, `booth_card_cache`, in-process scenario matrices, all MVs refreshed `CONCURRENTLY`, `scripts/bench.py` printing the table into `UAT_READINESS.md` | E6 | not started |
| B-6 | Ops: G1 off-host backup + `scripts/restore_drill.sh` (run once, date recorded); G3 JSON logs with request id + compose log rotation; G4 notifications; A7–A11 cleanup; `passlib` → `bcrypt`; `web/.dockerignore`; self-hosted fonts; remove vega | G1, G3, G4, G5, A4, A7–A11 | not started |

---

## 5. Audit ID ledger — every ID must end `fixed` / `deferred (reason)` / `not reproducible (evidence)`

| ID | Where handled | Status |
|---|---|---|
| A1 | A-1.5 | todo |
| A2 (TLS) | **likely deferred** — needs a domain and a host; will propose terminating TLS upstream and not publishing 443 from the container | todo |
| A3 | A-1.6 | todo |
| A4 | B-6 | todo |
| A5 | A-1.7 | **fixed** `f7e5f73` |
| A6 | A-0.1–A-0.3 | **fixed** `1b0efed` |
| A7 | A-0.4 | **fixed** `760a244` |
| A8, A9, A10, A11, A12 | B-6 | todo |
| B1 | A-3.7 | todo |
| B2 | A-3.6 | todo |
| B3 | A-2.2 (structural) | todo |
| B4 | A-3.7 | todo |
| B5 | A-3.6 | todo |
| B6 | A-2.1 | todo |
| B7 (no down-migrations) | **proposed deferred** — forward-only is a deliberate design; mitigation is the backup + restore drill in B-6 | todo |
| B8 | B-3 | todo |
| B9 | A-2.1 | todo |
| B10 | A-3.13 | **partly fixed** `252d6bd` (lifecycle + the three parsers; scrapers in B-1) |
| B11 | A-2.1 | todo |
| C1, C2 | A-3.2 | todo |
| C3 | A-1.2, A-1.4 | todo |
| C4, C5, C6, C17 | A-3.5 | todo |
| C7, C8, C9, C12, C15 | A-3.8 | todo |
| C10, C11, C16 | A-3.6 | todo |
| C13 | A-1.3 | **fixed** `33a51d3` |
| C14 | A-3.10 | todo |
| D1 | A-3.3 | todo |
| D2, D4, D9 | A-3.9 | todo |
| D3 | A-3.2 | todo |
| D5, D6 | A-3.11 | todo |
| D7 | A-3.7 | todo |
| D8 | A-3.10 | todo |
| E1 | A-1.1 | todo |
| E2 | A-1.7 / B-3 role gates — booth card threads the caller's `User` and applies `sees_caste` + block scope | todo |
| E3 | A-1.10 | **fixed** `f7e5f73` |
| E4 | A-1.9 | todo |
| E5 | B-3 (`common/pii.py`) | todo |
| E6 (no pagination) | **proposed deferred** — bounded by dataset size; B-5 addresses the hot paths | todo |
| E7 (SMS stub) | **proposed deferred** — no SMS provider credentials; will make `AUTH_MODE=otp` refuse to start rather than return `{"sent": true}` | todo |
| E8, E9 | A-2.8 (route rework touches both) | todo |
| F1, F2, F3, F4, F5 | B-4 | todo |
| F6 (JWT in localStorage) | **proposed deferred** — standard SPA trade-off; revisit with A2 | todo |
| G1, G3, G4, G5 | B-6 | todo |
| G2 | A-1.11 | todo |
| G6 (`demo_api.py` CORS) | B-6 — delete the file or fix the header; it is unreferenced scaffolding | todo |
| H (no SQL under test) | A-3 `tests/e2e/` + §5 test matrix | todo |

---

## 6. Open decisions that need the user

1. **Database and Docker (blocking for the Track A gates, not for the work).**
   No Docker, no Postgres, no admin rights here. I can write and unit-test everything; I cannot
   apply a migration, refresh a view, or prove a single number. Options: (a) proceed and mark every
   DB-dependent gate `NOT VERIFIED HERE` with the operator command — the honest default;
   (b) you install Docker Desktop or a PostgreSQL 16 + PostGIS + pgvector server and I run the real
   gates; (c) you give me connection details for a Postgres reachable from this machine.

2. **Scope for one session.** Track A as specified is a substantial rebuild of the data layer
   (party resolution, one metrics module, crosswalk write path, roll composition, multi-AC schema
   with a data migration, plus the test matrix). Track B adds a scraper framework, a news pipeline,
   two migrations of new tables, 13 UI pages and a benchmark harness. The master prompt is explicit
   that a green Track A with Track B untouched is success — I will hold that line and not start
   Track B early.

3. **Real Form 20.** `ceo.jharkhand.gov.in` answered a HEAD request, so acquisition looks feasible.
   If discovery fails or the document is scanned beyond Tesseract's reach, A-3 runs on a recorded
   fixture and everything downstream is marked `PARTIAL — fixture only`. Flagging now so it is not
   a surprise later.

---

## 7. Change log

| When | Commit | What |
|---|---|---|
| 30 Sep 2026 | — | State detection complete; plan written; approved |
| 30 Sep 2026 | `1b0efed` | Baseline committed. 166 files, no secret staged. A6 closed. |
| 30 Sep 2026 | `283cd90` | Plan and decision log committed |
| 30 Sep 2026 | `760a244` | A7 closed. Fallback Jaro-Winkler diverged from production by 0.12 on the abbreviation case the crosswalk exists to handle. 96 to 181 tests. |
| 30 Sep 2026 | `33a51d3` | Storage backends, `0013`, SQL lint. Rolls barred from remote by guard and CHECK constraint. C13 closed. 181 to 226 tests. |
| 30 Sep 2026 | `f7e5f73` | Worker-less API confirmed and tested. Two pages were not reading from the DB at all; `/health` reported healthy with the DB down. A5, E3 closed. 226 to 258 tests. |
| 30 Sep 2026 | `252d6bd` | Laptop ingestion: `--doc`/`--key`, storage-backed fetch, preflight. B10 partly closed. 258 to 278 tests. |
| 30 Sep 2026 | — | `UAT_READINESS.md` written as an interim report (line 1: NOT READY) |
| 30 Sep 2026 | `eeccb19` | **Item 1: E1.** The empty-key admin login is closed; the API refuses to start on an empty, placeholder, short or degenerate secret. 278 to 300 tests. |
| 30 Sep 2026 | `5def601` | **Item 2: multi-AC.** Spine, `ac_id` everywhere, `32-B0147` from a per-AC sequence, all six ACs seeded (five `verified=false`), every route under `/acs/{ac}`, switcher and `/compare`. 300 to 347 tests. |
| 30 Sep 2026 | `ea466bf` | **Item 3a:** §3.1 party resolution and §3.2 canonical metrics, `docs/METRICS.md`, e2e harness. 347 to 454 tests. |
| 30 Sep 2026 | `448b456` | **Item 3b:** §3.5 scenario on alliances with argmax winner, §3.4 caste rescale and UNMATCHED. 454 to 466 tests. |
| 30 Sep 2026 | `8ffb603` | **Item 3c:** §3.3 crosswalk write path and lineage, §3.6 LS segment extraction. Item 3 complete. 466 to 499 tests. |
| 30 Sep 2026 | `0db8578` | **Logic fix:** party resolution requires a 0.05 margin over second place and a first-name-part match; a bracketed party resolves first. The specified rules alone did not reject `Sudhir Kumat`; the added first-part floor does. 499 to 510 tests. |
| 30 Sep 2026 | `c3e55c9` | **Item 4: dashboard.** Every page against fixtures that reconcile to 1.85%, provenance on every figure, NULL as an em dash with a reason. Overview + health strip, booth table, map fixes (F1/F2/F3), booth card tabs, caste scatter (F4), candidates, local politics. 510 to 571 tests. |
| 30 Sep 2026 | `934e629` | **C3 closed.** Roll page text never reaches disk; `--privacy` scans disk and every text/jsonb column; purge script; roll load refuses on a dirty disk. E5 closed alongside. 571 to 587 tests. |
