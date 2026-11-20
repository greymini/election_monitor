# Election Monitor — Field Modules: Handoff

**Date:** 20 Nov 2026 · **Working name of this repo:** Project Pulse · **Name shown in the UI:** Election Monitor

This file is for whoever picks this code up on another machine. It covers:
- what the code is and what state it is in;
- how to run it;
- how to connect Telegram;
- how to join it to the existing Election Monitor codebase.

The commands, paths, variable names and counts below were checked against the code on 20 Nov 2026. Where this file and the code disagree, the code and `docs/MERGE.md` win. Review everything here before relying on it.

---

## 1. What this is

Two additions to the Jharkhand Election Monitor, built as one standalone service:

| Module | What it does |
|---|---|
| Expense Ledger | The candidate and representatives log money given (advances) and money spent (expenses), by Telegram bot or web. Entries are classified into 12 buckets and tracked by area, member and time, with a running balance per representative. |
| Ground Reports | Representatives send photos and a Hindi voice note to the same bot. Reports are filed and routed (urgent ones escalate), then assigned and tracked to closure. They can be grouped into issues and linked to spend. |

Design documents (in the repo root): `Field_Modules_HLD.md`, `Field_Modules_LLD.md`. Working rules for Claude Code: `CLAUDE.md`. `README (1).md` and the `Giridih_AC32_*` documents describe the existing platform and are background only.

## 2. Why it is built the way it is

The existing Election Monitor code was not available on the build machine. So:

1. **Standalone service.** Runs, tests and demos on its own.
2. **One boundary to the core: `CoreAdapter`** (`pulse/core_adapter/`).
   - Users and roles, token verification, constituencies, blocks, areas and elector counts all come through it.
   - Today a stub answers with invented data: AC 32, 3 blocks, 8 areas and 10 demo users.
   - The real implementation is the main merge task. A conformance test suite checks any implementation against the contract (§8).
3. **Own schema, own migrations.** All tables are in the Postgres schema `field`, and migrations are `field_0001…field_0014`. No core table is created or altered. Core ids (AC, block, area, user) are stored as plain text columns.
4. **Local stand-ins** for core pieces that could not be reused: text normalisation, PII screen, `job_run`, `alert_rule`, and issues (`field.issue` in place of `public_issue`). Each is listed in `docs/MERGE.md` §2.
5. **Dev-only pieces** to be removed or replaced at merge (`docs/MERGE.md` §6 item 13):
   - demo mode (`pulse/demo/`, `scripts/demo.py`);
   - the copied app shell and dev sign-in (`web/src/devshell/`);
   - the browser tests (`web/e2e/`);
   - the stub adapter's seed file.

## 3. What is built

| Slice | Content | State |
|---|---|---|
| 1a | Skeleton, core adapter + stub, team tree, Telegram linking, polling and webhook entry points | Built, tested |
| 1b | Buckets and keywords, amount parser, classifier, ledger service, expense bot flow, "मेरा हिसाब" | Built, tested |
| 1c | Advances, acknowledgement, dispute, returns, balances, ledger and spend API | Built, tested |
| 2a | Report intake by bot, categories, auto-file, media storage, minimal worker, receipt photo | Built, tested |
| 2b | Alerts, escalation, assignment, status lifecycle, reporter feedback, duplicates, daily digest, reports API | Built, tested |
| 2c | PIN, per-AC settings, chat cleanup, media retention, health, preflight, runbook, merge notes | Built, tested |
| 3 | Transcription interface (no real engine), issues, expense links, impact follow-ups, need-versus-spend, weekly digest | Built, tested |
| Demo | Browser demo with chat simulator and sample data, no Telegram needed | Built, used |
| Final | Election register, chart endpoints, label review, web screens (spend, ledger, balances, add entry, register, reports, map, issues, team, settings) | Built, tested |
| Handoff | Adapter conformance tests, team CSV import (API and button), header layout fix, layout and language audit | Built, tested |
| Review fixes | See below | Built, tested |

