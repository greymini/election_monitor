# Known issues

Every defect found in the code review of 1 October 2026 (branch `restructure-and-tests`), with
where it was fixed or why it is still open. Older audit IDs (A1…H9, N1…N16) live in
`AUDIT_REPORT.md` and `PROGRESS.md`; the OneDrive port's findings are `OD-N*` in
`PROGRESS.md` §8.

**Status key:** **Fixed** means a test that failed before the change now passes, in the commit
named. **Open** means it is not fixed; the reason is given. Paths are relative to the repo root.

---

## Fixed in this pass

### Crashes and broken core flows (P0) — commit `a381842`

| ID | Area | Symptom | Fix / test |
|---|---|---|---|
| R-01 | `frontend/src/App.tsx` | Signing in through the form, signing out, or any 401 blanked the app: `useAc()` ran after an early return. | Hooks before any return; cache cleared on sign-out; ErrorBoundary. `src/App.test.tsx` |
| R-02 | `backend/api/booth_card.py` | Booth card returned 500 for every booth (pre-0015 column names). Its shape also did not match the drawer. | 0015 names; `roll`, never-null `priority`/`new_voters`. `tests/e2e/test_booth_card.py` |
| R-03 | API, all routes | NUMERIC columns were serialised as JSON strings ("25.29"); `toFixed()` threw, so the Overview and the drawer could not render against a real API. | Pool loads numeric as float. `tests/e2e/test_api_json_types.py` |
| R-04 | `ingest/extract_pdf.py` | `--register` always failed: `storage_key` is NOT NULL since 0013. | `tests/test_extract_pdf_register.py` |
| R-05 | `ingest/fetch_sec.py` | Local results loaded against an arbitrary constituency (the label exists in six) and with no `ac_id`, so they never showed. | `--ac`, `ac_id` written. `tests/e2e/test_fetch_sec_load.py` |
| R-06 | `lib/api.ts` | A wrong password said "session expired"; a 422 showed "[object Object]". | `src/App.test.tsx` |
| R-07 | `scripts/dev_stack.py` | The macOS/Linux socket URL was mangled; the server died with "No module named 'api'". | `tests/test_dev_stack.py` |
| R-08 | Drawer | The dialog was labelled with the raw key "card.heading". | Key added |

### Wrong data, privacy, security (P1) — commits `dde94a7`, `4e7d8cb`, `cf030c4`

| ID | Area | Symptom | Fix / test |
|---|---|---|---|
| R-10 | Privacy: `ingest/ocr_tesseract.py` | A low-confidence OCR page of an electoral roll stored 1,500 characters (names, EPIC numbers) in `review_queue`, which the admin API serves. | No excerpt for personal documents. `tests/test_ocr_privacy.py` |
| R-11 | Review queue | 0018 made `ac_id` NOT NULL. Three writers with no AC then failed, and OCR failed silently. The daily supplement check re-inserted resolved items and hit the unique index. | Migration 0019; upserts. `tests/e2e/test_review_queue.py` |
| R-12 | `analytics/scenario.py` | A negative sympathy swing did nothing. A second roll link duplicated every booth in the projection. | `tests/test_scenario.py`, `tests/e2e/test_scenario_api.py` |
| R-13 | `mv_booth_priority` | A booth with a missing input was ranked near the top for it, inflating its priority score. | Migration 0020. `tests/e2e/test_views_0020.py` |
| R-14 | `mv_new_voter_share` | Two earlier mother rolls made the refresh fail (unique index). | Migration 0020 |
| R-15 | Read-only role | The chatbot's `giridih_ro` role lost SELECT on every view when 0014/0015 recreated them. | Migration 0021 |
| R-16 | News | Nothing tagged news to a constituency, so crawled news never appeared anywhere. The trend charts also mixed constituencies. | Crawl tags by place name. `tests/e2e/test_news_api.py` |
| R-17 | `/booths` | Any non-baseline election returned no booths. Booths appeared once per election that used their PS number. | `tests/e2e/test_booth_queries.py` |
| R-18 | `/results/{label}/booths` | A merged booth was repeated. Swing, new-voter, floating and volatility columns were missing, so the Booths page presets showed dashes. | Same test |
| R-19 | Legacy redirects | Absolute `Location` skipped the nginx `/api` prefix, and a `?_path=` query parameter could choose the target. | `tests/test_legacy_redirects.py` |
| R-20 | `/config` | Defaulted to AC 31, which has no data. | `tests/e2e/test_api_misc.py` |
| R-21 | Ground reports | An unknown booth returned 500. Another constituency's or block's booth was accepted. | Same test |
| R-22 | Auth | A token with a non-numeric subject returned 500. | Same test |
| R-23 | Chat (parked) | The booth-card tool always included community estimates, and `run_sql` was open to block users. | `tests/test_chat_tools_roles.py` |
| R-24 | Knowledge cards | Every re-seed reset the Giridih cards to "general", so they showed on every constituency. | `ac: 32` front matter. `tests/e2e/test_knowledge_cards.py` |
| R-25 | Worker image | `chatbot/` was missing, so news labelling crashed. Data directories were not created before use (audit A3). | `tests/test_docker_images.py` |
| R-26 | Backups | `pg_dump` got the password on its command line (G2). | `tests/test_ops_backup.py` |
| R-27 | Reference SQL | `analytics/metrics.sql` and the chatbot schema doc used dropped columns. | `tests/e2e/test_reference_queries.py` |
| R-28 | Frontend, 5 pages | Switching constituency showed the previous AC's cached data. Filters carried over. | `src/pages/__tests__/acSwitch.test.tsx` |
| R-29 | Overview insights | Wrong link, duplicate community rows, empty news panel, empty swings panel, and a 403 request for block users. | `src/components/OverviewInsights.test.tsx` |
| R-30 | i18n | `health.roll` rendered as raw text. The checker could not catch keys that are used but never defined. | Extended `check-i18n.mjs` |
| R-31 | Mobile | No way to sign out under 640 px. | `src/components/Layout.test.tsx` |
| R-32 | Fixture mode | Ignored the HTTP method and the query string, so filters did nothing, VS-2019 was shown as 2024, and Scenario and Admin writes failed. | `src/fixtures/responses.test.ts` |
| R-33 | Text matching | Anusvara (ं) was transliterated as "m" ("anamd"), so Devanagari names never matched their Latin spellings. These were the 2 baseline test failures. | commit `cec3b1b` |

