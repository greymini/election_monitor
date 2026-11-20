# Project Pulse — merge notes

This is for the developer who merges Pulse into the Jharkhand Election Monitor core. Pulse was built
standalone. Everything it needs from the core goes through one protocol, and every table it owns is
in the Postgres schema `field`.

## 1. The CoreAdapter (`pulse/core_adapter/base.py`)

The core implements this protocol. `StubCoreAdapter` (`stub.py`, data in `seed.json`) is the local
stand-in. Ids are opaque strings: Pulse stores them in plain columns and never parses them.

| Method | Expected behaviour | Used by |
|---|---|---|
| `get_user(user_id) -> CoreUser \| None` | The user, or `None` if unknown or disabled. | members API (link a member to a core user) |
| `verify_token(token) -> CoreUser \| None` | Validate a bearer token: signature, expiry, revocation. Return `None` for anything invalid; never raise for a bad token. Pulse turns `None` into 401. | every API route |
| `list_acs() -> list[Ac]` | Every AC the deployment knows, with `ac_number` (used in URLs `/acs/{n}/...`) and Hindi and English names. | URL → AC lookup (`ac_by_number`), admin checks |
| `list_blocks(ac_id) -> list[Block]` | Blocks of one AC. An empty list for an unknown AC. | team scopes, report area pickers, validation |
| `list_areas(ac_id, block_id=None) -> list[Area]` | Panchayats or wards of the AC, or of one block. `kind` is `panchayat` or `ward`. | scopes, report area, reach metrics |
| `electors_for_area(area_id) -> int \| None` | Electors from the linked roll snapshot. `None` when no snapshot is linked. Never 0 as a stand-in for unknown: Pulse's rule is "missing is NULL". | reach and per-elector metrics |

`CoreUser` has these fields:

- `user_id`
- `display_name`
- `role`: one of `admin | strategist | block | rep`
- `ac_ids`: the ACs the user may act in
- `block_ids`: empty means the whole AC

Pulse reads only `role == "admin"` (admin routes) and `ac_ids` (which ACs). Everything finer
grained is Pulse's own member model (§3).

Pulse does not call the core in hot paths beyond `verify_token`. Lookups are cheap in the stub. If
they are remote in the core, cache `list_acs`, `list_blocks` and `list_areas` in the adapter.

**Conformance tests.** `pulse/core_adapter/conformance.py` holds the contract as read-only checks:
- unique ids and both names on every AC, block and area;
- areas inside the blocks of their AC, and the block filter adding up to the whole;
- empty lists or `None` (never an exception) for unknown ids;
- electors an int or `None`;
- the known user's role, ACs and blocks pointing at things the adapter lists;
- junk and tampered tokens refused, a valid token resolving to the same user as `get_user`.

They run against the stub in the normal suite. To run them against the real adapter, write a factory that
returns `Target(adapter, user_id, token_for)` and run:

```
PULSE_CONFORMANCE_TARGET=package.module:factory python -m pytest tests/test_adapter_conformance.py
```

(PowerShell: `$env:PULSE_CONFORMANCE_TARGET="package.module:factory"; python -m pytest tests/test_adapter_conformance.py`.)
Without `token_for` the valid-token check is skipped, not passed.

## 2. Local stand-ins to swap for the core's versions

