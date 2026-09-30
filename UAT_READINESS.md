# UAT_READINESS.md

**NOT READY — interim report, 30 Sep 2026.** Track A is in progress: A-0 (version
control, A7), the deployment-topology work, E1, the multi-AC spine and the whole of
master prompt §3 are done; the dashboard (B-4) and the rest of A-1/A-3/A-4 are not. Giridih has **not** been run on a real Form 20 — no Form 20
PDF is present in `raw/` and the parser defects C1/C2 that would block the load are still
open.

**No database and no Docker were available in the build environment**, so every check whose
proof requires a running stack is recorded below as `NOT VERIFIED HERE` with the command the
UAT operator must run. Nothing in this file is marked PASS unless a command or test that I
ran produced the evidence quoted. See `DECISIONS.md` D-002.

> This report is itself an output of the system and must be reviewed by a human for accuracy
> and completeness before it is relied on.

---

## 1. Deployment topology: API without a worker, ingestion from a laptop

Requested mid-session: *confirm and test that the API runs and serves every page from the
database with no worker service deployed; ingestion runnable from a laptop against a remote
`DATABASE_URL`, with `STORAGE_BACKEND=local` for roll PDFs and `s3` for Form 20 PDFs.*

### 1.1 Does the API run with no worker deployed?

**Yes, and it is now tested.** `tests/test_api_topology.py`, 33 cases, `pytest -q` green.

| Claim | Result | Evidence |
|---|---|---|
| No API module imports `worker/`, `ingest/` or `news/` at module scope | **PASS** | AST check per file in `api/`. The single `analytics.scenario` module-scope import is permitted and guarded by a test that `analytics/scenario.py` stays dependency-free, since the API image would otherwise need its dependencies. |
| The API imports with worker-only dependencies absent | **PASS** | A meta-path finder blocks `worker`, `ingest`, `news` and nine worker-only distributions (`sentence_transformers`, `apscheduler`, `feedparser`, `pytesseract`, `boto3`, `pandas`, `scipy`, `numpy`, `pdfplumber`), drops the modules from the cache and re-imports `api.main`. Blocking beats "not installed here", which stopped proving anything once `pdfplumber` was needed by another test. |
| Every endpoint the frontend calls exists in the route table | **PASS** | Scanned from the call sites in `web/src`; 37 routes registered. Two apparent misses were bugs in my scanner (nested template literals), not in the app. |
| The API spawns no process and touches no filesystem | **PASS** | No `subprocess`/`shutil`/`tempfile` import and no `open(` anywhere in `api/`. |
| Compose has no api→worker dependency | **PASS** (by inspection) | `docker-compose.yml`: `api.depends_on` is `db` only; `web.depends_on` is `api` only. Dropping the worker service leaves the graph valid. |
| Every page's data comes from the API | **PASS after two fixes** | See below. |
| **The API actually serves each page's data correctly** | **NOT VERIFIED HERE** | Needs a populated Postgres. Operator command in §4. |

**Two pages were not reading from the database at all, and are now.**

- `Factors.tsx` made no API call. It rendered five knowledge cards as a hardcoded array whose
  own comment claimed they were "the same cards that go into the assistant's cached prompt, so
  what the dashboard shows and what the assistant knows cannot drift apart". They had already
  drifted — the assistant reads the `knowledge_card` table, the page read a constant, five
  cards against six seeded, with a mismatched slug — and the array carried LS/VS vote counts
  and the 2024 margin as literals that nothing reconciled against loaded results. Now reads
  `GET /knowledge-cards`.
- `Overview.tsx` assigned its margin chart the `PUBLISHED_MARGINS` constant unconditionally.
  The variable deciding whether real data exists was computed correctly and then ignored, so
  the bars never moved after a Form 20 load and only the caption changed — the constant's own
  comment said it was "no longer used" at that point. `/summary` now returns winner, runner-up
  and margin per election from `mv_result_booth_wide`; the published figures remain only as an
  explicitly captioned fallback when nothing is loaded. The "grew about 15%" and "tightest at
  3,838 votes" captions are now derived.

**Three behaviours that degrade rather than fail, recorded rather than fixed:**

