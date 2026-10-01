# Claude Code prompt — fix, expand to multi-AC, harden scrapers, gate for UAT

Paste at the repo root. Both `AUDIT_REPORT.md` and `MULTI_AC_EXPANSION_SPEC.md` must be in the repo root first.

---

You are the engineer on this repository for the next session. Read `AUDIT_REPORT.md` and `MULTI_AC_EXPANSION_SPEC.md` completely before touching any file. The audit lists defects by ID (A1, B2, C3 …); the spec defines the multi-constituency data model, new data points, scraper rules and the UAT-1 scope. Your job is to make the codebase pass UAT-1 as defined in spec §8.

## Ground rules

1. **Work in this order, committing after each phase with a clear message.** If the repo is not a git repository, `git init` and commit the current state first as `chore: baseline before audit fixes`. Do not squash phases.
2. **Never write individual voter data anywhere** — not DB, not cache, not logs, not fixtures, not test output. If a change would persist a name, EPIC, address, relative's name or phone from an electoral roll, stop and redesign.
3. **Every ingestion path must be idempotent and transactional.** Re-running any loader on the same source must produce identical tables. A failed load must leave the DB exactly as it was.
4. **No fabricated numbers.** Where data is absent, emit `NULL` and let the UI show "not loaded", never 0, never 50%, never a default.
5. **Do not touch chatbot logic.** Only isolate it (Phase 1). It stays parked.
6. **Do not invent constituency facts.** Seed the five new ACs from `MULTI_AC_EXPANSION_SPEC.md §1` values but mark each seed row `verified=false` until a human confirms against ECI. Blocks/areas for the new ACs come from the CEO PS list when available; until then seed block names from the spec with `source='spec-unverified'`.
7. Keep the existing conventions (psycopg3, materialized views, APScheduler, React Query, i18n). Don't introduce new frameworks.
8. When you finish each phase, run the full test suite and `docker compose build`. Do not proceed if either fails.

## Phase 0 — Safety first (do these before anything else)

- E1: `api/main.py` lifespan must raise and refuse to start if `JWT_SECRET` is empty, shorter than 32 bytes, or equals any placeholder in `.env.example`. Verify the empty-key HS256 bypass with a test before and after.
- C3 + C13: `extract_pdf.extract_document` gains `cache: bool`; `parse_roll` calls with `cache=False`. Add a startup and validate-time filesystem scan that fails if any file under `OCR_DIR` matches the EPIC regex `[A-Z]{3}[0-9]{7}`. Add `scripts/purge_roll_cache.py` and run it. Retain raw roll PDFs under `raw/` with 0700 permissions (`RETAIN_RAW_ROLLS` default true); document why.
- A1: build `docker/Dockerfile.db` from `pgvector/pgvector:pg16` installing `postgresql-16-postgis-3`; pin it in compose; add a migration precondition that asserts both extensions.
- A3: `mkdir -p /data/raw /data/ocr /data/backups` before `chown` in `Dockerfile.worker`; verify with a compose run.
- A5: `CHAT_ENABLED` setting (default false). Guard `include_router(chat.router)`; move all `chatbot.*` imports inside handlers; the API must import and boot with `sqlglot` and `anthropic` absent. Frontend hides `ChatPanel` when `/config` reports `chat_enabled=false`. Add a test that imports `api.main` with `chatbot` removed from `sys.modules`.
- E4: rate-limit `/auth/login` (e.g. 5/min per phone and per IP, in-process is fine); constant-time path for unknown phone.
- E3: chat SSE error path returns flat "Internal error" like everywhere else.
- G2: `pg_dump` receives credentials via `PGPASSWORD` env, not argv.
- A6: `.gitignore` verified; commit.

Commit: `fix(security): JWT guard, roll cache removal, PostGIS image, chat isolation, login rate limit`.

## Phase 1 — Multi-AC data model (spec §2)