Review fixes:
- **Bot replies are sent after the commit.** A reply Telegram refuses is delivered later by the outbox and says the entry was already saved (`docs/MERGE.md` §3a).
- **PII screen on typed web fields:** titles, issue close notes, member names.
- **Ledger figures from the server:** `GET /txns/summary`; rows are paged for display only.
- **Idempotency keys** on web creates.
- **Team position not netted:** what the team holds and what it owes are separate figures.
- **Member audit.**
- **58 stale-button tests** and the fixes they exposed.

Test counts at the last full run (20 Nov 2026):
- Python: 758 passed.
- Web unit tests: 27 passed.
- Browser tests (Edge): 12 passed.

Run the suites to get current numbers (§5).

**Important limit on "tested":** every Telegram interaction has run only against a fake client. The bot has never talked to the real Telegram API. Expect a few fixes on first real use: card editing, sending a photo by file id, the contact button, and rate-limit handling.

## 4. What is not done

| Item | Notes |
|---|---|
| Real `CoreAdapter` | Reads the live `ac`, `block`, `area` and user tables, and verifies the live login token. Six methods; the contract is in `docs/MERGE.md` §1. Run the conformance tests against it. |
| `rep` role in the core | Every team member needs a core user, including bot-only representatives. Decide whether those accounts can log in to the web. |
| Lifting the web pages into the core React app | Pages are in `web/src`. `docs/MERGE.md` §7 says which folders to lift, the shell contract and the token mapping. Fonts and the light theme were inferred from screenshots. |
| Real Telegram test | See §7. |
| Speech-to-text engine | The interface exists (`pulse/asr/`), with engines `none` and `fake` only. Voice notes are stored and playable, but there is no transcript until an engine is chosen and tested on real Giridih voice notes. Khortha-influenced speech is the expected weak point. |
| Statutory-head mapping | Ships empty and editable. Needs the candidate's election agent. |
| Entries logged before an election window exists | They do not count toward the ceiling and are listed separately. Create the window before the period starts; there is no way yet to pull earlier entries in. |
| LLM assist | Deliberately not built. |
| S3 media backend | The `Storage` protocol is ready (`pulse/common/storage/`); only local disk is implemented. |
| Voided-entry retention | The setting is stored; nothing acts on it. |

## 5. Moving to another machine

**What to take:** an archive of the tracked files only. From the repo root on the build machine:

```
git archive --format=zip -o election-monitor-field.zip HEAD
```

The archive leaves out everything that must not travel:
- secrets and local environments: `.env`, `.venv/`;
- builds and dependencies: `web/node_modules/`, `web/dist/`;
- databases and media: `.pgdata/`, `.demo-pgdata/`, `.demo-media/`, `media/`;
- caches and the design screenshots (`design-ref/*.png`).

Never copy `.env` or a bot token between machines in a zip.

**Needed on the new machine:**
- Python 3.11 (`pyproject.toml` requires `>=3.11,<3.12`).
- Node.js 18 or later for the web build (built with Node 24 and npm 11).
- Network access to PyPI and the npm registry.
- For Telegram tests, network access to `api.telegram.org`.
- For the browser tests, Microsoft Edge (Playwright uses the installed Edge; nothing is downloaded).

Python versions are pinned in `constraints.txt` and npm versions in `web/package-lock.json`.

**Set up and verify.** Windows (PowerShell):

```
python -m venv .venv
.venv\Scripts\activate
pip install -c constraints.txt -e ".[dev]"
python -m pytest -q                 # full Python suite
python -m scripts.demo              # builds the web app on first run, loads sample data
```

macOS / Linux:

```
python3.11 -m venv .venv
source .venv/bin/activate
pip install -c constraints.txt -e ".[dev]"
python -m pytest -q
python -m scripts.demo
```

Open the address the demo prints, `http://127.0.0.1:8787/` by default (`--port N` changes it).
- `/` is the Election Monitor field screens.
- `/demo` is the developer chat simulator.

The demo needs no `.env` and no Telegram. Ctrl+C stops it and its database.

Web tests, from `web/` (the browser tests need a freshly started demo running):