| Stand-in | Where | Interface to keep | Note |
|---|---|---|---|
| Text normalisation | `pulse/common/text.py` | `normalise(text) -> str`, `tokens(text) -> list[str]`, `fold_digits(text) -> str` | Used for keyword matching (buckets, categories, urgency) and name matching. Seeds are normalised with the same function at seed time, so **re-run the seeds** if the core's normaliser differs. |
| PII screen | `pulse/common/pii.py` | `screen(text) -> list[str]` of kinds (`epic`, `aadhaar`, `mobile`); also `MASK` | Typed free text is screened before it is stored. A non-empty result rejects it, or masks it in report notes. |
| `field.job_run` | `db/migrations/field_0007_jobs.sql` | `(job_name, worker_id, job_id, started_at, finished_at, status ok\|error\|retry, detail)` | Every worker run. `/health` and preflight read `max(finished_at)`. If the core's `job_run` is reused, point those two queries at it. |
| `field.job` (queue) | `field_0007` | `FOR UPDATE SKIP LOCKED`, 10-minute lease, `dedupe_key` | Not in the LLD as a core table. Keep it or map it to the core's queue. |
| `field.alert_rule` | `field_0008_triage.sql`, seeds `db/seeds/alert_rules.json` | `trigger`, `condition`, `recipients` / `fallback` (role codes), `after_minutes`, `at_local_time` | The core's `alert_rule` may have a different shape. Rules are read only through `pulse/notify/alerts.py:rules()`. |
| Storage | `pulse/common/storage/` | `put(bytes) -> StoredObject`, `get`, `exists`, `delete`; keys are sha256 | Local disk now. Swap in object storage behind the same protocol. |
| `field.issue`, `field.issue_event`, `field.issue_counter` | `field_0010_slice3.sql`, `pulse/reports/issues.py` | issue: `issue_no`, `title`, `category_id`, `block_id`, `area_id`, `status open\|closed`, `opened_by`, `closed_at`, `closed_by`, `close_note`; events `opened`, `renamed`, `report_linked`, `report_unlinked`, `closed` | Stand-in for the core's `public_issue` and `issue_event` (LLD §3.4). `report.issue_id` and `money_txn.issue_id` are plain uuids with no foreign key; only `pulse/reports/issues.py` sets `report.issue_id`. At merge, either map these to `public_issue` (keep `issues.py` as the one writer) or migrate the rows into it. |
| Speech-to-text engines | `pulse/asr/` | `transcribe(audio_path, lang) -> Result(text, confidence, engine)`; one module per engine exposing `make() -> Engine \| None` | Only `none` (default) and `fake` (tests) exist. A real engine is one new module, chosen by `ASR_ENGINE`. Audio is converted to 16 kHz mono with ffmpeg when it is on PATH. |

## 3. Roles and finance scope

Pulse keeps its own team tree in `field.member`, one row per person per AC. `user_id` holds the core
user id and is unique.

- `parent_member_id`: the tree. "Subtree" means the member and everyone below them
  (`pulse/team/tree.py`).
- `is_principal`: the candidate. One per AC. Sees everything and never has a balance.
- `is_coordinator`: sees every report of the AC, including restricted categories.
- `can_give_advance`: may give money to their own subtree.
- `finance_scope`, the ledger rows a member may see (`pulse/ledger/scope.py`). The checks are
  inside the SQL, never applied after the query.
  - `none`: nothing
  - `own`: rows where they are the payer, the receiver or the creator. This is the default.
  - `team`: the same, for anyone in their subtree
  - `all`: every row of the AC
- `member_scope`: the blocks and areas a member covers. Used for report areas and routing.
- **Admin** comes from the core only (`CoreUser.role == "admin"` with the AC in `ac_ids`). Admins
  manage members, links, PINs and per-AC settings through the API.
- **Team import.** `POST /acs/{n}/field/members/import` (admin) takes a CSV with columns:
  - `user_id`, `display_name`, `parent_user_id` (required);
  - `finance_scope`, `can_give_advance`, `is_coordinator`, `areas`, `blocks` (optional).

  `dry_run=true` is the default. Every row goes through the same `service.create_member` as the Team
  screen. A file with any bad row imports nothing, and each row comes back with its result. The
  principal is not imported: create them first. Format details are in `pulse/team/csv_import.py`; the
  Team screen has the button.
- **Audit.** Member create and update through the API, and each CSV import, write `admin_audit`
  rows. Names are not copied into the audit data.
- **Idempotency keys.** `POST /txns`, `/reports`, `/issues`, `/members` and `/members/import` accept
  an `Idempotency-Key` header (`pulse/api/idempotency.py`).
  - A repeat with the same key by the same user returns the first response, marked `Idempotent-Replay: true`.
  - The same key with a different body is refused with 422.
  - Keys are kept 48 hours.
  - The web forms always send one. If the core's web app calls these routes, it should too.
- **PII screen on typed web fields.** Report titles, issue titles, issue close notes and member names
  are refused (422) when they contain a phone, Aadhaar or voter-id number. Report notes and
  transcripts are masked instead, because refusing them would lose the report.
