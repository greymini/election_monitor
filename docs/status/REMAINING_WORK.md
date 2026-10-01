# Remaining work

The work still to do, in the order it should be picked up. Each item says what is
needed to do it and where to start.

**Last updated:** 1 October 2026, at the end of the restructure / bug-fix / test pass on
branch `restructure-and-tests`.

**State at that point**
- The repo is split into `backend/`, `frontend/` and `docs/`.
- Both OneDrive patches are ported.
- 50+ bugs are fixed (`KNOWN_ISSUES.md`).
- Tests: backend 1,403 passed / 7 skipped; Vitest 61; Playwright fixtures 42.
- The local full stack (`make dev-stack`) runs end to end on mock data.

Related documents: open defects are in `KNOWN_ISSUES.md` (O-xx); how to run is in
`../GETTING_STARTED.md`; tests are described in `../TESTING.md`.

---

## 1. Finish the test plan (interrupted here)

This pass was stopped partway through Phase 4. Each item lists what is not yet written.

| # | Task | Notes |
|---|---|---|
| 1.1 | **Extend the live Playwright suite.** `frontend/e2e/live/smoke.spec.ts` exists (every page × role, drawer, boundaries, scenario, CSV, review queue - 8 tests, green). Still to add: AC switching mid-session via the switcher, an expired session returning to login, hard refresh on nested routes, three viewport widths, Hindi. | Against `make dev-stack`; logins from `backend/.devstack/users.json`. |
| 1.2 | **Page-level Vitest tests** for the pages still without one. | Pages: Overview, MapExplorer, Caste, CasteScatter, Transfer, LocalPolls, Factors, Scenario, Compare, Candidates, LocalPolitics, Admin, Login.<br>Each needs four cases: renders data, empty state, error state (route returns 500), and refetch on AC change. `src/test/server.ts` (fixture-backed fetch mock) and `src/test/render.tsx` (`renderWithProviders`, `fakeAc`) are ready to use; `src/pages/__tests__/` has examples.<br>MapExplorer needs `react-leaflet` mocked in jsdom. |
| 1.3 | **Component tests.** | Components: DataTable (sorting, row count), NavMenu (keyboard), AcSwitcher, StatTile, the two legends, ChatPanel rendering. |
| 1.4 | **Backend unit tests** for modules that have none. | • `chatbot/{agent,budget,llm}`: mock the Anthropic client; check the tool loop, the budget cap and refusals.<br>• `news/{label_batch,label_collect,embed}`: mock the client and the model.<br>• `ingest/{fetch_ceo,geocode}`: `httpx.MockTransport` plus recorded HTML.<br>• `ingest/parse_pslist` pure functions.<br>• `common/{jobs,logging_setup}`.<br>• `scripts/preflight.py` smoke test. |
| 1.5 | **Contract snapshot between fixtures and the API.** | A DB test records the key set of every GET response to `frontend/src/fixtures/contract.json` (`--update-contract`), and a Vitest test checks every fixture has those keys. This catches drift like R-18 and R-29 automatically. |
| 1.6 | **pytest markers.** | Register `db` and `slow` in `backend/pyproject.toml` and mark `tests/e2e/*`, so the Makefile can use `-m "not db"` instead of path filters. |
| 1.7 | **CI.** | Nothing runs the suites automatically. A GitHub Actions workflow should run `make test-backend test-db test-frontend test-e2e lint` on every push. pgserver works on Linux runners. |

## 2. Security and correctness fixes still open

| # | Task | Ref |
|---|---|---|
| 2.1 | Rate-limit `/auth/login`, `/auth/otp` and `/auth/verify`: 5 per minute per phone and per IP, with backoff and a constant-time path for unknown phones. In-process works for one API worker; use Redis or Postgres if `--workers > 1`. | O-01, audit E4 |
| 2.2 | Form 20: send unreadable numeric cells to the review queue, never 0. Add a unique index on `(election_id, column_index)` for candidates. | O-04, O-05 |
| 2.3 | `validate.check_roll_continuity`: partition by `ac_id`. | O-06 |
| 2.4 | Scenario page: build slider labels from the AC's contest pair. AC switcher: hide ACs the user cannot access. | O-09, O-10 |
| 2.5 | Finish i18n: move ~60 hardcoded English strings into `en.json`/`hi.json` and get the Hindi reviewed. | O-08 |
| 2.6 | Rewrite `docs/operations/RUN.md` against the real CLIs (`python -m <module> --help`). | O-13 |