- New migration `0013_multi_ac.sql`: `state`, `district`, `pc`, `ac`, `ac_district`, `election_event`, restructure `election`, `party_alliance`, `party_alias`; add `ac_id` to every table listed in spec §2.2; `booth_uid` format `'{ac_number}-B{nnnn}'`; unique baseline per AC (B9); FKs on `booth_crosswalk.election_id` and `ps_list_entry.election_id` (B6); `roll_revision` conflict target fixed (B11).
- Write a data migration for existing Giridih rows (`ac_number=32`) so nothing is lost.
- Rebuild all MVs with `ac_id` leading; add `mv_ac_summary`.
- Seeds: `db/seed/ac.csv` (six ACs from spec §1 with `verified=false`), `blocks.csv`/`areas_*.csv` per AC, `party_alias.csv` (all Devanagari and English spellings you can find in the repo, ECI pages and Form 20 fixtures — this table is what makes C1 go away), `party_alliance.csv` per event (2009, 2014, 2019, 2024 VS and LS).
- Every API route and every SQL query takes/requires `ac_id` (or `ac_number`), and block-scoping is now `(ac_id, block_id)`. `/summary` becomes `/acs` (list with `mv_ac_summary`) and `/acs/{ac}/summary`.
- Frontend: AC switcher in `Layout`, stored in URL (`?ac=32`), all queries keyed by it. A `/compare` page over `mv_ac_summary`.

Commit: `feat(schema): multi-constituency spine, ac-scoped tables and views`.

## Phase 2 — Make Giridih's numbers correct (audit C, B, D)

Do these against a **real** Giridih 2024 Form 20 PDF. If none is in `raw/`, run `fetch_ceo` first; if the portal is unreachable, stop and report — do not proceed on synthetic headers.

- C1/C2/C6/D1/D3 together: `resolve_candidates` matches header cells to the AC's seeded `result_ac_total` candidates by fuzzy name (Devanagari-normalised, jaro-winkler ≥ 0.85) and resolves party via `party_alias`; abort the load if any column is unresolved. Load NOTA as a real candidate row (`party=NOTA`). One denominator everywhere: total valid votes incl. NOTA. Candidate uniqueness on `(election_id, column_index)`. Ranking CTE ranks real parties only; independents compete individually. Add `validate.py` check: AC-level `margin_pct` reproduces the published figure to 2 dp (Giridih 2024 → 1.85).
- C4: unparseable numeric cell → review queue, never 0.
- C5: one connection/transaction for the whole load; stage → validate → promote.
- B2/B3/C11: review-band matches write a `booth_crosswalk` row with real confidence and `reviewed=false`; UIDs minted from a sequence per AC; `crosswalk_quality` denominator = `ps_list_entry`. Add `check_no_dropped_booths`: every `result_booth` PS must reach an MV row.
- B5: `parse_pslist --anchor` refuses when `booth` is non-empty for that AC unless `--re-anchor`, and re-anchor routes through `crosswalk` instead of overwriting.
- B1/B4/D7: seed `election_roll_link` (VS-2024 → its roll revision) via a small CSV; populate `result_booth_meta.electors` from the linked roll snapshot (fallback PS list); `/rolls/changes` joins the latest mother roll at or before the change revision.
- C7/C8/C9: carry supplement section state across PS groups; per-entry deletion reason; add roll-composition checks (`male+female+other == electors`, age bands sum within tolerance, `other/electors < 0.02`) — fail the load, don't warn.
- C10: carry `roll_part` onto `booth`; include in crosswalk scoring.
- C12: `check_roll_continuity` over mother rolls only.
- C15: `roll_snapshot`/`roll_change` get `source_doc`, `source_page`.
- C16: dedupe `review_queue` inserts on `(kind, ref)`.
- C17: regex fallback runs on pages whose table extraction returned fewer rows than expected PS count.
- D2: `swing_pct`/`swing_votes` NULL when no prior election; D9: emit negative swing rows for parties that vanished.
- D4: `floating_pct` NULL when only one poll type exists that year; validate warns when an LS election has results but no crosswalk.
- D5: scenario winner = argmax excluding NOTA; D6: perturb before renormalising, label band "sensitivity range at noise = x".
- D8: fix `_rescale_category` so the documented blend is what's computed.
- B10: parsers advance `source_doc.parse_status` through the lifecycle in spec §5.7.

Acceptance (write as tests where possible, in `tests/e2e/` against a throwaway Postgres via `testcontainers` or the compose db):
- Giridih 2024 loads with zero validation errors; AC totals match ECI to the vote; margin = 1.85%.
- Re-running the load produces byte-identical MVs.
- Crosswalk 2019→2024: `check_no_dropped_booths` passes; quality metric uses the right denominator.
- One hand-verified booth's row in `mv_result_booth_wide` matches the PDF (choose a booth, record its numbers in the test).
- Privacy scan passes on `OCR_DIR` and on DB columns.

