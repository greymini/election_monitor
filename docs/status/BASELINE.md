# Baseline: test and build state before the restructure

Recorded 2026-10-01 on branch `restructure-and-tests`, at `dev` commit `3087f89`, before any change.

**Environment:** macOS (arm64), Python 3.11.11 in `.venv`, Node 22.14, Playwright Chromium headless shell. The database is pgserver 0.1.4 (an embedded PostgreSQL 16 with pgvector).

Every later phase of the restructure must match or improve these numbers. Any test that changes is named in the commit that changes it.

| Check | Command | Result |
|---|---|---|
| Python, all tests | `pytest -q` | **911 passed, 2 failed, 4 skipped** |
| Python, without a database | `pytest -q --ignore=tests/e2e` | 807 passed, 2 failed, 1 skipped |
| Python, needing a database (pgserver) | `pytest -q tests/e2e tests/test_metric_functions_sql.py` | 186 passed, 3 skipped |
| Lint | `ruff check .` | All checks passed |
| Frontend build | `cd web && npm run build` | OK |
| i18n | `npm run check:i18n` | 301 keys resolve in both languages (10 plural families) |
| Playwright, fixture mode | `PLAYWRIGHT_CHANNEL= npx playwright test` | 41 passed, 1 flaky |

## Failures that were already present

1. **`tests/test_textnorm.py::test_transliteration_keeps_inherent_vowel`** and **`tests/test_resolve.py::test_comparable_puts_the_two_scripts_on_an_equal_footing`**.
   - `common.textnorm.to_latin` transliterates the anusvara (ं) as `m` before a dental or alveolar consonant. For example, "आनंद" comes out as "anamd" and "अंसारी" as "amsari".
   - Devanagari and Latin spellings of the same candidate name then compare unequal.
   - This bug is real and is tracked in `KNOWN_ISSUES.md`.
2. **`web/e2e/overview.spec.ts:155`, "the priority list is ranked and no row is blank"**. It fails intermittently, about 1 run in 3.
   - The test counts `li` elements before the panel's query has resolved.
   - The bug is in the test (it needs to wait for the list to appear), not in the app.

## Not run at baseline

- **Docker.** No daemon was reachable, so `docker compose build` was not run.
- **The live API.** Playwright has never run against a live API, because `scripts/dev_stack.py` arrives only with the patch port.
