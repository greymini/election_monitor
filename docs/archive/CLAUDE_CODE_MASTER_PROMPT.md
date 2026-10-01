# Claude Code — Master Build Prompt (v2: multi-AC, logic corrections, next layer)

Paste this whole file into Claude Code at the repo root.

---

## 0. Role, inputs and precedence

You are the lead engineer on this repository for this session. Before editing anything, read these files completely, in this order:

1. `AUDIT_REPORT.md` — defects by ID (A1…H). Every ID must end the session as `fixed`, `deferred (reason)` or `not reproducible (evidence)`.
2. `MULTI_AC_EXPANSION_SPEC.md` — v2 design: six constituencies, data model, new data points, scraper rules, UI, UAT-1 scope (§8).
3. `claude_code_fix_and_expand_prompt.md` — phased fix plan. This master prompt **supersedes** it where they differ; use it for detail.
4. `RUN.md` — the operator's run guide. Commands you implement must match it; if you must change a command, update `RUN.md` in the same commit.
5. `Giridih_AC32_Election_Monitor_HLD.md`, `..._LLD.md`, `README.md`, `docs/RUNBOOK.md`.

**Precedence when documents conflict:** this prompt → spec → fix prompt → LLD → HLD → existing code. If you find a conflict that none of these resolve, write it to `DECISIONS.md` with the option you chose and why, then continue. Do not stop to ask unless the choice would store individual voter data, fabricate a number, or delete source data.

## 1. Operating mode

- **State detection first.** Run `git log --oneline -30`, check for `UAT_READINESS.md`, `PROGRESS.md`, migration files ≥ `0013`. Some of the fix prompt may already be done. Build on what exists; do not redo committed work. Record what you found at the top of `PROGRESS.md`.
- **Plan file.** Maintain `PROGRESS.md` as a checklist of every task below with status `todo / doing / done / blocked (why)`. Update it at every commit.
- **Two tracks, strictly ordered.** Track A (UAT-critical) must be fully green before any Track B work begins. UAT is tomorrow. If time runs out, a green Track A with Track B untouched is success; a half-done Track B with a red Track A is failure.
- **Commit discipline.** One logical change per commit, conventional-commit messages, tests and `docker compose build` passing before each commit. Never commit a failing state.
- **Non-negotiables** (from all prior documents — restated because they override convenience):
  1. No individual voter data anywhere: DB, disk cache, logs, fixtures, test output, API responses, LLM prompts.
  2. Every loader idempotent and transactional: stage → validate → promote in one transaction.
  3. Absent data is `NULL` and renders as "not loaded", never 0, never 50%, never a default.
  4. Every loaded row carries `source_doc` + `source_page` (or `source` for manual CSVs).
  5. Chatbot stays parked behind `CHAT_ENABLED=false`; the app boots without `sqlglot`/`anthropic`.
  6. Constituency facts for the five new ACs are seeded `verified=false`. Never "correct" a seeded number from memory.
- **If a real source document is unobtainable** (portal down, no network), continue on a recorded fixture, but mark every affected acceptance check `PARTIAL — fixture only` and say so on line 1 of `UAT_READINESS.md`.

---

## 2. Track A — UAT-critical (do first, in this order)

Detailed steps are in `claude_code_fix_and_expand_prompt.md` Phases 0–2. Execute them with these additions and acceptance gates.

### A-1 Safety (fix prompt Phase 0)
Audit IDs: E1, C3, C13, A1, A3, A5, E3, E4, G2, A6.
Additional:
- Test proving an empty-key HS256 token is rejected (write it before the fix, see it fail, then fix).
- `scripts/purge_roll_cache.py` + `python -m ingest.validate --privacy` (filesystem EPIC scan of `OCR_DIR`, `raw/` text sidecars, `/tmp`, logs directory; DB scan of every `text`/`jsonb` column for EPIC, 10-digit mobile `(?<!\d)[6-9]\d{9}(?!\d)`, 12-digit Aadhaar `(?<!\d)\d{4}\s?\d{4}\s?\d{4}(?!\d)`).
- `GET /config` returns `{chat_enabled, acs:[…], version, build_time}`; frontend reads it once at boot.

**Gate A-1:** clean clone → `cp .env.example .env` with a generated secret → `docker compose build && docker compose up -d` → `/api/health` ok; API refuses to start with the placeholder secret; privacy scan clean; importing `api.main` with `chatbot` package removed succeeds.

