# Testing

How the tests are laid out, how to run them, and which tests cover each feature. Commands
assume the setup in `GETTING_STARTED.md`.

## Layers

| Layer | Location | Runner | Needs |
|---|---|---|---|
| Backend unit | `backend/tests/*.py` | pytest | nothing (DB access is faked) |
| Backend DB / API | `backend/tests/e2e/*.py`, `tests/test_metric_functions_sql.py` | pytest | builds an embedded Postgres (pgserver) per session, or `E2E_DATABASE_URL` (name must contain `test`) |
| Frontend unit | `frontend/src/**/*.test.ts(x)` | Vitest + Testing Library (jsdom) | nothing |
| Frontend static | `backend/tests/test_frontend_hardening.py`, `test_api_topology.py`, `test_fixture_parity.py`, `test_i18n_keys.py`; `frontend/scripts/check-i18n.mjs` | pytest / node | frontend `node_modules` for the i18n check |
| Browser (fixtures) | `frontend/e2e/*.spec.ts` | Playwright, project `fixtures` | Chromium (or `PLAYWRIGHT_CHANNEL`) |
| Browser (live) | `frontend/e2e/live/` | Playwright, project `live` | `make dev-stack` running. **Not written yet**; see `status/REMAINING_WORK.md` §1.1 |

## Commands

```bash
make test-backend      # cd backend && pytest -q --ignore=tests/e2e
make test-db           # cd backend && pytest -q tests/e2e tests/test_metric_functions_sql.py
make test-frontend     # npm test && npm run build && npm run check:i18n   (in frontend/)
make test-e2e          # npx playwright test --project=fixtures
make lint              # ruff + eslint
make test              # all except lint and live
```

One test: `cd backend && ../.venv/bin/pytest tests/e2e/test_booth_card.py -q` ·
`cd frontend && npx vitest run src/App.test.tsx` · `npx playwright test -g "priority"`.

## How the DB tests work

`tests/e2e/conftest.py` starts pgserver, applies every migration plus the seed, then
(`loaded_dataset`, once per session) generates mock PDFs and loads them **through the real
loaders** into AC 32. The fixtures `ids`, `users` (one per role, real password hashing),
`client` (FastAPI TestClient) and `tokens` are shared by every API test. AC 31 is reserved
for `test_metrics_sql.py`'s hand-built fixture and AC 42 is kept empty for "no data" checks
— do not load into them.

## How the frontend unit tests work

`src/test/server.ts` stubs `fetch` and answers every GET from `src/fixtures/responses.ts`
(the same data fixture mode serves), so the real `lib/api.ts` request path (token, 401,
error formatting) runs. Override a route with `mockServer({ '/acs/32/news': { status: 500 } })`.
`src/test/render.tsx` provides `renderWithProviders` (QueryClient + router) and `fakeAc(n)`.

## Rules

- A bug fix comes with a test that fails without it. Most commits on this branch were
  checked by reverting the fix and watching the test fail.
- Schema changes go in a new migration (`apply_migrations` never re-runs an edited file).
  Generated metric functions come from `analytics/metric_sql.py`; the parity tests fail if
  `0015_metrics.sql` drifts from it.
- `scripts/endpoint_inventory.py --check` and `scripts/generate_fixtures.py --check` must
  pass; regenerate `docs/design/ENDPOINTS.md` after changing a route or a call site.

## Feature → tests

| Feature / section | Backend | Frontend |
|---|---|---|
| Login, roles, tokens, JWT secret | `test_password`, `test_jwt_secret`, `e2e/test_api_contract` (every route × role), `e2e/test_api_misc` | `App.test`, `lib/__tests__/api.test`, `Layout.test` |
| Constituency switching / multi-AC | `e2e/test_api_contract` (block scoping, empty AC), `test_seed_data` | `lib/__tests__/ac.test`, `pages/__tests__/acSwitch.test` |
| Overview / summary | `e2e/test_api_routes_run`, `e2e/test_api_json_types` | `OverviewInsights.test`, Playwright `overview.spec` |
| Booth table, results, CSV | `e2e/test_booth_queries`, `e2e/test_api_contract` | `pages/__tests__/Results.test`, `lib/__tests__/api.test` (CSV) |
| Booth card / drawer | `e2e/test_booth_card` | `BoothDrawer.test`, Playwright `drawer-and-map.spec` |
| Map | `e2e/test_booth_queries` | Playwright `drawer-and-map.spec`; `responses.test` (filters) |
| Metrics (margin, turnout, swing, priority…) | `test_metrics`, `test_metric_parity`, `test_metric_functions_sql`, `e2e/test_metrics_sql`, `e2e/test_metric_parity_sql`, `e2e/test_views_0020` | — |
| Scenario | `test_scenario`, `e2e/test_scenario_api` | `responses.test` (fixture POST) |
| Caste / community estimates | `test_caste_estimate`, `e2e/test_api_contract` (block denied) | `OverviewInsights.test` (gating) |
| Voters / rolls | `test_roll_privacy`, `test_privacy_disk`, `e2e/test_ingest_pipeline`, `e2e/test_views_0020` | `acSwitch.test` |
| News | `test_dedupe`, `test_news_tagging`, `e2e/test_news_api` | `pages/__tests__/News.test` |
| Knowledge cards (Factors) | `e2e/test_knowledge_cards` | — |
| Local results | `e2e/test_fetch_sec_load` | `acSwitch.test` |
| Admin: review queue | `e2e/test_review_queue`, `e2e/test_api_contract` | `responses.test` |
| Ingestion: Form 20, PS list, rolls, crosswalk | `test_parse_form20`, `test_resolve`, `test_crosswalk`, `test_ls_segment`, `test_documents`, `test_storage`, `test_extract_pdf_register`, `test_mock_documents`, `test_pdf_writer`, `e2e/test_ingest_pipeline` | — |
| Privacy (no voter data) | `test_ocr_privacy`, `test_roll_privacy`, `test_privacy_disk`, `test_storage` | — |
| Text matching | `test_textnorm`, `test_similarity`, `test_resolve` | — |
| Chatbot (parked) | `test_router`, `test_sql_guard`, `test_chat_tools_roles`, `e2e/test_views_0020` (grants), `e2e/test_reference_queries` | `lib/__tests__/sse.test` |
| Worker, ops, Docker images | `test_worker`, `test_ops_backup`, `test_docker_images` | — |
| Legacy URLs | `test_legacy_redirects`, `e2e/test_api_contract` | — |
| i18n, styling, tiles, a11y guards | `test_i18n_keys`, `test_frontend_hardening`, `test_api_topology` | `styles/__tests__/styles.test`, `Provenance.test`; `npm run check:i18n` |
| Dev stack | `test_dev_stack` | — |

**Not covered yet** (see `status/REMAINING_WORK.md` §1): live browser tests; page tests for
Map, Caste, CasteScatter, Transfer, LocalPolls, Factors, Scenario, Compare, Candidates,
LocalPolitics, Admin; chatbot agent/budget/llm; news labelling and embedding; `fetch_ceo`,
`geocode`; `preflight`.