1. `POST /scenario` returns **HTTP 409**, not an empty result, when no baseline is loaded.
   Correct but abrupt; the page shows an error, not an empty state. Left for A-3.
2. `GET /news?q=` cannot embed the query in the API image (`sentence-transformers` is
   worker-only), so it silently falls back to date ordering. The fallback is logged, not
   signalled in the response.
3. **The materialized views only hold rows once `analytics.refresh` has run.** With no worker
   there is no schedule, so this becomes an operator step after every load. This is the single
   most important operational consequence of dropping the worker — `/summary`, `/booths`,
   `/results/*`, `/transfer`, `/priority` and the booth card all read MVs and will return
   structurally valid but **empty** payloads until it is run. `RUN.md` §5 must say so.

**Also fixed while here.** `GET /health` returned HTTP 200 with a `"degraded"` string when the
database was unreachable, so the compose healthcheck — which only inspects the status code —
reported the service healthy while every data route was timing out. It now returns **503**, and
carries the exception class rather than its message, since `/health` is unauthenticated and a
psycopg error can name the host, database and user.

### 1.2 Is ingestion runnable from a laptop against a remote `DATABASE_URL`?

**Built and unit-tested; not executed against a remote database.**

| Claim | Result | Evidence |
|---|---|---|
| Parsers accept a document by registered digest, not only a local path | **PASS** | `ingest/documents.py`; `--doc SHA256` looks the row up in `source_doc` and reads whichever backend it records. `tests/test_documents.py`, 20 cases. |
| A positional local path still works | **PASS** | Every `RUN.md` command is unchanged; `--help` verified on all three parsers. |
| A substituted or overwritten object is refused | **PASS** | `--doc` verifies fetched bytes against the recorded digest. Without it, a load would put unreconciled numbers into the serving tables under a `source_doc` reference that looks audited. |
| Remote documents leave nothing on the laptop | **PASS** | Downloaded to a 0700 temp directory, removed on exit including on exception. |
| One command tells an operator what is missing | **PASS** | `scripts/preflight.py`: `DATABASE_URL` (redacted back), TLS warning when the host is remote and `sslmode` absent, server version, `postgis`/`vector`, pending migrations, row counts, storage routing, S3 reachability, `RAW_DIR` writable, PDF toolchain. Exit 1 on any blocking failure. Writes nothing. |
| **Ingestion completes against a remote database** | **NOT VERIFIED HERE** | No database. Operator command in §4. |
| `--ac` on every loader, as `RUN.md` §4 promises | **FAIL — documented but not implemented** | No ingestion module accepts `--ac` today. It arrives with the multi-AC spine in A-2. `RUN.md` currently describes commands that do not exist. |

### 1.3 `STORAGE_BACKEND=local` for rolls, `s3` for Form 20

**Built and unit-tested against a stub; no real bucket exercised.**

| Claim | Result | Evidence |
|---|---|---|
| Per-kind routing, `form20`→s3 and rolls→local | **PASS** | `common/storage.py`; `STORAGE_BACKEND` sets the default, `STORAGE_BACKEND_<KIND>` overrides. `.env.example` ships exactly this configuration. |
| Roll PDFs **cannot** go to a remote backend | **PASS** | Enforced three ways, not configured: `storage.assert_local_only` refuses regardless of env; a `roll_docs_stay_local` CHECK constraint in `0013` refuses the row even if a future loader forgets; `scripts/preflight.py` reports it. `tests/test_storage.py` asserts refusal under `STORAGE_BACKEND=s3` **and** under an explicit per-kind override, at both the resolver and `for_kind`, plus that `ROLL_KINDS` still matches the schema's CHECK so a new roll-bearing kind cannot quietly escape the rule. |
| Storage keys cannot traverse or escape | **PASS** | Filenames come from portal URLs, taken verbatim by `fetch_ceo`. `_validate_key` refuses `..`, absolute paths and drive letters; `LocalStorage` re-checks containment. |
| Local files are written with restrictive permissions | **PASS** (on Linux) | 0700 directories, 0600 files, which is what LLD §12 describes and the audit found was not done. `os.chmod` is a near no-op on Windows; the compliance target is the Linux host. |
| Writes are atomic | **PASS** | `.partial` sibling then `os.replace`, so an interrupted write cannot leave a truncated PDF that looks complete. |
| S3-compatible, endpoint configurable | **PASS** (stub) | boto3 with `endpoint_url`, so AWS S3, Backblaze B2, MinIO and Wasabi all work. |
| **A real S3 round-trip** | **NOT VERIFIED HERE** | No bucket or credentials. The stub covers key construction, prefixing, digest metadata and error wrapping; it cannot prove credentials, IAM policy, TLS or bucket region. Operator command in §4. |
| `C13`: the auditable original is kept | **FIXED** | `RETAIN_RAW_ROLLS` now defaults **true**. The old default deleted the source roll PDF while `extract_pdf`'s page cache kept the full elector text on disk — the system destroyed the evidence and retained the personal data. |