- Report visibility (`pulse/reports/access.py`):
  - the reporter sees their own reports
  - members see the reports of their subtree
  - the principal and coordinators see all reports
  - restricted categories are visible to the principal and coordinators only
- Issues (`pulse/reports/issues.py`): any member who can see reports reads issues, except issues
  in a restricted category (principal and coordinators only). Only the principal and coordinators
  open, rename, link, unlink and close them. A report joins only an open issue in its own area.
- Transcript corrections: the principal, coordinators and the report's assignee.
- Money beside reports and issues (`linked_spend`), need-versus-spend and team activity always
  go through finance scope: linked spend is summed inside the caller's scope and is absent for
  `none`; `GET /spend/need-vs-spend` needs `all`; `GET /team/activity` needs a core admin or
  finance scope `team` (their subtree) or `all`.

## 3a. Bot replies are sent after the commit

`pulse/bot/outgoing.py`. While an update is handled, the flows only note their replies. `dispatch`
commits the user's input first, then sends the replies in the same request, still holding the chat's
lock so one chat's replies keep their order.

- **If Telegram fails a reply** (429, a network error, a 5xx), that reply and every later one of the
  chat that confirms something go to the outbox as `alert_message` kind `reply`.
  - The outbox honours `retry_after` and backs off, and holds a chat's later replies behind earlier ones.
  - The reply is drawn from the database when it is finally sent, and says the entry was already saved
    (`render.reply`), so nobody sends it again.
- **No Telegram failure undoes or hides an entry.** It is in "मेरा हिसाब" and on the web at once. Only a
  failure before the commit produces "not saved, send again".
- **Replies that confirm nothing** (a prompt, an error, the menu) are not queued. If one fails while a
  draft is open, the draft's card is queued instead.
- **Card edits:**
  - an alert card edit that fails is redrawn by `tg_edit`;
  - a report draft card edit is redrawn by the coalesced `report_card` job, so an album never turns
    into a stream of messages.
- **PIN messages** are still deleted at once; a failed delete is retried by a `tg_delete` job, and the
  member is asked to delete it only if every attempt fails.
- **Worker jobs** that call flow code send directly, as before; the queue retries them.

At merge nothing changes for the core: the webhook still answers 200 for every update it recorded.

## 4. Environment variables

Secrets are required, and the app refuses to start with placeholders. Everything else has a
default.

| Variable | Purpose | Default |
|---|---|---|
| `DATABASE_URL` | PostgreSQL 16 URL | required |
| `TELEGRAM_BOT_TOKEN` | bot token from @BotFather | required, secret |
| `TELEGRAM_BOT_USERNAME` | without `@`; used for deep links | required |
| `TELEGRAM_WEBHOOK_SECRET` | 32–256 chars `[A-Za-z0-9_-]`, sent back by Telegram on every webhook call | required, secret |
| `JWT_SECRET` | ≥ 32 chars; stub token signing and media links (derived key) | required, secret |
| `CORE_ADAPTER` | only `stub` exists here | `stub` |
| `BOT_MODE` | `polling` or `webhook` | `polling` |
| `WEBHOOK_URL` | https URL of `/telegram/webhook`, required in webhook mode | — |
| `MEDIA_DIR` | private media store, outside the repo | `%LOCALAPPDATA%\pulse\media` / `~/.local/share/pulse/media` |
| `LEDGER_LARGE_ENTRY` | flag entries at or above (₹) | 25000 |
| `LEDGER_PIN_LIMIT` | bot asks for the PIN at or above (₹) | 50000 |
| `LEDGER_TAIL_THRESHOLD` | tail analysis band (₹) | 2000 |
| `LEDGER_BACKDATED_DAYS`, `LEDGER_UNDO_MINUTES`, `LEDGER_CONFIRM_AMOUNT_ABOVE`, `EXPENSE_SESSION_MINUTES` | ledger behaviour | 7, 10, 1000000, 15 |
| `LEDGER_ACK_REMIND_HOURS`, `LEDGER_ACK_FLAG_HOURS` | unacknowledged advance or return | 24, 48 |
| `REPORT_JOIN_WINDOW_SECONDS`, `REPORT_AUTOFILE_MINUTES`, `REPORT_ADD_MORE_MINUTES`, `REPORT_MAX_PHOTOS`, `REPORT_MAX_VOICE_SECONDS` | report intake | 180, 10, 10, 10, 300 |
| `TG_MAX_DOWNLOAD_BYTES` | Bot API getFile limit | 20971520 |
| `MEDIA_RETENTION_DAYS` | delete media files after; unset keeps them | unset |
| `VOID_RETENTION_DAYS` | stored only; no job acts on it (open decision §6) | unset |
| `CHAT_CLEANUP_HOURS` | delete the bot's money cards after; must be < 48 | 24 |
| `BCRYPT_ROUNDS` | PIN hash cost | 12 |
| `ASR_ENGINE` | speech-to-text engine module in `pulse/asr` (`none`, `fake`, …) | `none` |
| `FOLLOWUP_DAYS` | impact follow-up for ASSIST and WORKS expenses after (days) | 14 |