### A-2 Multi-AC spine (fix prompt Phase 1)
Implement spec §2 fully. Specifics:
- `booth_uid = f"{ac_number}-B{seq:04d}"` from a per-AC sequence `booth_seq_{ac_number}`; never derived from PS number.
- Migration of existing Giridih rows: `B0147 → 32-B0147`, all FKs rewritten in one transaction; verify row counts before/after in the migration and `RAISE EXCEPTION` on mismatch.
- `ac` seed columns: `ac_number, name_en, name_hi, reservation, district(s), pc_number, verified, bypoll_due`. Seed Giridih `verified=true` only for facts already reconciled; the other five `verified=false`.
- `result_ac_total` seeded for 2019 and 2024 VS for all six ACs from the spec §1 values where given; empty (not zero) where the spec does not give a number. Store `source='spec-unverified'`.
- API: every data route is under `/acs/{ac_number}/…`; keep old Giridih routes as 308 redirects to `/acs/32/…` for one release.
- Frontend: AC switcher (`?ac=` in URL, persisted), "unverified" badge from `ac.verified`, `/compare` page reading `/acs`.

**Gate A-2:** `/acs` lists six ACs; switching AC changes every page; Giridih data identical before and after the migration (write a snapshot test on `mv_result_booth_wide` counts and totals).

### A-3 Giridih numbers correct (fix prompt Phase 2, plus §3 logic below)
Audit IDs: C1, C2, C4–C12, C15–C17, B1, B2, B4, B5, B10, B11, D1–D9.
Implement the canonical metric definitions in §3.2 exactly.

**Gate A-3 (all must pass, as automated tests in `tests/e2e/`, against a real Postgres):**
- Giridih VS-2024 Form 20 loads with zero errors; every candidate column resolved to a party or to `IND:<name>`; AC totals equal ECI to the vote; AC `margin_pct` = 1.85.
- Re-running the load leaves every MV byte-identical (compare `md5(string_agg(t::text, '' ORDER BY …))`).
- One hand-verified booth: pick a booth, write its candidate votes from the PDF into the test, assert equality.
- `check_no_dropped_booths` passes for VS-2024 and (if loaded) VS-2019.
- `mv_new_voter_share` total additions equal `SUM(roll_change.additions)` for the linked window.
- Turnout non-NULL for every booth with a linked roll snapshot.
- Privacy scan clean after a roll load.

### A-4 UAT scaffolding (new)
- `scripts/uat_seed.sh` — idempotent: migrations, seeds, Giridih loads (from `raw/` if present, else fixtures), refresh, validate. One command to bring a fresh environment to the UAT state.
- `scripts/uat_users.py` — creates one user per role (`admin`, `strategist`, `block` scoped to Pirtand) with printed one-time passwords.
- `UAT_SCRIPT.md` — test cases for human testers tomorrow: numbered, each with *steps*, *expected result*, *pass/fail box*. Cover: login per role and what each role can/cannot see; AC switching; Giridih overview numbers vs ECI; booth table filter/sort/export; booth card source-page link opens the right PDF page; map colours signed by winner and filters; voters page; caste page confidence greying and block-role denial; crosswalk review-queue workflow end to end; honest "not loaded" states on the other five ACs; news stream; alert rule firing; a deliberate bad upload being rejected by validation.

**Gate A-4:** `scripts/uat_seed.sh` on a fresh volume completes and `UAT_SCRIPT.md` cases 1–N pass when you walk them via the API and a headless browser (Playwright) where practical. Record results in `UAT_READINESS.md`.

Commit and tag `uat-1-candidate` when Track A is green.

---

## 3. Logic updates — authoritative definitions

These replace whatever the code currently does. Implement once in SQL views or a single Python module; never duplicate a formula in two places.

### 3.1 Form 20 party resolution
1. Normalise header cell: NFC, strip, collapse whitespace, Devanagari nukta/chandrabindu normalisation (`common/textnorm.py`), remove punctuation except parentheses.
2. If the cell contains `(…)`, look up the bracket text in `party_alias` (script-aware). Hit → party.
3. Else fuzzy-match the name against that election's candidates in `result_ac_total` (and `candidate_profile` if loaded): Jaro-Winkler on transliterated Latin, threshold 0.88; tie or <0.88 → unresolved.
4. `NOTA`/`नोटा`/`None of the Above` → the NOTA party; it is a candidate row.
5. Independents resolve to `party=IND` with `candidate_id` kept distinct; ranking is by candidate, not party bucket.
6. Any unresolved column → load aborts; review-queue item lists the raw header and the top-3 candidate guesses with scores.

### 3.2 Canonical metric definitions (single source of truth)