---

## 1A. §3.2 metric tests — status

Requested explicitly: write a test for every metric in §3.2 against hand-computed
fixtures, run them if a Postgres is available, and **mark them NOT RUN rather than
skipping them** if not.

There are two suites, because there are two implementations that must agree.

| Suite | What it checks | Status |
|---|---|---|
| `tests/test_metrics.py` (60 tests) | `analytics/metrics.py` against hand-computed fixtures: every row of §3.2, every NULL rule in it, and the specific audit finding each rule prevents | **RUN — 60 passed** |
| `tests/e2e/test_metrics_sql.py` (14 tests) | the same fixtures through `db/migrations/0015_metrics.sql`, so the Python and the SQL cannot drift | **NOT RUN — no PostgreSQL in the build environment** |

**NOT RUN, not skipped.** pytest reports the second suite as skipped, which is a
weaker statement than the truth: the SQL implementation of every metric in this
system is, at the time of writing, unexecuted. The Python is tested; the SQL that
the API actually reads is not. Treat any figure the dashboard shows as unverified
until an operator runs:

```bash
export E2E_DATABASE_URL='postgresql://user:pass@host:5432/giridih_test'
pytest tests/e2e -v        # expect 14 passed
```

That suite drops and rebuilds the schema, and refuses a URL whose database name does
not contain `test`.

### What the 60 Python tests pin, by §3.2 row

| Metric | Hand-computed check | NULL rules covered |
|---|---|---|
| `valid_votes` | Giridih 2024 = 207,598 including NOTA | no result; all-None votes; a real zero is counted |
| `votes_polled` | 1,000 + 12 = 1,012 | no result; missing `rejected` is zero, missing `valid` is NULL |
| `turnout_pct` | 1,012 / 1,500 = 67.47; Giridih 2024 = 68.09 | electors unknown or zero (B4) |
| `share_pct` | JMM 45.3, BJP 43.45, JLKM 5.2 vs published | no result |
| `margin_votes` | 3,838 for Giridih 2024 | fewer than two candidates (not zero) |
| `margin_pct` | **1.85%**, and 1.87% with the wrong denominator | as above (D1) |
| `signed_margin_pct` | +10.0 / −30.0 on the fixture booths | third party wins → NULL, not 0 (F1) |
| `swing_pct` | −2.9 and +4.75 | no prior election (D2); weak unreviewed crosswalk; unaggregated split |
| `alliance_swing_pct` | NDA +7.75 using each event's own alliance map | either side has no members; weak crosswalk |
| `new_voter_pct` | 40,084 / 304,898 = 13.15 | either roll missing (B1) |
| `net_roll_change_pct` | +9.0, and −7.0 for a post-SIR case | either roll missing |
| `transfer_delta` | +16.0 and −22.3 | either leg missing |
| `floating_pct` | 59.45 on the LS/VS fixture | **only one poll type → NULL, never 50.00 (D4)** |
| `volatility` | 8.05 (sample stdev, verified by hand) | fewer than two years |
| `priority_score` | 0.695 with all inputs; 0.8583 renormalised over two | all inputs missing; `inputs_used` recorded |

Three of those tests exist because a previous test asserted the *bug*: the caste
census blend asserted 19.81 where the documented formula gives 21.0 (D8), and the
scenario tests all relied on the hardcoded JMM/BJP pair that §3.5 removes. Both are
corrected with the reason recorded in the test.

---

## 2. Audit IDs closed so far