Development and test only (never set in production):
- `DEMO_MODE`: set by `scripts/demo.py` itself.
- `PULSE_CONFORMANCE_TARGET`: the adapter conformance tests (§1).
- `PULSE_API`: the Vite dev-server proxy target.
- `EM_BASE_URL`: the Playwright base URL.

Per-AC overrides of the large-entry flag, tail threshold, PIN limit, auto-file minutes, advance
reminder and flag hours, media retention, voided-entry retention, chat cleanup hours and follow-up days live in
`field.ac_setting`:

- read them only through `pulse/settings_ac.py:effective()`
- change them with `GET` / `PATCH /acs/{n}/field/settings` (admin only, audited)

## 5. Migrations

Pulse has its own sequence, `db/migrations/field_0001…`, recorded in `field.schema_migration` with
a sha256 of each file. Pulse never creates or alters anything outside the schema `field`.

| # | File | Content |
|---|---|---|
| 1 | `field_0001_team.sql` | member, member_scope |
| 2 | `field_0002_telegram.sql` | tg_link, tg_link_token, tg_update, tg_session |
| 3 | `field_0003_buckets.sql` | expense buckets, keywords, custom labels |
| 4 | `field_0004_ledger.sql` | money_txn (append-only trigger), txn_audit, txn_counter, election window |
| 5 | `field_0005_transfers.sql` | advances, returns, acknowledgement states, default finance scope `own` |
| 6 | `field_0006_reports.sql` | report (LLD §3.4 columns), report_category, report_media, media_asset, report_event, counters |
| 7 | `field_0007_jobs.sql` | job queue, job_run |
| 8 | `field_0008_triage.sql` | alert_rule, alert_message (outbox), duplicates, lifecycle columns |
| 9 | `field_0009_hardening.sql` | PIN columns, ac_setting, admin_audit, bot_message, outbox without bodies, media purge |
| 10 | `field_0010_slice3.sql` | report_transcript and human transcript columns, transcript suggestions, issue, issue_event, issue_counter, txn_followup, tg_link.last_seen_at, new outbox kinds (followup, digest_weekly), alert trigger weekly_digest, setting followup_days |
| 11 | `field_0011_register.sql` | admin_audit actions for election windows, statutory heads, label promotion and keywords; report.title_source; exp_custom_label.review_listed_at |
| 12 | `field_0012_member_audit.sql` | admin_audit actions member_created, member_changed, members_imported |
| 13 | `field_0013_idempotency.sql` | idempotency_key: first response of a web create, kept 48 hours |
| 14 | `field_0014_reply_outbox.sql` | outbox kind `reply`: bot replies Telegram refused for now, delivered later |

Apply them with `python -m pulse.migrate`, which is idempotent and holds an advisory lock. The same
command then applies the idempotent reference seeds (`pulse/seed.py`: buckets, categories, keywords, alert rules).

At merge, run Pulse's migrations after the core's. They depend on nothing outside `field`.

## 6. Open decisions

1. **Issues.** `field.issue` stands in for `public_issue` (§2). `report.issue_id` and
   `money_txn.issue_id` are `uuid NULL` with no foreign key. Decide whether they become foreign keys
   to `public_issue` and whether `field.issue` rows are migrated into it.
2. **Core ids as foreign keys.** `user_id`, `ac_id`, `block_id`, `area_id` and `booth_uid` are plain
   columns validated through the adapter. Decide whether to add FKs to the core's tables after the
   merge.