| Metric | Definition | NULL when |
|---|---|---|
| `valid_votes` | Σ candidate votes **including NOTA** (= Form 20 "total valid votes") | no result |
| `votes_polled` | `valid_votes + rejected` (+ tendered excluded) | no result |
| `turnout_pct` | `votes_polled / electors × 100` (electors from linked roll snapshot; fallback PS list; never Form 20) | electors unknown |
| `share_pct` | `candidate_votes / valid_votes × 100` | no result |
| `margin_votes` | winner − runner-up, both real candidates (NOTA never ranks) | fewer than 2 candidates |
| `margin_pct` | `margin_votes / valid_votes × 100` | as above |
| `signed_margin_pct` | `+margin_pct` if contest pair's first party wins, `−` if second, NULL if neither (map ramp) | as above |
| `swing_pct` (party, booth) | `share_now − share_prev` for the **same election type** and **same booth_uid** via crosswalk | no prior election, or crosswalk row `reviewed=false AND confidence<0.85`, or booth split/merged without aggregation |
| `alliance_swing_pct` | same, but summing parties by `party_alliance` for each event | as above |
| `new_voter_pct` | additions in window / electors at window end × 100; window = (linked roll of previous GE, linked roll of target] | either roll missing |
| `net_roll_change_pct` | (additions − deletions) / electors at window start × 100 | either roll missing |
| `transfer_delta` (party) | `share_VS − share_LS` same year, same booth | either leg missing |
| `floating_pct` | Pedersen index ½ Σ|Δshare| across parties, LS vs VS same year | either leg missing |
| `volatility` | stdev of `signed_margin_pct` over available VS years | fewer than 2 years |
| `priority_score` | weighted **per-AC** percentile ranks: 0.35 closeness (1 − margin percentile), 0.25 new_voter_pct, 0.20 floating_pct, 0.20 volatility; missing inputs dropped and weights renormalised, with `inputs_used` stored | all inputs missing |

Also: the **contest pair** (for signed margin and scenario) is configured per AC per event in `ac_contest(ac_id, event_id, party_a, party_b)` — Giridih JMM/BJP, Silli JMM/AJSU, Dumri JLKM/JMM, Kanke INC/BJP, etc. Do not hardcode JMM/BJP anywhere.

Add `validate.py` checks that recompute headline AC metrics from base tables and assert they equal the MV values and the published ECI values.

### 3.3 Booth crosswalk
- Score = 0.5·building + 0.3·place + 0.2·roll_part (roll_part now stored on `booth` and `ps_list_entry`).
- Bands unchanged (≥0.85 auto, 0.65–0.85 review with row written `reviewed=false`, <0.65 new).
- Detect **splits** (one old → many new with scores ≥0.75) and **merges** (many old → one new); store in `booth_lineage(old_election_id, old_ps, new_booth_uid, kind, weight)`; swing for split/merged booths is computed on the aggregated lineage group and flagged in the UI.
- Crosswalk editor supports accept / reject / reassign / mark split / mark merge, each writing an audit row.

### 3.4 Caste and religion estimates
- Fix D8: rescale SC/ST to target, then scale *only the non-SC/ST* members so the total is 100 and SC/ST equal the documented blend.
- Unmatched surnames become an explicit `UNMATCHED` bucket; `est_pct` is share of *all* electors; a separate `matched_pct` is exposed. No proportional extrapolation of the unmatched residual.
- Religion is an aggregate bucket derived from the same pass (Muslim / not-Muslim / unmatched) with its own confidence.
- `caste_estimate` gains `ac_id`, `method_version`. Confidence formula unchanged except coverage is `matched / electors`.
- Surname dictionary: expand toward ~300 with Kurmi/Mahato, Yadav, SC (Turi, Rajwar, Bhuiyan, Dusadh, Ravidas, Baitha — relevant for Kanke SC), ST (Santhal, Munda, Oraon, Bedia — relevant for Silli/Kanke) spellings in both scripts. Every entry has `source` and `weight`; ambiguous surnames split.

### 3.5 Scenario engine
- Per AC, per contest pair; baseline = that AC's `is_baseline` election.
- Transfer matrix built on **alliances** of the target event, not raw parties.
- Winner = argmax of projected candidate/alliance totals excluding NOTA.
- Noise applied to source totals and transfer rows before renormalisation (D6); output labelled "sensitivity range at noise=x".
- Inputs echo back verbatim; response includes `baseline_source` and `verified` flag of the AC.

### 3.6 LS segment
- `parse_form20 --type LS` reads the PC-level Form 20, extracts only rows for the target AC's polling stations (by AC header section), writes an `election` row for the AC segment of the LS event; candidates shared via `pc_candidate`.

---

## 4. Track B — Next layer (only after Track A is tagged)