### Quality (P2) — commits `fefa712`, `aec4d1a`

| ID | Area | Fix |
|---|---|---|
| R-40 | Chat stream | The CRLF event separator was never split, so live chat showed nothing. Added a `response.ok` check. `src/lib/__tests__/sse.test.ts` |
| R-41 | Styling | Status colour tokens, the `.select`/`.input` classes and the `text-3xs` size were used but never defined. `src/styles/__tests__/styles.test.ts` |
| R-42 | Map | Every filter change unmounted the map and lost the zoom. There was no empty state. |
| R-43 | Results / Booths | The dropdown was overridden by the URL. The Booths election list was hardcoded. |
| R-44 | Roles | Block users were offered Transfer and Scenario, which the API refuses with 403. |
| R-45 | CSV export | Failures were swallowed. A 401 was ignored. Fixture mode was not supported. The Voters export ignored the selected revision. |
| R-46 | Transfer | The legend colours did not match the cells. |
| R-47 | Source links | Pointed at `/raw/`, which nothing serves. Now uses `VITE_SOURCE_DOCS_URL` when set. |
| R-48 | Tooling | `npm run lint` called an uninstalled ESLint. Installed it with react-hooks rules. Playwright defaulted to Edge. |
| R-49 | Tests | One Playwright test was flaky. `test_metrics_sql` wiped the shared dataset. |

### From branch `rahul-working` (Rahul Gupta), merged 1 Oct 2026

Rahul's branch fixed several of the same defects independently: Decimal→float, the booth card, the
`/booths` joins, anusvara, the dev-stack `sys.path` fix, and the review-queue and caste writers
from the OneDrive port. Where both branches fixed the same thing, the merge kept one implementation
that passes both branches' tests (see the merge commit). His additional work:

| ID | Area | What | Test |
|---|---|---|---|
| R-50 | Map | AC and block boundary outlines (DataMeet / geoBoundaries), synthetic ward/panchayat cells for the fixture, `GET /acs/{n}/boundaries`, a layer toggle remembered per viewer. Migration `0022_boundaries.sql` (written as 0019; renumbered on merge). Known data problem pinned by a test: DataMeet's AC-32 outline misses Giridih town by 5.4 km. | `tests/test_boundaries.py` |
| R-51 | Fixtures | Areas filed under their real blocks (they were round-robin), and booths sampled inside their own area polygon (they were jittered across ~60 km). Votes and metrics unchanged. | `test_fixture_parity`, Playwright |
| R-52 | API | `/areas` offered every block to a block-scoped user. | `e2e/test_api_contract` |
| R-53 | Map | Filters reset per AC, an empty AC fits its outline, and previous markers are kept only within one AC. | Playwright |
| R-54 | Loaders | New `ingest/load_csv.py`: demography, candidate_profile, local_office_holder, scoped to one AC, `--dry-run`. RUN.md and the LLD had documented it without it existing. | `tests/test_ui_commands.py` |
| R-55 | Data health | Four of the "run this to fill the gap" commands shown in the UI failed (bad flags or a missing module). | `tests/test_ui_commands.py` checks that every command the UI shows names a real module and only flags it defines. |
| R-56 | Loaders | `fetch_sec --load-csv` now requires `--ac`. | `e2e/test_fetch_sec_load.py` |
| R-57 | Dev stack | New `caste` step (the Community tile showed "not loaded" over 180 estimates) and `geo` step (`scripts/dev_geo.py`: synthetic area outlines and booth points, dev/test databases only). | — |
| R-58 | Tests | The e2e `db_url` fixture returned instead of yielding when `E2E_DATABASE_URL` was set, so every e2e test errored. | — |