3. **Existing `ground_report` table.** Pulse created `field.report` instead of altering it. Migrate
   the old rows, or keep both and present them together.
4. **Text normalisation and PII screen.** Swap in the core's versions (§2), then re-seed keywords
   and re-run the classifier tests.
5. **`job_run` and `alert_rule`.** Keep Pulse's own tables or map them to the core's (§2).
6. **Voided-entry retention.** The setting is stored per AC and in the environment, but no job acts
   on it. Decide what "retention" means for an append-only ledger: hide from the bot, archive, or
   nothing.
7. **Media link binding.** A signed media link names the viewer it was issued to and one asset, and
   expires after 5 minutes. Access is re-checked for that viewer on every request. It is still a
   bearer link: a forwarded link opens for 5 minutes as the original viewer. If the core's web app
   has a session cookie, bind links to that session as well.
8. **Telegram rules.** The 48-hour delete window and the 20 MB download limit were taken from the
   Bot API documentation and not re-checked from this network (RUNBOOK §9).
9. **Metrics.** `pulse/metrics/` keeps the single-source rule and its parity tests. Decide whether
   they move into the core's `analytics/metric_sql.py`. Slice 3 added `areas.py` (reports_open,
   reports_per_1000_electors, need_vs_spend_rank_gap) and `activity.py` (member_active_days,
   followup_done_pct). Elector counts come from the adapter and are passed into the SQL as a
   parameter list, so the rank-gap SQL does not depend on where the core stores the roll.
10. **Speech-to-text engine.** No real engine is chosen (HLD decision 3). Transcripts are masked for
   PII before storage, and human corrections are kept apart as the evaluation set.
11. **Materialised views.** LLD §3.5 lists `field.mv_*` views refreshed every 15 minutes. Pulse
   computes the same figures on request; add the views if the core's dashboards need them.
12. **Spend without an area.** Expenses with only a block, or neither, do not count toward any area
   in need-versus-spend.
13. **Local demo (dev-only, do not merge).** Delete `pulse/demo/` (including `webapp.py`),
   `scripts/demo.py`, `db/seeds/demo_sample.json`, `docs/DEMO.md`, `tests/test_demo.py`,
   `web/src/devshell/` and `web/e2e/`. The demo-only users `usr-coordinator`, `usr-treasurer`,
   `usr-worker-c1` and `usr-worker-c2` (core users with no member row, for trying the CSV import) in
   `pulse/core_adapter/seed.json` go with the stub. Then take out the
   `demo_mode` / `demo_pgdata` / `load_demo_settings` lines in `pulse/config.py`, every
   `if settings.demo_mode` block in `pulse/api/main.py`, and the offset functions in
   `pulse/common/clock.py` (`utcnow()` goes back to plain `datetime.now(timezone.utc)`).
   Keep `verify_iat: False` in `pulse/api/media_tokens.py`: link times are checked against the
   injected clock, as `exp` already was.
14. **Statutory heads and keywords are shared.** `exp_bucket.statutory_head` (LLD §3.2) and
   `exp_keyword` have no AC column, so a mapping or keyword change applies to every AC. Both are
   therefore admin-only in Pulse. If several candidates' agents will map heads separately, move the
   mapping to a per-AC table. Promoted labels are AC-specific sub-heads (`exp_bucket.ac_id`), but
   their keyword goes on the shared parent bucket.
15. **Entries recorded before an election window was set.** `in_election_window` and the window
   flags are stamped when an entry is saved, and the ledger is append-only. An entry dated inside a
   window that was created later therefore does not count to `ceiling_used_pct`. The register lists
   these separately as `recorded_before_window`; it does not count or drop them silently. Decide
   whether such entries should be re-stamped through an audited treasurer action.

## 7. Frontend (`web/`)

React 18, Vite, TypeScript, Tailwind 3.4, Recharts 2, React Query 5, i18next, Leaflet (react-leaflet 4): the
core app's stack. `npm ci && npm run build` produces `web/dist`; `python -m scripts.demo` builds it and serves
it at `/` (demo mode only). In the merged product the core serves the pages.

### What to lift and what to drop