### B-1 Scraper layer (spec §5)
- `scrapers/base.py` — `class Fetcher: discover() -> list[DocRef]; fetch(ref) -> FetchedDoc; fingerprint(html) -> str`. Shared: retries (exponential 1→60 s, 5 tries), per-host concurrency 1, 2–5 s jitter, `robots.txt`, UA with contact email, `--dry-run`.
- `scrapers/sources.yaml` schema:
  ```yaml
  - id: ceo_jh_form20
    state: JH
    kind: form20            # form20 | pslist | roll | roll_supplement | sec_result | press_note | news_rss | news_html
    applies_to_acs: [31, 32, 33, 42, 61, 65]
    discovery:
      url: "https://…"
      method: html          # html | playwright | rss
      item_selector: "table tr a[href$='.pdf']"
      filters: { text_regex: "AC\\s*-?\\s*{ac_number}\\b" }
    fingerprint:
      selector: "main"      # structural skeleton = tag path sequence of this subtree
      expected: "<sha256>"
    schedule: "0 6 * * *"
  ```
- Fingerprint = sha256 of the tag-name path sequence (no text, no attributes except `class`) of the configured subtree. Mismatch → `source.status='drifted'`, no parse, alert.
- `source_doc.parse_status` lifecycle `new → extracted → parsed → validated → loaded | failed | drifted`, each with timestamp and actor.
- Recorded fixtures per source in `tests/fixtures/scrapers/<id>/` with a parse test per fixture.
- Watchers per active AC: PS list, roll supplement, Form 20, SEC notice, CEO/DEO press note.

### B-2 Live news (spec §4)
- Crawl every 30 min; per-AC keyword sets in `sources.yaml` (AC name both scripts, block names, panchayat/ward names, sitting MLA and 2024 top-3 candidate names).
- Label hourly micro-batches (Haiku Batch API; optional key — if absent, items are stored unlabelled and keyword-tagged to ACs; the UI shows "unlabelled").
- Label JSON schema (strict, validated with pydantic): `{summary_hi, summary_en, ac_numbers[], area_names[], issues[] (enum from spec), parties[], persons[], sentiment_by_party{abbr: -2..2}, event_candidate: bool, event_kind?: enum}`.
- YouTube Data API v3 fetcher (optional key, channel list in YAML) and Telegram bot channel reader (optional token) implemented behind flags; both off by default.
- `alert_rule(id, ac_id NULL, name, match JSONB, channel, enabled)`; match supports `keywords_any`, `issues_any`, `parties_any`, `areas_any`, `event_candidate`, `source_kinds`. `notify.py` senders: Telegram, webhook, log. Job failures and watcher hits route through it.

### B-3 Extended ground-level data (spec §3, §6)
- Migration `0014_extended_data.sql` as specified.
- CSV loaders with documented headers in `docs/CSV_FORMATS.md`: `candidate_profile`, `local_office_holder`, `area_indicator` (long format: `lgd_code|area_name, indicator, period, value, unit, source`), `influencer`, `organisation`, `political_event`, `booth_attributes`.
- Optional parsers (bonus): MyNeta candidate pages, TCPD Lok Dhaba CSV, Mission Antyodaya CSV, Census 2011 PCA CSV → `area_indicator` and `demography`.
- Shared PII screen (`common/pii.py`) applied to every free-text write path; reject with a Hindi+English message naming the pattern class, never echoing the matched text.
- Role gates: `influencer`, `organisation`, `political_event` strategist+; `public_issue` contact field admin-only and encrypted (`pgcrypto`), purge 90 days after close.
- Worker/representative and public-issue *tables and read endpoints only*; no forms yet — render placeholders "coming soon" in nav, hidden for block role.

### B-4 UI (spec §7)
Build/upgrade in this order; each page must have loading, error, empty (with CLI command) states, Hindi default, `en-IN` grouping, and an AC-aware URL.
1. **Overview** — headline, 20-year trend, electors growth, JLKM share, bypoll status, data-health strip (per dataset: loaded / partial / missing, last loaded, command to fix).
2. **Booth table** — virtualised (e.g. TanStack Table + virtual), column chooser with saved presets ("Results", "Swing", "Voters", "Community", "Priority"), per-column filters, multi-sort, sticky header and first column, CSV of current view, row → booth card, source-page link, crosswalk-confidence indicator, NULL rendered as "—" with tooltip explaining why.
3. **Map** — metric + year + block + area filters; ramp signed by contest pair; size by electors; grey for NULL with legend entry; polygons when loaded; click → booth card.
4. **Booth card** — tabs: Results (all years), Voters, Community (with confidence), News (area-tagged), Ground reports, Local politics, Sources.
5. **Area rollup** — same column presets aggregated; drill to booths.
6. **Voters**, **Caste & community** (with scatter + regression + caveat), **Transfer**.
7. **Candidates** — profile cards per contest, side by side, turncoat/incumbent badges.
8. **Local politics** — office holders, influencers (strategist+), organisations, event timeline.
9. **News** — stream with filters, "suggested events" tab with one-click promote to `political_event`.
10. **Indicators** — area × indicator pivot with AC and district medians.
11. **Scenario** — per AC, contest pair picker, sliders, sensitivity band.
12. **Compare** — `mv_ac_summary` table + small multiples.
13. **Admin** — sources per AC with lifecycle and drift status, review queue, crosswalk editor (§3.3 actions), surname dictionary, alert rules CRUD, jobs, usage.

