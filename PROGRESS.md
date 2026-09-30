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

## 5. Audit ID ledger — all 70 findings

Every ID in `AUDIT_REPORT.md`, with its status now. `fixed` carries the commit that
closed it. Real Form 20 loading is deferred and will be mocked, so nothing here is
blocked on obtaining a document.

**Of the 70 lettered findings: 32 fixed · 9 partial · 29 open.** Of the 29 open, 5 are deployment items the
operator has scoped out, 8 are ops work not yet started, and 4 are proposed deferrals —
leaving 12 that this batch closes. Section H adds 9 testing items: 3 fixed, 2 partial,
4 open. Three findings from this batch that are not audit IDs are listed as N1–N3 below.

### A · Structure and build (12)

| ID | Severity | What | Status |
|---|---|---|---|
| A1 | Critical | Compose pins `pgvector/pgvector:pg16`, which has no PostGIS, so migration 0001 fails and a clean checkout cannot start | **open** — next item of work: a DB image so the SQL tests can run locally |
| A2 | High | Port 443 published and a certs volume mounted, but nginx has one `listen 80` block and no certbot exists | **open** — deployment, scoped out |
| A3 | High | Worker mounts volumes at paths absent from the image, so Docker creates them root-owned and every write fails | **open** — needs a live host to verify; deployment |
| A4 | Medium | No `web/.dockerignore`, so the host's `node_modules` enters the build context | **open** — deployment |
| A5 | Medium | No chatbot feature flag; mounting the router made `sqlglot` a hard import requirement of the whole API | **fixed** `f7e5f73` |
| A6 | Medium | No version control anywhere under `election_monitor/` | **fixed** `1b0efed` |
| A7 | Medium | `jellyfish` absent from everything `requirements-dev.txt` pulled in, so every crosswalk threshold test ran against a fallback that disagreed with production by up to 0.12 | **fixed** `760a244` |
| A8 | Low | `pandas`, `scipy`, `pypdf`, `rapidfuzz`, `python-multipart`, `vega` declared and unused | **open** — `boto3` was added for storage; nothing removed yet |
| A9 | Low | `POSTGRES_HOST`/`PORT`, `SMS_API_KEY`/`SENDER_ID` documented and read by nothing | **open** |
| A10 | Low | `./db/migrations:/migrations:ro` mounted into the db container and read by nothing | **open** — deployment |
| A11 | Low | Fonts fetched from `fonts.googleapis.com` on every page view; no CSP | **open** |
| A12 | Low | Stub audit — the finding was that the codebase is clean; one bare `pass` in `chatbot/agent.py` | **partial** — two pre-existing lint findings in `scripts/demo_api.py` fixed `f7e5f73`; the bare `pass` remains, chatbot untouched by instruction |

### B · Data model and migrations (11)

| ID | Severity | What | Status |
|---|---|---|---|
| B1 | Critical | Nothing writes `election_roll_link`, so `mv_new_voter_share` reported 0 additions everywhere while another screen showed the real numbers | **partial** — the metric returns NULL rather than 0 (`ea466bf`) and the view reads the link table; **seeding and using the link is item 3 of this batch** |
| B2 | Critical | Review-band matches got no `booth_crosswalk` row, and every view inner-joins it, so those booths' votes vanished silently | **fixed** `8ffb603` |
| B3 | Critical | `next_uid_start=0` hardcoded, so a second crosswalk run reused the first's booth UIDs and summed unrelated stations onto one booth | **fixed** `5def601` (per-AC sequence, structural) + `8ffb603` (write path) |
| B4 | High | `result_booth_meta.electors` has no writer, so turnout is NULL everywhere it appears | **partial** — turnout is correctly NULL with a stated reason rather than 0; **populating electors is item 3 of this batch** |
| B5 | High | `parse_pslist --anchor` re-mints `booth_uid` from a new PS list, silently rebinding every FK keyed on it | **partial** — UIDs no longer derive from PS numbers (`5def601`), which removes the mechanism; **the `--re-anchor` guard is item 6 of this batch** |
| B6 | Medium | `booth_crosswalk.election_id` and `ps_list_entry.election_id` had no FK | **fixed** `5def601` |
| B7 | Medium | Forward-only migrations, no rollback path | **open** — proposed deferral: forward-only is deliberate; mitigation is the backup and restore drill (G1) |
| B8 | Medium | `demography` and `caste_survey` are read and written by nothing — no Census loader, no survey intake | **open** |
| B9 | Medium | `is_baseline` had no uniqueness constraint; a second baseline doubles every scenario total | **fixed** `5def601` |
| B10 | Low | `parse_status` never advanced past `extracted`; `parsed_at` never set | **partial** — lifecycle, columns and the three parsers done (`252d6bd`); scrapers are Track B |
| B11 | Low | `roll_revision` upsert targeted `(label)` while the constraint was `(revision_date, is_mother)` | **fixed** `5def601` |