| Folder | Status | Note |
|---|---|---|
| `web/src/field/` | **lift** | The pages: `spend/` (overview, ledger, balances, add entry, register), `reports/` (inbox and drawer, map, issues), `admin/` (team, settings), `routes.tsx`, `nav.ts`, `data.ts` |
| `web/src/components/` | **lift or map** | KPI card, panel, pill tabs, filter row, data table, chips, drawer, empty/error states, chart wrappers. Replace with the core's own where it has them; the props are small |
| `web/src/lib/` | **lift** | `api.ts` (field API client), `format.ts` (Indian grouping, ₹, IST), `palette.ts` (bucket and category colours), `session.tsx` (the contract with the shell), `theme.tsx` |
| `web/src/i18n/` | **merge** | `hi.json` / `en.json`: fold into the core's catalogue. `keys.test.ts` fails on any missing key; keep it |
| `web/src/styles/tokens.css` | **map** | See token names below |
| `web/src/devshell/` | **dev-only, delete** | Copy of the core shell, dev sign-in with stub tokens, the "view as" strip |
| `web/e2e/`, `playwright.config.ts` | dev-only | Browser tests and `screens.mjs` run against the demo |
| `pulse/demo/webapp.py` | dev-only | Serves `web/dist` in demo mode |

**Shell contract.** The field pages read one React context, `SessionContext` (`web/src/lib/session.tsx`):
`token`, `acNumber`, `acName {name_hi, name_en}`, `userId`, `userName`, `role`, `financeScope`, `memberId`,
`isPrincipal`, `isCoordinator`. The core shell provides it from its own sign-in (`GET /acs/{n}/field/me`
returns everything except the token), and wraps the pages in `QueryClientProvider`, `ThemeProvider` and
i18next. Mount `<FieldRoutes />` under the core router and add the Field group (labelled "मैदान" in Hindi and "Field" in English, one at a time) from
`FIELD_PAGES` (`web/src/field/nav.ts`) to the core nav; each entry has a `visible(session)` rule that mirrors
the API's scope checks.

### Token names to map onto the core tokens

All colours, type and spacing are CSS variables in `web/src/styles/tokens.css` (dark on `:root`, light under
`[data-theme="light"]`). Tailwind reads them through `web/tailwind.config.js`, so mapping the variables is
enough.

| Pulse token | Role | Sampled value (dark) |
|---|---|---|
| `--em-page` / `--em-surface` / `--em-raised` | page, card/header, raised (active nav, chips, table header) | `#0d0d0d` / `#1a1a19` / `#232322` |
| `--em-border` / `--em-divider` / `--em-grid` / `--em-baseline` | borders, table rows, chart grid, axis | `#2c2c2a` / `#232322` / `#2a2a28` / `#383835` |
| `--em-text` / `--em-text-2` / `--em-muted` | primary, secondary, muted ink | `#ffffff` / `#c3c2b7` / `#898781` |
| `--em-accent`, `--em-accent-ink` | selected pill, primary button | `#3987e5`, white |
| `--em-ok` / `--em-warn` / `--em-serious` / `--em-bad` | status (always with icon and label) | `#0ca30c` / `#fab219` / `#ec835a` / `#d03b3b` |
| `--em-cat-1` … `--em-cat-12`, `--em-cat-other` | bucket and category palette (field-only; not in the core) | see tokens.css |
| `--em-seq-1` … `--em-seq-5` | one-hue magnitude scale | blue steps |
| `--em-font`, `--em-size-kpi/title/panel/body/note/label` | type | 36 / 23 / 18 / 18 / 14 / 14 px |
| `--em-radius-card/ctl/chip`, `--em-space-page/card/gap`, `--em-header-h` | shape and spacing | 12 / 8 / 6 px, 20 / 20 / 10 px, 64 px |

The light theme is derived, not sampled (no light screenshot was available): replace it with the core's
light tokens. The font family could not be confirmed from screenshots (`Inter, "Segoe UI", system-ui`).

**Palette.** `--em-cat-*` is a 12-slot palette made for buckets and report categories. It uses blues, cyans
and magenta-to-red only, so it never reuses the party colours (BJP orange, JMM green, JLKM violet, AJSU
yellow), and its slots are separated by lightness. Validated with the dataviz validator: worst adjacent
red-green-blind distance 13.6 (dark) and 13.7 (light), normal-vision 16.1/16.2, all slots above the chroma
floor. Slots fall outside the validator's lightness band on purpose (12 slots cannot be told apart by hue
alone), so every chart carries a legend with values, direct labels or a table view. Colour follows the entity
(`web/src/lib/palette.ts`), never its rank.

