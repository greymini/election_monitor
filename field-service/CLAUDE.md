# Project Pulse — instructions for Claude Code

## What this is

A standalone service that adds two field modules to an existing, separately deployed platform (the Jharkhand Election Monitor):

1. **Expense Ledger**: the candidate and their representatives log money given and money spent, by web or Telegram bot.
2. **Ground Reports**: representatives send photos and a Hindi voice note to the same bot; reports are filed, routed and tracked to closure.

The design is in `Field_Modules_HLD.md` and `Field_Modules_LLD.md` in the repo root. Read both before any work. They are the source of truth except where this file overrides them (see "Deviations").

The existing platform's code is NOT in this repo and is not available on this machine. Other documents in the root describing it (HLD v2, LLD v2, README) are background only. Do not try to recreate the existing platform.

## Hard rules

1. **Standalone.** This service must run, test and deploy on its own. It will be merged with the core platform later by another developer.
2. **One boundary to the core.** Anything needed from the existing platform (users and roles, token verification, constituencies, blocks, areas, elector counts) goes through the `CoreAdapter` protocol in `pulse/core_adapter/`. No other module may assume how the core stores these. Locally we use `StubCoreAdapter` with seed data.
3. **Own schema, own migrations.** All tables live in the Postgres schema `field`. Migrations are numbered `field_0001`, `field_0002`, … in `db/migrations/`. Never create or alter tables outside the `field` schema.
4. **Build only the slice asked for.** Each prompt names a slice. Do not build ahead, even if the LLD describes it.
5. **Plan first.** For each slice, show the plan and file list and wait for approval before writing code.
6. **Dummy data only.** Seed data, fixtures and tests use invented names and amounts. Never real people, phone numbers or tokens.
7. **No secrets in the repo.** Bot token, webhook secret, JWT secret and database URL come from the environment. Keep `.env` git-ignored and maintain `.env.example`. The app must refuse to start with placeholder secrets.
8. **Report deviations.** If the LLD is ambiguous, wrong or impractical, stop and say so; do not silently pick something different. List every deviation in the closing summary.

## Deviations from the LLD (intentional)

| LLD says | Do this instead | Why |
|---|---|---|
| Migrations 0018–0023 | `field_0001…` own sequence | Avoid collisions with the core's migration numbers |
| Alter the existing `ground_report` table | Create `field.report` with the columns in LLD §3.4 | Core tables are not available here |
| `issue_id FK public_issue` | `issue_id uuid NULL`, no foreign key | Linking to core issues is decided at merge |
| `user_id FK app_user` | `user_id text NOT NULL UNIQUE`, resolved via `CoreAdapter` | Same reason |
| `ac_id`, `block_id`, `area_id`, `booth_uid` as foreign keys | Plain columns holding the core's ids, validated via `CoreAdapter` | Same reason |
| Metrics in `analytics/metric_sql.py` | `pulse/metrics/` in this repo, same single-source rule and parity test | Core module not available |
| Reuse `common` text normalisation and PII screen | Implement minimal local versions behind small interfaces in `pulse/common/` | To be swapped for the core's at merge |
| Reuse `job_run` and `alert_rule` | Create `field.job_run` and `field.alert_rule` | Same reason |

## Stack

- Python 3.11, FastAPI, psycopg 3 (no ORM), PostgreSQL 16
- Embedded PostgreSQL for local development and tests (`pgserver`), no Docker
- `httpx` for the Telegram Bot API (thin client of our own; no bot framework)
- `bcrypt` for PIN and token hashing
- pytest
- No frontend in this repo until asked

## Layout

```
pulse/
  config.py            environment loading, placeholder-secret check
  db.py                connection pool, transaction helper
  core_adapter/        base.py (protocol), stub.py, seed.json
  common/              text normalisation, PII screen (minimal local versions)
  team/                members, scopes, subtree helpers
  bot/                 client.py, dispatch.py, polling.py, flows (one file per flow), texts_hi.py
  ledger/              parse_amount.py, classify.py, service.py
  reports/             service.py, media.py
  asr/                 base.py and engines (later)
  notify/              alerts, digests (later)
  metrics/             single source of metric formulas
  api/                 main.py and routers
  worker/              job queue and scheduled jobs (later)
db/migrations/         field_0001_*.sql …
db/seeds/              buckets, categories, keywords
scripts/               dev_stack.py, run_polling.py
tests/
```

## Design rules to hold throughout

- **Money given is not money spent.** Only `kind = expense` counts as spend. Keep a named test for this.
- **Ledger is append-only.** No UPDATE of amount, kind, members, date. Void with a reason. `pulse/ledger/service.py` is the only code that writes `field.money_txn`.
- **Amounts** are `numeric(12,2)` in the database and `Decimal` in Python. Never float.
- **Same dispatcher for polling and webhook.** Entry points differ; logic does not.
- **Idempotency.** Every Telegram update is recorded by `update_id`; duplicates are ignored.
- **Private chats only.** Ignore group and channel updates.
- **All bot text is Hindi** and lives in `pulse/bot/texts_hi.py`. No user-facing strings elsewhere.
- **Sending is enough.** No flow may lose what the user sent because they did not finish it.
  Exception: an expense draft that expires (15 minutes) is discarded, and the user is told so on their next message. Report drafts are never discarded: they auto-file.
- **Machine output is labelled.** Suggested buckets, categories and transcripts record their source and never override a choice a person made.
- **Authorisation in the query**, not after it: finance scope, subtree and restricted categories.
- **Missing is NULL**, never 0 or a default.
- **Times** are stored in UTC and displayed in Asia/Kolkata.

## Working practice

- Small commits, one concern each, with a clear message.
- Every new behaviour gets a test. Tests must not use the network; Telegram is exercised with recorded update dicts and a fake client.
- Run the full test suite before reporting a slice as done.
- Closing summary for every slice: what was built, what was skipped and why, deviations from the LLD, commands to run it locally.

## Slices (do not start one until asked)

| Slice | Content |
|---|---|
| 1a | Skeleton, core adapter with stub, team tree, Telegram linking, polling and webhook entry points |
| 1b | Bucket presets, amount parser, classifier, ledger service, expense bot flow, "मेरा हिसाब" |
| 1c | Advance, acknowledgement, dispute, return, balances, ledger and summary API |
| 2a | Report draft assembly, categories, filing, auto-file, media storage |
| 2b | Alerts, escalation, status lifecycle, reporter feedback, inbox API |
| 2c | Hardening: PIN, per-AC settings, chat cleanup and media retention, outbox without message bodies, media route, escalation catch-up, health, preflight, runbook and merge notes |
| 3 | Transcription interface, digests, issue linking, impact follow-ups, need-versus-spend |
| 4 | Election-window register and ceiling, optional LLM assist |