### C · Ingestion and data correctness (17)

| ID | Severity | What | Status |
|---|---|---|---|
| C1 | Critical | Form 20 columns never resolved to a party — the lookup never consulted `name_hi` — so every candidate loaded unattributed and every booth reported a 100% margin | **fixed** `ea466bf`, tightened `0db8578` |
| C2 | Critical | AC-total validation keyed on `"Name (ABBR)"` against raw header cells, so it never matched and the operator had to disable the check to load anything | **fixed** `ea466bf` — resolution matches onto the seeded candidates, which is what the totals hang off |
| C3 | Critical | Every roll page's full text written to `OCR_DIR` as plaintext JSON and kept, while the source PDF was deleted | **fixed** `934e629` |
| C4 | High | `parse_int(c) or 0` turns an unreadable cell into zero votes, and the arithmetic check that would catch it is skipped when the same damage hit the total | **open** — **item 4 of this batch** |
| C5 | High | The load docstring says one transaction; it is three, so a mid-load failure leaves committed deletes and partial rows | **open** — **item 4 of this batch** |
| C6 | High | Candidate uniqueness on `(election_id, name_en, party_id)` with NULL party never fires, so every re-parse inserts fresh candidates and doubles every vote total | **open** — **item 4 of this batch** |
| C7 | High | Gender and age read from a different line than the EPIC count, so a layout mismatch silently makes every elector "other" and all age bands zero | **open** — **item 5 of this batch** |
| C8 | Medium | Supplement section state resets per PS group, so every group after the first records 0 additions | **open** — **item 5 of this batch** |
| C9 | Medium | One deletion reason applied to every entry on a line, and `\bS\b` matches the S in S/O | **open** — **item 5 of this batch** |
| C10 | Medium | `booth` carried no `roll_part`, so the documented 0.20 crosswalk term never contributed | **fixed** `5def601` (column) + `8ffb603` (supplied and scored) |
| C11 | Medium | Crosswalk coverage measured against `booth_crosswalk`, which is circular — the dropped rows were missing from the denominator too | **fixed** `ea466bf` — `mv_ac_summary` measures against `ps_list_entry` |
| C12 | Medium | `check_roll_continuity` compares consecutive revisions including supplements, which write no snapshots, so it fails loudly on correct data | **open** — **item 5 of this batch** |
| C13 | Medium | `discard_raw` deletes the source PDF by default, destroying the audit trail while the cache kept the text | **fixed** `33a51d3` + `934e629` |
| C14 | Medium | Surname percentages normalised over matched tokens then multiplied by the full electorate, biasing against under-covered communities | **fixed** `448b456` — shares over all electors, explicit `UNMATCHED` |
| C15 | Medium | `roll_snapshot` and `roll_change` record no source document or page | **partial** — columns added `5def601`; **writers are item 6 of this batch** |
| C16 | Low | `review_queue` inserts had no dedupe, so the queue doubled on every re-run | **fixed** `5def601` (unique index) + `8ffb603` (upsert) |
| C17 | Low | Pages that produced some rows via the table path are excluded from the regex fallback | **open** — **item 6 of this batch** |

### D · Analytics correctness (9)

