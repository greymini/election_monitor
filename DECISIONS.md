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