```
npm ci
npm test                            # unit tests (Vitest)
npx playwright test                 # browser tests on Edge
node e2e/audit.mjs                  # layout and language audit of every screen
```

Always run Python commands with the virtual environment active. "No module named …" almost always means it is not.

**PowerShell commands and their macOS/Linux equivalents:**

| PowerShell (Windows) | macOS / Linux (bash, zsh) |
|---|---|
| `python -m venv .venv` | `python3.11 -m venv .venv` |
| `.venv\Scripts\activate` | `source .venv/bin/activate` |
| `copy .env.example .env` | `cp .env.example .env` |
| `$env:ASR_ENGINE="fake"; python -m scripts.run_worker` | `ASR_ENGINE=fake python -m scripts.run_worker` |
| `$env:PULSE_CONFORMANCE_TARGET="pkg.mod:factory"; python -m pytest tests/test_adapter_conformance.py` | `PULSE_CONFORMANCE_TARGET=pkg.mod:factory python -m pytest tests/test_adapter_conformance.py` |
| `Remove-Item -Recurse -Force .demo-pgdata` | `rm -rf .demo-pgdata` |
| `Get-Content .env` | `cat .env` |

Media defaults to `%LOCALAPPDATA%\pulse\media` on Windows and `~/.local/share/pulse/media` elsewhere. Everything else (`python -m …`, `npm …`, `npx …`, `uvicorn …`, `git …`) is the same on every system.

## 6. Running it for real (still standalone, stub team)

```
copy .env.example .env              # macOS/Linux: cp .env.example .env
```

Fill in `.env`:

| Variable | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_BOT_USERNAME` | From @BotFather. Use a **test bot**, never the production one |
| `TELEGRAM_WEBHOOK_SECRET`, `JWT_SECRET` | Two different values from `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `DATABASE_URL` | Written by the next command |

Every other variable is optional and documented in `.env.example` and `docs/MERGE.md` §4. The app refuses to start while any required value is still `change-me`.

```
python -m scripts.dev_stack up --seed-team      # database, migrations, presets, dummy team
python -m scripts.preflight                     # checks secrets, migrations, media folder, worker, webhook mode
python -m scripts.webhook info                  # proves Telegram is reachable from this machine

uvicorn pulse.api.main:create_app --factory --port 8000    # terminal 1
python -m scripts.run_polling                              # terminal 2 (local: long polling)
python -m scripts.run_worker                               # terminal 3 (downloads, alerts, timers)
```

To link a phone to a dummy member:
1. Get a token with `python -m scripts.dev_token usr-principal`.
2. Call `POST /acs/32/field/members/{member_id}/link-token`, or press **Make a link** on the Team screen.
3. Open the returned link on the phone and press Start.

Full commands are in `docs/RUNBOOK.md`.

Polling will not run while the bot has a webhook set; it exits with a message. Production uses the webhook: see `docs/RUNBOOK.md` §2–3 for registering it with the secret.

## 7. Telegram test order

Two Telegram accounts are needed (one account links to one member).

1. **Linking:** open the link and press Start; reuse the link (refused); unlink, then message (fixed reply).
2. **Expenses (1b):**
   - `1500 chai nashta baithak`;
   - `1500`, then "अन्य";
   - `30000 tent` (warning);
   - `15 lakh` (confirm);
   - a message with a phone number (refused);
   - double-tap confirm, undo, "मेरा हिसाब".
3. **Advances (1c):** the principal gives, the worker acknowledges, disputes, returns; balances reconcile.
4. **Reports (2a):**
   - one photo; an album of three; voice then text;
   - walk away: auto-file after 10 minutes, or set `REPORT_AUTOFILE_MINUTES=2` in `.env` for testing;
   - `turant …`; location; a receipt photo in an expense.
5. **Alerts (2b):**
   - an urgent report reaches the principal; acknowledge; assign; start and close;
   - escalation;
   - a restricted category is text-only;
   - merge duplicates; digest.