### B-5 Performance
Targets (p95, warm, on the compose stack with all six ACs seeded):
- `/acs/{ac}/booths` map GeoJSON < 50 ms (serve a pre-built blob per AC per metric set, regenerated on MV refresh, with `ETag` = refresh timestamp).
- Booth card < 30 ms (precomputed JSON in `booth_card_cache`, rebuilt on refresh).
- Booth table page (500 rows) < 100 ms.
- Scenario (500 draws) < 150 ms (baseline matrix held in process memory per AC, reloaded on refresh signal).
- MV refresh for all ACs < 60 s; all MVs refresh `CONCURRENTLY`.
Write `scripts/bench.py` that measures these and prints a table; include the output in `UAT_READINESS.md`.

### B-6 Ops (fix prompt Phase 6)
G1 off-host backup + `scripts/restore_drill.sh` (run once, record date); G3 JSON logs with request id + compose log rotation; G4 notifications via `notify.py`; A7–A11 cleanup; replace `passlib` with `bcrypt`; `web/.dockerignore`; self-hosted fonts; remove vega.

---

## 5. Tests you must add (minimum)

| Area | Test |
|---|---|
| Security | empty/placeholder JWT rejected; login rate limit; block role cannot read other block or caste; chat routes absent when disabled |
| Privacy | roll parse leaves no EPIC/phone/Aadhaar pattern on disk or in DB; PII screen rejects each pattern class on every free-text endpoint |
| Form 20 | real-page fixture → exact booth numbers; unresolved column aborts; unparseable cell → review queue; re-run idempotent |
| Metrics | each row of §3.2 against a hand-computed fixture, including every NULL rule |
| Crosswalk | auto/review/new bands write correct rows; split/merge lineage; UID sequence per AC never collides; `--anchor` refusal |
| Roll | composition checks fail on bad fixture; supplement section state carried across PS groups; per-entry deletion reason |
| Multi-AC | same PS number in two ACs never collides; every API route rejects missing/unknown AC; MVs partitioned by AC |
| Scrapers | fixture parse per source; fingerprint drift detection; content-hash skip |
| Scenario | argmax winner; alliance-based transfers; sensitivity label |
| Performance | `scripts/bench.py` thresholds as a non-blocking CI job |

---

## 6. Documentation to update

- `README.md` — multi-AC architecture, directory map, env contract, tech stack.
- `RUN.md` — reconcile every command with what you built; keep its plain-language tone.
- `docs/RUNBOOK.md` — drift handling, crosswalk review workflow, privacy incident procedure, restore drill.
- `docs/CSV_FORMATS.md`, `docs/METRICS.md` (§3.2 table verbatim, with the SQL location of each metric).
- `DECISIONS.md` — every judgement call.
- `UAT_SCRIPT.md` — human test cases (Track A-4).
- `UAT_READINESS.md` — final report (§7).

---

## 7. Definition of done and final report

Write `UAT_READINESS.md` with:
1. **Line 1:** `READY` / `READY WITH CAVEATS` / `NOT READY`, and whether Giridih ran on a real Form 20 or a fixture.
2. Every spec §8 "Must pass" item → PASS / FAIL / PARTIAL with the test or command that proves it.
3. Every audit ID → fixed (commit hash) / deferred (reason) / not reproducible (evidence).
4. Track B items → done / partial / not started.
5. `scripts/bench.py` output.
6. Seeds still `verified=false`, per AC.
7. Known risks for tomorrow's UAT and the workaround for each.
8. Exact commands for the UAT operator: fresh setup, `scripts/uat_seed.sh`, `scripts/uat_users.py`, URL.

Be blunt. Do not claim a check passes unless a test or command you ran shows it. All outputs of this system, including this report, must be reviewed by a human for accuracy and completeness before they are relied on.
