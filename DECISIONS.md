# DECISIONS.md

Every judgement call made during the v2 build, with the option chosen and why. Required by
`CLAUDE_CODE_MASTER_PROMPT.md` §0 for any conflict the governing documents do not resolve.

Newest last. Each entry: what the conflict was, what was chosen, why, and what would change the
answer.

---

## D-001 · Repo root is ambiguous; `git init` at `election_monitor/`

**Conflict.** `claude_code_fix_and_expand_prompt.md` line 3 requires `AUDIT_REPORT.md` and
`MULTI_AC_EXPANSION_SPEC.md` to both sit at "the repo root". They do not. The spec, HLD, LLD,
`RUN.md` and the master prompt are at `election_monitor/`; the audit, `README.md`, `docs/`,
`docker-compose.yml`, `.gitignore`, `.env` and all code are at `election_monitor/giridih-monitor/`.
No governing document resolves which directory is the root.

**Chosen.** `git init` at `election_monitor/` — the outermost directory, so the design documents
are versioned alongside the code they govern. `giridih-monitor/` remains the *application* root:
compose, `.env`, requirements and all code stay there, and every command in `RUN.md` continues to
run from there. Session and report documents (`PROGRESS.md`, `DECISIONS.md`, `UAT_SCRIPT.md`,
`UAT_READINESS.md`) sit at `election_monitor/` beside the master prompt and `RUN.md`. No existing
document is moved.

**Why.** Moving `AUDIT_REPORT.md` up or the spec down would satisfy the letter of the precondition
while breaking every path reference in both documents and in the audit's 600 file:line citations.
Versioning the design documents matters more than co-locating two of them.

**Would change the answer.** If the operator wants a single flat repo, move `giridih-monitor/*` up
one level in a dedicated commit and rewrite `RUN.md`'s working directory. Not worth doing before
UAT.

---

## D-002 · `docker compose build` cannot gate commits; substituted gate

**Conflict.** Master prompt §1 requires "tests and `docker compose build` passing before each
commit". Verified in this environment: Docker is not installed, the daemon is unreachable, and the
session has no administrator rights to install it. `docker compose build` cannot be run at all.

**Chosen.** Pre-commit gate becomes: `pytest -q` green **and** `npm run build` green **and**
migration SQL parsed by `scripts/lint_sql.py` (statement-splits, checks every file is
`BEGIN`/`COMMIT`-balanced and references no unknown relation) **and** no secret staged. Each commit
message names the gates that ran. Dockerfiles and `docker-compose.yml` are still written and
reviewed; they are simply unbuilt.

**Why.** The alternative is to never commit, which loses the diffability that `git init` was for.
A named, weaker gate that actually ran is worth more than a stronger one that did not.

**Consequence, recorded for `UAT_READINESS.md`.** Every acceptance check whose proof requires a
running stack — Gate A-1's boot check, Gate A-2, Gate A-3's e2e suite, Gate A-4's `uat_seed.sh`
run — is reported as `NOT VERIFIED HERE`, with the command the UAT operator must run, never as
PASS. Approved by the operator at plan sign-off.

---

## D-003 · `0013` is the storage migration; multi-AC becomes `0014`

**Conflict.** `claude_code_fix_and_expand_prompt.md` names the multi-AC migration
`0013_multi_ac.sql` and the extended-data one `0014_extended_data.sql`. The operator then
asked for per-kind document storage, which needs two columns on `source_doc` before any
ingestion can use it - and migrations are forward-only and applied in filename order, so
numbering has to follow build order.

**Chosen.** `0013_source_doc_storage.sql` ships first. Multi-AC becomes `0014_multi_ac.sql`
and extended data `0015_extended_data.sql`. Nothing is deployed yet, so no applied migration
is renumbered and no checksum drifts.

**Why.** The alternatives are a gap at `0013` reserved for work not yet written, which
`scripts/lint_sql.py` reports as a hole, or shipping the storage columns after multi-AC and
leaving the storage layer unreachable in between.

---

## D-004 · Roll documents are barred from remote storage, not merely defaulted to local

**Conflict.** The operator asked for `STORAGE_BACKEND=local` for roll PDFs and `s3` for Form
20. Read literally that is a configuration request, satisfiable with two environment
variables.

**Chosen.** Implemented as an invariant instead, with the operator's explicit agreement.
`STORAGE_BACKEND` and per-kind overrides work as asked, but `roll_mother` and
`roll_supplement` are refused any non-local backend regardless of configuration, in three
places: `storage.assert_local_only`, a `roll_docs_stay_local` CHECK constraint in `0013`, and
a reported line in `scripts/preflight.py`.

**Why.** A roll PDF holds every elector's name, EPIC number, relative's name, house number
and age. Under a pure-configuration design one mistyped variable - `STORAGE_BACKEND=s3`
without the two roll overrides - uploads the entire electoral roll to third-party object
storage, its access logs, its versioning history and its backups. That is the exact failure
the aggregate-only design exists to prevent, and it would be silent. The CHECK constraint is
there because the Python guard only protects the write paths that exist today; a future
loader, admin endpoint or hand-written UPDATE would bypass it.

**Would change the answer.** Nothing short of a legal basis for holding rolls off-host. If
that arrives, the change is deliberate and touches three named places, which is the point.

---

## D-005 · Repo re-rooted at `C:\dev\giridih-monitor`; supersedes D-001