6. **PIN (2c):** set; large entry; three wrong attempts lock; unlock by API or on the Team screen; expired prompt.
7. **Slice 3:** transcript on the card (`ASR_ENGINE=fake` in `.env`); link to issue; expense linked to a report; follow-up.

Also, with the real API:
- Switch the phone's network off while confirming an entry: the entry must still appear in "मेरा हिसाब", and the delayed reply must say it was already saved.
- Send an album of ten photos quickly: one card, edited in place.

Record anything that differs from the simulator; those are the real-API fixes.

## 8. Joining it to the existing Election Monitor repo

Do this with the existing repo present on the same machine. Suggested order:

1. **Read `docs/MERGE.md`** end to end. It is the detailed contract; this section is the outline.
2. **Place the code.** Either keep it as a separate service next to the core (own process, same database, schema `field`), or move `pulse/` into the core repo as a package. A separate service is less work and matches the plan to move toward services.
3. **Database.** Point `DATABASE_URL` at the core's Postgres and run `python -m pulse.migrate`. It applies the migrations and then the reference seeds (buckets, categories, keywords, alert rules), and touches only the `field` schema. Confirm the core's privacy scan and backup cover that schema.
4. **Write the real adapter** in `pulse/core_adapter/`, implementing the six methods against the core's tables and login token.
   - Set `CORE_ADAPTER` to select it (`make_core_adapter` in `pulse/api/main.py`).
   - Run the conformance tests against it: `PULSE_CONFORMANCE_TARGET=package.module:factory python -m pytest tests/test_adapter_conformance.py`.
   - `docs/MERGE.md` §1 says what `factory` returns.
5. **Roles.** Add `rep` to the core's roles. Create core users for the candidate and each representative.
6. **Team.**
   - Create the candidate as the single principal for the constituency first.
   - Add the rest on the Team screen with **Import CSV**: check first, then import; one bad row imports nothing. Or add them through `POST /acs/{n}/field/members`.
   - Set areas, finance scope and coordinators. Generate Telegram links.
7. **Web.**
   - Lift the page folders into the core React app and map the token names onto the core's `tokens.css`.
   - Add the Field nav group (labelled "मैदान" in Hindi, "Field" in English) and the routes.
   - Delete `web/src/devshell/`.
   - Check the core header at 1280 px in Hindi once the group is added (`docs/MERGE.md` §7).
8. **Swap stand-ins** for the core's own text normalisation, PII screen, `job_run` and `alert_rule`. Decide whether `field.issue` maps onto `public_issue`.
9. **Remove dev-only code** as listed in `docs/MERGE.md` §6 item 13:
   - demo mode and its routes;
   - the demo-only stub users;
   - `web/e2e/`;
   - the "view as" switch.
10. **Deploy:**
    - the API;
    - the worker (now required, not optional);
    - the webhook, registered with the secret;
    - private media storage;
    - `python -m scripts.preflight` green.

## 9. Decisions still open

Product and policy decisions (from `Field_Modules_HLD.md` §9 and the build):

1. **Privacy rule for field data.** The platform's "no individual data" principle is kept absolute for voter-roll data only; field data is a separate class. Beneficiaries are recorded as type and count. Confirm or tighten.
2. **Full finance access.** Who holds finance scope `all` besides the candidate. Technical admins do not see the ledger by default.
3. **Retention periods** for media and voided entries (`docs/MERGE.md` §6 item 6).
4. **Speech-to-text:** which engine, and where it runs (`docs/MERGE.md` §6 item 10).
5. **Legal review.** The election agent and counsel should review the election-period spending records:
   - ceiling tracking;
   - cash-limit flags;
   - assistance and gift buckets during the model code;
   - the fact that a complete ledger is evidence if leaked.

   Nothing in this system is legal advice.
6. **Shared statutory heads and keywords.** Whether they should be per constituency; today they are shared (`docs/MERGE.md` §6 item 14).
7. **Media links** are bearer links valid for 5 minutes. Closing that gap needs a browser session cookie (`docs/MERGE.md` §6 item 7).
8. **Entries recorded before an election window was created** (`docs/MERGE.md` §6 item 15).