## 3. Real data (the most important item)

**Giridih's VS-2024 and VS-2019 Form 20s are loaded** (2 Oct 2026, from the
extracted tables in `backend/db/seed/form20/`, via `ingest.load_form20_tables`).
Every result figure for those two elections is now the ECI's. What is still
missing is everything a Form 20 does not contain: where each station is, who is
on its roll, and the parties of eleven 2024 candidates.

| # | Task | Resources needed |
|---|---|---|
| 3.1 | ~~Giridih VS-2024 Form 20.~~ **Done**: margin 3,838 (1.85%), 367 stations, reconciled to the printed EVM, postal and total rows. **Still needed:** the 2024 polling-station list (names, buildings, locations, wards/panchayats). Until then the 367 booths sit in a placeholder block "PS list not loaded" (3299) and have no map position. A person should also check one booth against the original Form 20 PDF page, because the xlsx is an extraction. | 2024 PS list (CEO Jharkhand / DEO Giridih). Original Form 20 PDFs for the hand check. |
| 3.2 | ~~VS-2019 Form 20.~~ **Done**: margin 15,884, mapped to 2024 by PS number (r = 0.96; 367 crosswalk rows at 0.95, unreviewed, evidence in `crosswalk_audit`). **Still needed:** review that mapping against the 2019 and 2024 PS lists, and load LS-2024 for the AC segment. | 2019 PS list; LS-2024 Form 20 (Giridih segment). Someone to review the crosswalk. |
| 3.1a | Party affiliation for the 11 VS-2024 and 10 VS-2019 candidates shown as "party not in source". Add rows to `backend/db/seed/form20/candidate_parties.csv` with a source, then re-run `python scripts/build_form20_seeds.py` and reload. | ECI candidate list / Form 7A, or the affidavits on MyNeta. |
| 3.1b | Electors per AC: the 3,04,898 (2024) and 2,64,814 (2019) figures are secondary. Turnout depends on them. | ECI statistical report for the two elections. |
| 3.3 | Electoral roll counts (mother roll and supplements), linked to elections with `parse_roll --link-election`. | CEO Jharkhand roll PDFs (Hindi, often scanned). **Tesseract with `hin` + `eng` traineddata and poppler (`pdftoppm`)** on the machine that parses. |
| 3.4 | The five other ACs (31, 33, 42, 61, 65): verify the seeded totals against ECI and clear `verified=false`; then load their Form 20s and PS lists. | ECI results pages; a person to check each figure. |
| 3.5 | Panchayat names (`db/seed/areas_panchayats.csv` is header-only for every AC). These come from the PS lists. | The PS lists from 3.1 / 3.4. |
| 3.6 | Local election results (SEC Jharkhand), transcribed to CSV: `python -m ingest.fetch_sec --load-csv … --election PANCHAYAT-2022 --ac 32`. | jharkhandsec.gov.in results; manual transcription. |
| 3.7 | Census 2011 village data, for the community-estimate blend: `python -m ingest.load_csv demography FILE --ac 32`. | Census 2011 PCA + Village Directory, transcribed to CSV. |
| 3.8 | Candidate profiles and local office holders: `python -m ingest.load_csv candidate_profile|local_office_holder FILE --ac 32` (added on `rahul-working`). Organisations and political events still have no loader. | MyNeta/ADR affidavits, TCPD Lok Dhaba; manual transcription to CSV. |
| 3.9 | Geocoding booths (`python -m ingest.geocode`). | Nominatim (free, 1 request per second; set `NOMINATIM_USER_AGENT`). |