| ID | Severity | What | Status |
|---|---|---|---|
| D1 | Critical | Three mutually inconsistent totals in one row; the margin divided by the NOTA-excluding total while displaying the NOTA-including one, giving 1.87% against a published 1.85% | **fixed** `ea466bf` |
| D2 | High | `LAG` COALESCEd to 0, so the earliest loaded election reported each party's entire vote share as its swing | **fixed** `ea466bf` |
| D3 | High | Ranking grouped on `COALESCE(party_id, -1)`, so independents competed as one bucket that could outrank a real winner | **fixed** `ea466bf` |
| D4 | Medium | Pedersen index over one poll type gives exactly 50.00, reported for every booth in the constituency | **fixed** `ea466bf` |
| D5 | Medium | Scenario winner was `jmm if margin >= 0 else bjp` with the pair hardcoded, so the response contradicted its own votes dict | **fixed** `448b456` |
| D6 | Medium | Noise multiplied then renormalised, cancelling exactly on identity rows, so the band came from one arbitrary constant on one row | **fixed** `448b456` |
| D7 | Medium | `/rolls/changes` joined `roll_snapshot` on an equal `revision_id`, which can never match, so electors and both percentages were always NULL | **fixed** `5def601` |
| D8 | Low | `_rescale_category` renormalised after rescaling, partly undoing it, so the documented blend was not what was computed | **fixed** `448b456` |
| D9 | Low | A party that vanished between elections has no later row, so its collapse never appeared opposite the winner's gain | **fixed** `ea466bf` — `mv_swing_vanished` |

### E · API layer (9)

| ID | Severity | What | Status |
|---|---|---|---|
| E1 | High | Empty `JWT_SECRET` accepted at startup; HS256 verification with an empty key succeeds, so anyone could mint an admin token | **fixed** `eeccb19` |
| E2 | Medium | `/chat`'s `get_booth_card` tool calls `build_booth_card` with caste included and no block filter, bypassing both role gates | **partial** — the route does not exist with `CHAT_ENABLED=false` (`f7e5f73`), and `build_booth_card` now takes `ac_id`; `chatbot/tools.py` still passes no user. Not live, and the instruction is not to touch chatbot logic |
| E3 | Medium | Chat SSE error path streamed `str(exc)[:200]`, leaking table and column names | **fixed** `f7e5f73` |
| E4 | Medium | No rate limit, lockout or backoff on `/auth/login`; timing side channel on unknown phone | **open** — **item 10 of this batch** |
| E5 | Medium | `POST /ground-reports` took 4,000 characters of free text with no PII screening and embedded it for vector search | **fixed** `934e629` |
| E6 | Low | No pagination on seven list endpoints | **open** — proposed deferral: bounded by dataset size |
| E7 | Low | `AUTH_MODE=otp` returns `{"sent": true}` from a sender that only logs | **open** — proposed deferral: no SMS credentials. Should at least refuse to start |
| E8 | Low | `/summary` returns AC-wide totals and review counts to block-role users with no block filter | **partial** — now AC-scoped (`5def601`); the block filter on `data_health` is still absent |
| E9 | Low | `Content-Disposition` built from an unsanitised path parameter | **partial** — filenames are now prefixed and constructed, but `election_label` still reaches the header |

### F · Frontend (6)

| ID | Severity | What | Status |
|---|---|---|---|
| F1 | High | Unsigned `margin_pct` fed to a diverging ramp, so only half the scale was reachable and a JMM hold rendered identically to a BJP hold | **fixed** `c3e55c9` |
| F2 | Medium | `/booths` accepted `election_label` and ignored it; no year, block or area filters | **fixed** `5def601` (API) + `c3e55c9` (UI) |
| F3 | Medium | Marker radius from `electors ?? 400` where electors was always NULL, so every marker was the clamped minimum under a caption claiming size meant electorate | **fixed** `c3e55c9` |
| F4 | Medium | The caste scatter plotted community share against a constant zero with the Y axis hidden | **fixed** `c3e55c9` |
| F5 | Low | `vega`, `vega-lite`, `vega-embed` declared and imported by nothing | **open** |
| F6 | Low | JWT in `localStorage` with a 12-hour TTL and no TLS | **open** — proposed deferral: standard SPA trade-off; revisit with A2 |

### G · Operations and security (6)

