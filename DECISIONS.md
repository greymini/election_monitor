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