Full ledger in `PROGRESS.md` §5. Closed in this session:

| ID | What it was | Commit |
|---|---|---|
| **A6** | No version control anywhere under `election_monitor/` | `1b0efed` |
| **A7** | `jellyfish` absent from everything `requirements-dev.txt` pulled in, so all 96 tests — including the seven pinning the 0.85/0.65 crosswalk thresholds — ran against a fallback that **disagreed with production by up to 0.12**, deeper than the review band. Fallback now reproduces jellyfish; parity asserted to 3 dp. | `760a244` |
| **A5** | No chatbot feature flag; mounting the router made `sqlglot` a hard import-time requirement of the whole API | `f7e5f73` |
| **E3** | Chat SSE error path streamed `str(exc)[:200]`, so a psycopg failure surfaced table and column names to the browser | `f7e5f73` |
| **C13** | `discard_raw` deleted the only auditable original by default while the extracted text stayed on disk | `33a51d3` |
| **B10** (partly) | `parse_status` never advanced past `extracted`; `parsed_at` never set | `252d6bd` |
| **E1** | An empty `JWT_SECRET` was a working unauthenticated admin login, not a broken one — HS256 verification with an empty key succeeds | `eeccb19` |
| **B3, B6, B9, B11** | Closed structurally by the multi-AC spine: per-AC booth sequence, FKs on the crosswalk, one baseline per AC, roll-revision uniqueness | `5def601` |
| **C1, D3** | Form 20 columns never resolved to a party, so every booth reported a 100% margin with a NULL winner | `ea466bf` |
| **D1** | Two inconsistent totals in one row; the AC margin came out 1.87% against the published 1.85% | `ea466bf` |
| **D2, D4, D9** | Fabricated swing for the earliest election; a uniform 50.00% floating vote; a vanished party's collapse never shown | `ea466bf` |
| **C11** | Crosswalk coverage measured against `booth_crosswalk` — circular, and reported ~100% healthy while 17% of the seat was dropped | `ea466bf` |
| **D5, D6** | Scenario named a contest-pair party as winner while its own votes dict showed another; the band came from one row and cancelled itself on the rest | `448b456` |
| **D8, C14** | The caste rescale partly undid itself; unmatched surnames were extrapolated rather than left explicit | `448b456` |
| **B2, C10, C16** | Review-band matches written; the 0.20 roll-part term actually contributes; the review queue no longer doubles per run | `8ffb603` |
| **D7, F2** | `/rolls/changes` joined a revision that can never be both mother and supplement, so electors were always NULL; `/booths` ignored `election_label` | `5def601` |

Two audit claims the auditor could not execute, now verified empirically:

- **E1 is real.** `jwt.encode({'sub':'1','role':'admin'}, '', algorithm='HS256')` mints and
  `jwt.decode(token, '', algorithms=['HS256'])` returns the payload. The empty-secret admin
  bypass is not theoretical. **Still open** — the fix is A-1.1.
- **A7 is real**, and worse than described: the divergence is 0.12 on the abbreviation case the
  crosswalk exists to handle (`'pra vi chataro'` vs `'prathamik vidyalaya chataro'` — 0.71642
  fallback, 0.59489 jellyfish). jellyfish applies Winkler's gate and boosts only above a Jaro
  of 0.7; the fallback boosted unconditionally.

---

## 3. Gates run before each commit

`docker compose build` cannot run in this environment (`DECISIONS.md` D-002). Substituted:

| Gate | Latest result |
|---|---|
| `pytest -q` | **499 passed, 15 skipped** (was 96 at baseline) |
| `ruff check .` | **clean** |
| `python scripts/lint_sql.py` | **16 migrations, no problems**. Verified against deliberately broken input: it catches transaction control inside a migration and a reference to a relation no earlier migration creates. |
| `npm run build` | **clean** |
| `docker compose build` | **NOT RUN — Docker not installed, no rights to install it** |

Of the 15 skipped: 14 are the e2e SQL suite above (NOT RUN, no database) and one is
`Login`, exempted from the "every page fetches from the API" check because it posts
credentials and renders nothing from the database.

---

## 4. Commands the UAT operator must run to close the gaps above