### Routes and API calls per page

All calls are `/acs/{n}/field/...` with the core bearer token; finance scope and report visibility are applied
by the API in the query, and the pages only hide what the API would refuse.

| Route | Page | API calls |
|---|---|---|
| `/field` | landing | `GET /me` (shell) |
| `/field/spend` | Spend overview (team, all) | `GET /spend/summary?group_by=bucket \| sub_bucket \| week,bucket \| area \| member \| pay_mode \| fund_source` with `date_from, date_to, block_id, area_id, member_id, bucket_id`; `GET /balances`; `GET /spend/tail` (all); `GET /spend/need-vs-spend` (all); `GET /buckets`, `GET /places` |
| `/field/ledger` | Ledger (own+) | `GET /txns/summary?grouping=day \| bucket \| member` (every figure on the screen, over all matching rows); `GET /txns` (paged 100 at a time, for display only; filters as above plus `kind, status, flag`); `GET /txns/{id}`; `POST /txns/{id}/void \| reclassify \| ceiling`; `GET /txns/export.csv?reason=` (all); receipt via the signed link in `GET /txns/{id}` |
| `/field/balances` | Team balances (team, all) | `GET /balances` (members, and `team_position {holds, owed}`, not netted); `GET /txns?status=disputed \| pending_ack`; `GET /spend/flow` |
| `/field/entry` | Add entry (own+) | `POST /txns/preview` (warnings), `POST /txns` (keyed) |
| `/field/register` | Election register (all) | `GET/POST/PUT /election-windows`; `GET /spend/register`; `GET /spend/register/review`; `POST /txns/{id}/ceiling`; `GET /statutory-heads`; `PUT /buckets/{id}/statutory-head` (admin); `GET /spend/register/export.csv?reason=` |
| `/field/reports` | Reports and drawer (members) | `GET /reports/summary`; `GET /reports?open=true`; `GET /reports/{id}`; `GET /reports/{id}/assignable`; `POST /reports/{id}/ack \| assign \| status \| merge \| title \| urgency \| transcript \| link-issue`; `POST /issues`; media via signed links |
| `/field/map` | Map (members) | `GET /reports?located=true[&open=true]`; `GET /places` (tile URL from `MAP_TILE_URL`) |
| `/field/issues` | Issues (members) | `GET /issues`; `GET /issues/{id}`; `PATCH /issues/{id}`; `POST /issues/{id}/close` |
| `/field/team` | Team (admin) | `GET /members`; `POST /members/import?dry_run=` (CSV, keyed); `PATCH /members/{id}`; `POST /members/{id}/link-token`; `DELETE /members/{id}/link`; `POST /members/{id}/pin/unlock \| reset` |
| `/field/settings` | Settings (all; AC settings admin) | `GET /buckets`; `GET /keywords`; `POST/DELETE /buckets/{id}/keywords` (admin); `GET /labels/pending`; `POST /labels/promote`; `GET/PATCH /settings` (admin) |

### Tests

`cd web && npm test` (Vitest: shared components, number formatting, chart wrappers, missing translation keys).
`npx playwright test` (Edge via `channel: "msedge"`, nothing downloaded) runs against a **fresh**
`python -m scripts.demo`: one test acknowledges and closes a sample report. `node e2e/screens.mjs` regenerates
`docs/screens/{dark,light}/`. `node e2e/audit.mjs` checks every page and drawer in both languages, both themes and at
1280, 1440 and 1920 px:
- header items overlapping, hidden or off-screen;
- the page overflowing sideways, or text clipped by its box;
- wrong-script UI text (Latin in Hindi, Devanagari in English).

Text people typed (names, titles, notes, keywords, statutory heads) is shown as typed and is skipped. One
Playwright test fails on any header overlap at those widths.

**The header** is the dev shell's copy of the core's. It wraps onto a second row when the nav and the controls do
not fit, which Hindi needs at 1280 and 1440. If the core's real header does not wrap, the Field group adds one more
item to it, so check it at 1280 in Hindi.