| ID | Severity | What | Status |
|---|---|---|---|
| G1 | High | Backups on the same host as the data; restore drill never run | **open** — deployment, scoped out |
| G2 | Medium | `pg_dump` receives the full connection URI in `argv`, so the password is visible in the process table | **open** — was listed in the A-1 plan and **not implemented**; verified still present in `worker/ops.py:35` |
| G3 | Medium | Plain-text stdout logging, no request id, no rotation | **open** |
| G4 | Medium | Job failures are recorded but never notified | **open** |
| G5 | Low | `common/jobs.already_done()` implements the documented idempotency key and is never called | **open** |
| G6 | Low | `scripts/demo_api.py` sets `allow_origins=["*"]` with credentials | **open** — unreferenced scaffolding; should be deleted |

### H · Testing

| Item | What | Status |
|---|---|---|
| H.1 | Materialized views against a hand-built fixture — the audit's top-ranked missing test | **partial** — written (`ea466bf`, 14 cases) but **NOT RUN**: no Postgres. Item 2 of this batch makes it runnable |
| H.2 | Crosswalk write path, not just scoring | **fixed** `8ffb603` |
| H.3 | Form 20 end to end against a known booth | **open** — **item 8 of this batch** (mocked, per instruction) |
| H.4 | Re-run idempotency per loader | **open** — **item 4 of this batch** |
| H.5 | Privacy assertion beyond the parser | **fixed** `934e629` |
| H.6 | Roll composition invariants | **open** — **item 5 of this batch** |
| H.7 | API contract tests for block-role scoping | **open** — **item 7 of this batch** |
| H.8 | `jellyfish` vs fallback agreement | **fixed** `760a244` |
| H.9 | Metric parity: `metrics.py` against `0015_metrics.sql` on the same fixtures | **partial** — the Python half and the drift checks run now (82 cases); the SQL half needs Postgres, which is item 2 |

### New findings from this batch

Not audit IDs — found while doing the work, recorded so they are not lost.

| ID | What | Status |
|---|---|---|
| N1 | An absent crosswalk means opposite things in the two implementations: SQL treats NULL confidence as "cannot compare", Python's `link=None` default means "nothing to gate on". A caller who forgets the link gets ungated swings — the shape of D2. `swing_pct` has no production callers yet. | **open** — documented in `docs/METRICS.md` and asserted in `test_metric_parity.py`; must be closed before the Form 20 loader computes swings |
| N2 | D3 recurs at AC grain: `mv_ac_summary` ranks over `party_totals`, where every independent shares a NULL `party_id` and collapses into one row, so the AC headline can name a winner the booth table does not. Does not affect Giridih 2024 (top two are both major parties). | **open** — belongs with item 9, the full per-AC candidate lists |
| N3 | `mv_swing_vanished` applied no crosswalk or lineage gate at all, so a vanished party's collapse was reported even at booths too weakly matched to carry the surviving parties' swings — the two halves of one swing table disagreeing about whether the comparison was admissible. | **fixed** — both halves now call `metric_comparison_allowed` |

### What the 29 open lettered items are, grouped

- **This batch will close:** A1, B1, B4, B5, C4, C5, C6, C7, C8, C9, C12, C15, C17, E4, H.3, H.4, H.6, H.7 — plus the parity test and mock generator, which are new work rather than audit IDs.
- **Deployment, scoped out by the operator:** A2, A3, A4, A10, G1.
- **Ops, not yet started:** A8, A9, A11, G2, G3, G4, G5, G6.
- **Proposed deferrals:** B7, E6, E7, F6.
- **Blocked on other work:** B8 (needs Census/survey loaders, Track B), E2 (chatbot is parked and not to be touched), F5 (trivial, bundled with A8).

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
| 30 Sep 2026 | `5314668` | **Audit ledger completed** — all 70 findings with a verified status. G2 was found open, not fixed: it was planned in detail and never implemented, which is the kind of item that gets remembered as done. |
| 30 Sep 2026 | `ed0a52a` | **Item 1: metric parity.** The §3.2 formulas now exist once, in `analytics/metric_sql.py`, and the ten views call generated functions instead of restating them — the margin quotient alone had been written out seven times, which is how D1 survived. Parity is three-way: hand-computed == Python == SQL. N3 fixed (`mv_swing_vanished` had no gate at all); N1 and N2 recorded. 587 to 669 tests. |