## 4. Features specified but not built

| # | Feature | Where specified |
|---|---|---|
| 4.1 | Pydantic response models on every route, OpenAPI-generated TS types (`openapi-typescript`), and fixtures validated against them. | FRONTEND_HARDENING §2 |
| 4.2 | Constituency in the page URL (`/acs/32/booths`) instead of `?ac=`. | LLD §8, finding N13 |
| 4.3 | Scraper layer: `sources.yaml`, structure fingerprints, drift detection, portal watchers beyond the supplement check. | MULTI_AC spec §5, LLD §5.4 |
| 4.4 | Track B tables and their UI: influencer registry, alert rules + Telegram notifications, area indicators, poll-day turnout, issue log, worker (party-worker) form. | MULTI_AC spec, `0017_ground_level_data.sql` notes |
| 4.5 | Booth drawer **News** and **Ground** tabs (placeholders now). Ground-report endpoints exist but no UI calls them. | spec §7.4 |
| 4.6 | Admin: sources tab, crosswalk editor, surname dictionary editor (the endpoints exist). | LLD §7, RUN.md §6 |
| 4.7 | OTP login: the UI and a real SMS sender. Today `AUTH_MODE=otp` only logs the code. | audit E7 |
| 4.8 | `db.link_roll` CLI. Today `parse_roll --link-election` does this. | RUN.md |
| 4.9 | Chatbot: parked behind `CHAT_ENABLED=false`. Before enabling it: AC-scope the tools, render charts, add tests for 1.4. | HLD §9 |
| 4.10 | Self-host fonts and add a CSP. | audit A11 |

## 5. Deployment (documented only, by decision)

**Option A: one VPS with Docker Compose.**
- Run `docker compose build` once; it has not been run since the restructure (O-19).
- Add TLS: an nginx `listen 443` block and a certbot service (A2).
- Set up an off-host backup copy and a restore drill (G1).
- Resources: a 4 vCPU / 8 GB VPS, a domain, and B2/S3 storage for backups.

**Option B: hosted demo.** This is the plan in `NEXT_STEPS.md` Step 3:
- Supabase: Postgres with `vector`, connecting through the session pooler over SSL.
- Railway: the API, listening on `$PORT`.
- Vercel: the frontend, with `VITE_API_BASE` set to the API.

Code still needed for option B:
- `frontend/vercel.json` with SPA rewrites;
- `CORS_ORIGINS` handling (the variable is currently `API_CORS_ORIGINS`);
- `$PORT` support;
- psycopg `prepare_threshold=None` for the pooler;
- a script that loads the mock dataset into any `DATABASE_URL` (`dev_stack.py` does this only for the embedded server);
- a `DEPLOY.md`.

Resources: Supabase, Railway and Vercel accounts. Note that free Supabase projects pause after a week idle.

**Map tiles:** switch `VITE_TILE_URL` to MapTiler, Thunderforest or similar (a key is needed) before regular team use.

## 6. Resources summary

| Kind | Item | Required? |
|---|---|---|
| Data | ECI Form 20 (VS-2024, VS-2019, LS-2024 segments), polling-station lists, CEO mother rolls and supplements, for six ACs | Yes |
| Data | SEC panchayat/ULB results; Census 2011 PCA + Village Directory; MyNeta/ADR; TCPD; LGD codes | For the related pages |
| People | Verify five ACs against ECI; review the crosswalk; review Hindi strings; check one booth per load against its PDF | Yes |
| Tools | Tesseract (hin+eng) and poppler, for scanned rolls | For roll loading |
| Keys | `ANTHROPIC_API_KEY` | News labelling; chat if re-enabled |
| Keys | Map tile provider key (MapTiler / Thunderforest) | Before regular use |
| Keys | Telegram bot (alerts), SMS provider (OTP), YouTube API | Optional, for unbuilt features |
| Infra | PostgreSQL 16 + pgvector; a VPS or Supabase/Railway/Vercel; S3/B2 bucket for documents and backups | For any deployment |