**Why now.** The operator moved the tree to `C:\dev\giridih-monitor` and asked that work
continue there. The move brought only the *application* subtree: no `.git`, and none of the
governing documents — no `PROGRESS.md`, `UAT_READINESS.md`, `DECISIONS.md`,
`CLAUDE_CODE_MASTER_PROMPT.md`, `RUN.md`, HLD, LLD or spec. Working there as delivered would
have meant losing three commits and the 70-finding audit ledger, and continuing without a
commit gate.

**Chosen.** `C:\dev\giridih-monitor` is now both the repo root and the application root; the
two-level split D-001 established is gone. `.git`, `.gitattributes` and the nine governing
documents were copied in from the old root, and the app subtree's move was committed as 208
pure renames, so the full history is intact and reachable from the new root.

**What changed concretely.** Tracked paths lose their `giridih-monitor/` prefix:
`giridih-monitor/analytics/metrics.py` becomes `analytics/metrics.py`. Every command in
`RUN.md` already ran from the application root, so no command changes — but commands that
referenced the parent for session documents now find them in the same directory.

**One file was deleted rather than moved.** There were two `.gitignore` files: one at the old
repo root, one in the app subtree. With a single root there can only be one, and the root
version is kept because it is the deliberate superset — its own header says it "repeats the
dangerous patterns so a mistake in one place cannot commit a secret or a voter record". It
covers everything the app-level file did, with `**/` prefixes, plus `.ruff_cache`, coverage
output, Playwright artefacts and editor directories. Verified after the move that `.env` is
still ignored and that `raw/.gitkeep`, `ocr/.gitkeep` and `backups/.gitkeep` are still
tracked, since those sentinels are what keep the data directories in the tree while their
contents stay out of it.

**The old path was left in place, untouched and still a valid repo.** It is a backup until the
operator deletes it. It is also a hazard: two clones of this project now exist and only one is
being worked on. Nothing has been committed there since `e1b9263`.

---

## D-006 · The schema stops requiring PostGIS (N4); conflicts with LLD §6.4

**Why.** `FRONTEND_HARDENING.md` §1 needs a local full stack with no Docker, and the
operator directed that N4 be closed first because §1 depends on it. The blocker was
migration `0001`, which required `postgis`, `pg_trgm` and `unaccent`. A
pip-installable PostgreSQL provides none of them, so no SQL test in this project had
ever been executed — the schema had never once been applied anywhere.

**What was actually being used.** Investigated before changing anything:

| Extension | Real usage found |
|---|---|
| `vector` | The news embedding columns. Genuinely required. |
| `pg_trgm` | **None.** No trigram index, no `similarity()`, anywhere. |
| `unaccent` | **None.** No `unaccent()` call, anywhere. |
| `postgis` | Real but narrow: `area.geom`, `booth.geom`, and `ST_X`/`ST_Y` in `GET /booths` plus `ST_MakePoint`/`ST_SetSRID`/`ST_Centroid` in `ingest/geocode.py`. |

`booth.geom` was written from a longitude/latitude pair and read back with
`ST_X`/`ST_Y` — a round trip through PostGIS to recover the two numbers that went in.
`area.geom` was read only by `ST_Centroid`, and **nothing ever wrote it**, so the
area-centroid geocoding fallback could never fire.

**Chosen.** `0002` now stores `booth.lon` / `booth.lat` as `DOUBLE PRECISION` with
range CHECKs and a both-or-neither constraint, and `area.boundary` as `JSONB`
(a GeoJSON geometry) beside explicit `area.centroid_lon` / `centroid_lat`. `0001`
creates only `vector`. The GiST indexes become one partial composite index on
located booths.

The range CHECKs are a small gain the geometry type did not give: a transposed
lat/lon pair is now rejected rather than silently stored as a point in the wrong
hemisphere.

**What this costs.** No spatial *query* is possible — no point-in-polygon, no
distance ordering, no tile cutting. None is performed today. The day one is needed
this decision must be revisited; `docker/Dockerfile.db` therefore still installs
PostGIS, so that day needs no image change, and its build-time check reports PostGIS
as "available (not required)" rather than requiring it.

**Conflict with a governing document, recorded rather than glossed.**
`Giridih_AC32_Election_Monitor_LLD.md` §6.4 lines 89 and 92 specify
`geom geometry(MultiPolygon,4326)` and `geom geometry(Point,4326)` explicitly. This
change contradicts the LLD. The LLD has not been edited — it is a design document the
operator owns, and silently rewriting it to match the code would destroy the record
that a decision was made here. If the LLD is to stand, revert this and accept that
every SQL test needs Docker.

**What it bought, measured.** Before: 17 migrations never applied, 94 tests skipped
for want of a database, and the SQL half of every metric unexecuted. After: all 17
migrations apply on a stock PostgreSQL 16 with pgvector — 50 tables, 14 materialized
views, 15 metric functions — and the suite went from 759 passed / 94 skipped to
**849 passed / 4 skipped**, with the 4 remaining skips being data gaps (no booth
results in the seed) rather than environment gaps.

**Two real defects surfaced immediately, which is the point.** `0015_metrics.sql`
selected `l.ac_id` from `election_roll_link`, a table with no such column, so the
file could not be applied at all — the first `apply_migrations` run against any
database would have stopped there. And `tests/e2e/test_metrics_sql.py`'s dataset
fixture was function-scoped over a session-scoped connection, so 13 of its 14 tests
died on a duplicate key the first time they ran. Both had passed review repeatedly
while nothing executed them.