---

## Open

**Severity:** High = wrong numbers or a security or privacy exposure in normal use. Medium = a
feature broken or misleading. Low = polish.

| ID | Sev | Area | Issue | Why open / what is needed |
|---|---|---|---|---|
| O-01 | High | Auth | No rate limit or lockout on `/auth/login` and `/auth/otp`. Each new OTP allows 5 more guesses (audit E4). | Needs a design choice: in-process or Redis. See `REMAINING_WORK.md` §2. |
| O-02 | High | Data | No real Form 20, PS list or roll has been loaded. All booth-level figures are synthetic. | Needs the documents (`REMAINING_WORK.md` §3). |
| O-03 | High | Data | Five constituencies' seed figures are unverified (`verified=false`). | A person must check them against ECI results. |
| O-04 | Medium | `ingest/parse_form20.py` | An unreadable cell still loads as 0 (audit C4). It is caught only by the constituency-total gate. | Send unreadable cells to the review queue, never to 0. |
| O-05 | Medium | Loaders | No unique index on `(election_id, column_index)` for candidates (audit C6). This is closed in practice by the unresolved-column abort. | Add a migration. |
| O-06 | Medium | `ingest/validate.py` | The roll-continuity check runs LAG across all constituencies, giving false positives with several ACs (C12). | Partition by `ac_id`. |
| O-07 | Medium | API | Five routes return bare dicts. No pydantic response models. No generated TS types (FRONTEND_HARDENING §2). | `REMAINING_WORK.md` §4. |
| O-08 | Medium | Frontend | About 60 hardcoded English strings remain in News, Caste, Transfer, Voters, LocalPolls, Scenario, Admin, ChatPanel and the footer, so Hindi mode is incomplete. | Move them to `en.json`/`hi.json`. Needs Hindi review. |
| O-09 | Medium | Scenario page | The slider labels hardcode "BJP→JMM" and "JLKM to BJP". This is wrong for ACs with another contest pair (Dumri, Kanke, Silli). | Read `contest` from `/areas`. |
| O-10 | Medium | AC switcher | Lists every AC to a block user, who then gets 403 on the others. | Filter by `/acs[].accessible`. |
| O-11 | Medium | News | Labelling needs `ANTHROPIC_API_KEY`. The schedule is every 4 h and nightly, not the 30 min and hourly that `RUN.md` claims. | A key; decide the schedule. |
| O-12 | Medium | Semantic search | The API image has no `news.embed` or sentence-transformers, so news search always ranks by date. | Ship the model in the API image, or embed queries in the worker. |
| O-13 | Medium | Docs | `docs/operations/RUN.md` still describes flags and commands that do not exist (`fetch_ceo --ac/--doc/--year`, `parse_roll --mother`, `db.link_roll`, `ingest.load_csv`, `restore_drill.sh`, TLS profile, Telegram). | Rewrite against the CLIs (`--help`). It is flagged at the top of the file. |
| O-14 | Low | Theme | Charts keep the old palette after a theme toggle until the next render. | Re-render on theme change. |
| O-15 | Low | Lint | 7 `react-hooks/exhaustive-deps` warnings (memo churn) remain. | Memoise the inputs. |
| O-16 | Low | Two tabs | Two tabs on different ACs can swap AC on navigation, because nav links read localStorage. | Carry `?ac=` on nav links, or put the AC in the route (N13). |
| O-17 | Low | Deploy | TLS: port 443 is published but nginx has no TLS server block and there is no certbot (A2). There is no off-host backup (G1). | Deployment work. |
| O-18 | Low | `/compare` | The `metric` parameter is echoed back and never used. | Implement it or drop it. |
| O-19 | Low | Docker | `docker compose build` has not been run since the restructure (no daemon was available). `docker compose config` validates. | Run it once. |