Commit: `fix(data): Form 20 party resolution, NOTA denominator, crosswalk write path, electors, roll composition`.

## Phase 3 — Scraper robustness (spec §5)

- `scrapers/base.py`: one `Fetcher` interface — config from `scrapers/sources.yaml` (per state/AC), structure fingerprint + drift detection, sha256 content addressing into `source_doc`, retries with backoff, per-host concurrency 1, polite delays, real User-Agent, `--dry-run`.
- Port `fetch_ceo`, `fetch_sec`, `crawl_rss` onto it. Add Playwright fallback (behind a flag; don't add it to the default image if it bloats it — a separate `worker-browser` profile is fine).
- Recorded fixtures in `tests/fixtures/scrapers/` for each source and a parser test per fixture.
- Watch jobs: `watch.ps_list`, `watch.roll_supplement`, `watch.form20`, `watch.sec_notice` per active AC → alert + auto-extract + review-queue entry.
- Alerts: `alert_rule` table + `notify.py` with a Telegram bot sender (token from env, optional) and a log fallback. Wire job failures (G4) and watcher hits through it.
- News: crawl interval 30 min; per-AC keyword sets in `sources.yaml`; labeller runs hourly micro-batches; items get `ac_ids[]`; add `event_candidate` to the label schema.

Commit: `feat(scrapers): config-driven fetch layer with drift detection, watchers and alerts`.

## Phase 4 — New data points, schema only + minimal loaders (spec §3, §6)

- Migration `0014_extended_data.sql`: `candidate_profile`, booth attribute columns, `poll_day_turnout`, `area_indicator`, `local_office_holder`, `influencer`, `influencer_area`, `organisation`, `political_event`, `worker_user` fields, `ground_report` extensions, `work_log`, `public_issue`, `issue_event`, `alert_rule`.
- CSV loaders (admin CLI) for `candidate_profile`, `local_office_holder`, `area_indicator`, `influencer`, `organisation`. Parsing MyNeta/TCPD/Mission Antyodaya exports is a bonus, not required for UAT.
- PII screen (E5) shared by `ground_report`, `public_issue`, `influencer.notes`, `political_event.detail`: reject EPIC, 10-digit phone, 12-digit Aadhaar patterns.
- Read-only endpoints for each new table, role-gated as in the spec (`influencer` strategist+).

Commit: `feat(data): extended ground-level data model and loaders`.

## Phase 5 — UI for UAT (spec §7, audit F)

- F1 sign the diverging ramp by winner; F2 year/block/area filters on the map and `/booths` honouring `election_label`; F3 marker size from real electors; F4 caste × party-share scatter with regression line via new `/caste/correlation`.
- **Booth table** as specified in spec §7.2 — column chooser, filters, sort, sticky header, CSV; source page link per row.
- Constituency overview with data-health strip; Candidates page from `candidate_profile`; Local politics page (read-only lists + event timeline); Compare page.
- Empty states for every not-loaded dataset name the CLI command.
- Remove vega packages (F5), add `web/.dockerignore` (A4), self-host fonts (A11).

Commit: `feat(ui): booth workhorse table, map filters, caste scatter, candidates, compare, overview health strip`.

## Phase 6 — Ops and cleanup

- G1: off-host backup upload (rclone or B2 API) + `scripts/restore_drill.sh`; run the drill once and record the date in `docs/RUNBOOK.md`.
- G3: JSON logging with request id; compose `logging:` block with rotation.
- A7/A8/A9/A10: fix dev requirements (`jellyfish`), drop unused deps, remove dead env vars and the dead migrations mount.
- Replace `passlib` with direct `bcrypt`.
- Update `README.md` for multi-AC and write `RUN.md` (a plain-language run guide — if one is already provided at repo root, reconcile with it rather than overwrite).

Commit: `chore(ops): off-host backups, structured logging, dependency cleanup`.

## Final report

Write `UAT_READINESS.md`: for every line in spec §8 "Must pass", state PASS / FAIL / PARTIAL with the command or test that proves it; list every audit ID with `fixed` / `deferred (reason)`; list anything you had to assume; list what's seeded `verified=false`. Be blunt. If Giridih's Form 20 could not be obtained and Phase 2 ran on a fixture, say so in the first line.