The technical merge decisions are in `docs/MERGE.md` §6: issues, foreign keys to core ids, the old `ground_report` table, stand-ins, metrics and materialised views.

## 10. Checklist for the other machine: what could not be verified here

The code review before this handoff could not check these without the real Telegram API or the core
codebase. Tick each one off there.

**Needs the real Telegram API** (a test bot, two phones):
- [ ] Rate limits: a 429 carries `retry_after`, and the outbox waits exactly that long. Bot replies queued during a 429 arrive in order, once, saying "already saved".
- [ ] The 48-hour delete window for the bot's own messages (chat cleanup), and the 20 MB `getFile` download limit.
- [ ] Editing a card that is too old or was deleted ("message can't be edited"): the card is sent again.
- [ ] Sending a photo by `file_id` (alert cards with the report photo).
- [ ] The contact button (`tg://user`), including a reporter whose privacy settings refuse it.
- [ ] Album delivery timing: the photos of one album join one report draft with one card.
- [ ] Webhook redelivery: Telegram resends an update when the webhook does not answer 200, and the duplicate is ignored.
- [ ] Reply speed: replies now go out right after the commit, in the same request; check it still feels instant on a slow network.
- [ ] Deleting PIN messages, and the retry when a delete fails.

**Needs the core codebase:**
- [ ] The core's token format and revocation, through the real `CoreAdapter`. Run `tests/test_adapter_conformance.py` against it with a token (`PULSE_CONFORMANCE_TARGET`).
- [ ] The core's roles and `ac_ids` match what the stub assumes (`admin | strategist | block | rep`; block users scoped by `block_ids`).
- [ ] The core's privacy scan and backups cover the `field` schema.
- [ ] The core's header with the Field group (मैदान) added, at 1280 px in Hindi: nothing overlaps (`docs/MERGE.md` §7).
- [ ] If the core's web app calls the field create routes, it sends `Idempotency-Key`.

## 11. Where things are

| Need | Look in |
|---|---|
| Design and presets (buckets, categories) | `Field_Modules_HLD.md`, `Field_Modules_LLD.md`, `db/seeds/` |
| Merge contract, stand-ins, env variables, open decisions, frontend | `docs/MERGE.md` |
| Operating it: webhook, token rotation, lost phone, PIN reset, backups | `docs/RUNBOOK.md` |
| Demo walkthrough | `docs/DEMO.md` |
| Screens, both themes | `docs/screens/` |
| Adapter contract tests | `pulse/core_adapter/conformance.py`, `tests/test_adapter_conformance.py` |
| Team CSV import | `pulse/team/csv_import.py` (format), `POST /acs/{n}/field/members/import` |
| Bot text (all Hindi strings) | `pulse/bot/texts_hi.py` |
| Web text (Hindi and English) | `web/src/i18n/hi.json`, `web/src/i18n/en.json` |
| Metric formulas (single source) | `pulse/metrics/` |
| The only code that writes the ledger | `pulse/ledger/service.py` |
| Pinned dependencies | `constraints.txt` (Python), `web/package-lock.json` (npm) |
| Bot replies after the commit, outbox fallback | `pulse/bot/outgoing.py`, `pulse/notify/render.py` (`reply`), `docs/MERGE.md` §3a |
| Idempotency keys for web creates | `pulse/api/idempotency.py` |

## 12. Starting prompt for Claude Code on the new machine

```
Read HANDOFF.md (including the checklist in section 10), CLAUDE.md and docs/MERGE.md fully. This service was built standalone
and now sits next to the existing Election Monitor repo at <path>. Do not change
anything yet. First: run the Python suite and the demo here to confirm it works on
this machine. Then read the existing repo's user, role, auth, ac/block/area and
elector-count code and give me a plan for the real CoreAdapter, the rep role, and
where the web pages go, listing every assumption in MERGE.md that turns out to be
wrong. Wait for my OK before writing code.
```

---

All figures, classifications and transcripts produced by these modules must be reviewed by a person before decisions rely on them.