Everything in §1 marked `NOT VERIFIED HERE` reduces to these. Run on a machine with Docker,
or against a throwaway PostgreSQL 16 + PostGIS + pgvector.

```bash
# 0. Preflight - fails fast with a reason for each problem
export DATABASE_URL='postgresql://user:pass@host:5432/giridih?sslmode=require'
python scripts/preflight.py

# 1. Schema and reference data
python -m db.apply_migrations --seed

# 2. The API serves every page from the database, with no worker running
uvicorn api.main:app --port 8000          # worker deliberately not started
curl -si localhost:8000/health   | head -1   # expect: HTTP/1.1 200
curl -s  localhost:8000/config               # expect: chat_enabled=false
# stop the database, then:
curl -si localhost:8000/health   | head -1   # expect: HTTP/1.1 503  (was 200 before this session)
# with a token, every route the frontend calls must answer 200 and not 500:
for p in /summary /knowledge-cards /areas /booths /priority /rolls/revisions \
         /rolls/changes /caste /transfer /local-results /news /news/issues; do
  printf '%s ' "$p"; curl -s -o /dev/null -w '%{http_code}\n' \
    -H "Authorization: Bearer $TOKEN" "localhost:8000$p"
done

# 3. Remember: with no worker, nothing refreshes the views on a schedule
python -m analytics.refresh               # after every load, or the pages are empty

# 4. Ingestion from a laptop, Form 20 out of object storage
export STORAGE_BACKEND=local STORAGE_BACKEND_FORM20=s3
export S3_ENDPOINT=... S3_BUCKET=... S3_ACCESS_KEY=... S3_SECRET_KEY=... S3_PREFIX=raw
python scripts/preflight.py                            # confirms the bucket is reachable
python -m ingest.fetch_ceo --discover <url> --kind form20 --download
python -m ingest.parse_form20 --doc <sha256-prefix> --election VS-2024 --dry-run

# 5. The roll invariant, proved rather than trusted
STORAGE_BACKEND=s3 python -m ingest.parse_roll <pdf> --revision 2026-SSR \
    --date 2026-01-01 --load
#   expect: RollStorageViolation, refusing to store a roll_mother document on 's3'
```

---

## 5. Still open, in the order Track A tackles it

| # | Item | Why it matters |
|---|---|---|
| A-1 | **E1** empty/placeholder `JWT_SECRET` accepted at startup | Anyone can mint an admin token offline. Verified exploitable. |
| A-1 | **C3** roll page text written to `ocr/` in plaintext | The aggregate-only guarantee is false on disk. `raw/` and `ocr/` are empty on this machine, so nothing has leaked here yet. |
| A-1 | **A1** compose pins an image without PostGIS | A clean checkout cannot start. |
| A-1 | E4 login rate limit, A3 worker volume ownership, G2 `pg_dump` password in argv | |
| A-2 | Multi-AC spine, `booth_uid` per AC, six ACs seeded `verified=false`, `/acs/{ac}/…`, AC switcher | Also delivers the `--ac` flag `RUN.md` already documents and the `acs` list `/config` is meant to return. |
| A-3 | **C1/C2** Form 20 columns never resolve to a party; **D1** NOTA denominator; **B2/B3** crosswalk write path; **B4/B1** electors and new voters | Until these land, every booth-level margin is wrong and the AC margin does not reproduce 1.85%. |
| A-4 | `uat_seed.sh`, `uat_users.py`, `UAT_SCRIPT.md` | |

## 6. Known risks for a UAT run today

| Risk | Workaround |
|---|---|
| No Form 20 has ever been loaded, so every results page is empty | Demonstrate the empty states, which name the CLI command to fix each gap. Do not present any booth-level number. |
| `POST /scenario` returns 409 rather than an empty state | Skip the scenario page, or load a baseline first. |
| `/news` is empty without an Anthropic key (items must be labelled to appear) | Expected; say so rather than debugging it live. |
| The views are empty until `analytics.refresh` runs, and with no worker nothing runs it | Put `python -m analytics.refresh` in the run sheet after every load. |
| Seeded constituency facts for the five new ACs are not yet present at all | Nothing to misread yet. When A-2 lands they carry `verified=false` and an "unverified" badge. |
