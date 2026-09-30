# Multi-Constituency Expansion Spec — Jharkhand Election Monitor v2

**Date:** 30 Sep 2026 · **Supersedes:** HLD v0.1 / LLD v1.0 where they conflict · **Status:** build spec for UAT-1

---

## 1. Scope change

From one constituency (Giridih AC-32) to six, all Jharkhand, all with the same JMM-vs-NDA-with-JLKM-factor pattern and a significant Kudmi/Mahato electorate.

| AC | No.* | District | Parliamentary seat | Blocks / ULB (confirm from PS list) | 2024 result (verify vs ECI) |
|---|---|---|---|---|---|
| Giridih | 32 | Giridih | Giridih | Giridih block, Pirtand block, Giridih Municipal Corp. | JMM 94,042 vs BJP 90,204; margin 3,838. **Seat vacant since 6 Sep 2026 → bypoll due by ~Mar 2027** |
| Gandey | 31 | Giridih | Kodarma | Gandey, Bengabad | JMM (Kalpana Soren) beat BJP (Muniya Devi) by 17,142 |
| Dumri | 33 | Giridih + Bokaro | Giridih | Dumri (Giridih); Nawadih, Chandrapura (Bokaro) | JLKM (Jairam Mahato) 94,496 beat JMM (Baby Devi) 83,551 |
| Tundi | 42 | Dhanbad | Giridih | Tundi, Purvi Tundi, Topchanchi | JMM (Mathura Pd. Mahato) won — margin to verify |
| Silli | 61 | Ranchi | Ranchi | Silli, Sonahatu, Rahe | JMM (Amit Kumar) beat AJSU (Sudesh Mahto) by 23,867; JLKM third |
| Kanke (SC) | 65 | Ranchi | Ranchi | Kanke, Burmu + Ranchi Municipal Corp. wards | INC (Suresh Kr. Baitha) 1,33,499 beat BJP (Jitu Charan Ram) 1,32,531 by **968**; JLKM 25,965; electors 4,81,815 |

\* AC numbers are from the 2008 delimitation and should be confirmed against the CEO Jharkhand PS list header before seeding.

**What this does to the design:** the system stops being "a Giridih tool with tables" and becomes "a constituency-keyed platform with Giridih as one tenant". Every table, view, API route, seed file and scraper config must be scoped by `ac_id`. There is no partial version of this — a half-scoped schema is worse than the single-AC one because it silently mixes constituencies.

---

## 2. Data model changes (mandatory before anything else)

### 2.1 New spine tables

```sql
CREATE TABLE state        (state_id SMALLINT PK, name_en TEXT, name_hi TEXT, ceo_base_url TEXT, sec_base_url TEXT);
CREATE TABLE district     (district_id SERIAL PK, state_id SMALLINT, name_en TEXT, name_hi TEXT, lgd_code INT);
CREATE TABLE pc           (pc_id SERIAL PK, state_id SMALLINT, pc_number SMALLINT, name_en TEXT, name_hi TEXT, reservation TEXT);
CREATE TABLE ac           (ac_id SERIAL PK, state_id SMALLINT, ac_number SMALLINT, name_en TEXT, name_hi TEXT,
                           reservation TEXT CHECK (reservation IN ('GEN','SC','ST')), pc_id INT REFERENCES pc,
                           is_active BOOLEAN DEFAULT true, bypoll_due DATE NULL, notes TEXT,
                           UNIQUE (state_id, ac_number));
CREATE TABLE ac_district  (ac_id INT, district_id INT, PRIMARY KEY (ac_id, district_id));   -- Dumri spans two
```

### 2.2 Scope every existing table

Add `ac_id INT NOT NULL REFERENCES ac` to: `block`, `area`, `booth`, `ps_list_entry`, `booth_crosswalk`, `election` (see below), `candidate`, `result_booth`, `result_booth_meta`, `result_ac_total`, `roll_revision`, `roll_snapshot`, `roll_change`, `caste_estimate`, `caste_survey`, `local_result`, `ground_report`, `knowledge_card`, `source_doc`, `review_queue`, `job_run` (nullable — some jobs are global), `news_item.ac_ids INT[]` (an article can concern several).

`booth_uid` becomes `'{ac_number}-B{nnnn}'` (e.g. `32-B0147`) so UIDs cannot collide across constituencies — this also closes audit finding B3 structurally.

### 2.3 Elections become two-level

An election *event* (VS 2024, LS 2024) is shared; the *contest* is per AC.

```sql
CREATE TABLE election_event (event_id SERIAL PK, type TEXT CHECK (type IN ('VS','LS','PANCHAYAT','ULB')),
                             year SMALLINT, label TEXT UNIQUE, poll_dates DATE[], count_date DATE, is_bypoll BOOLEAN DEFAULT false);
CREATE TABLE election        (election_id SERIAL PK, event_id INT REFERENCES election_event, ac_id INT REFERENCES ac,
                             phase SMALLINT, poll_date DATE, electors_published INT, votes_polled_published INT,
                             is_baseline BOOLEAN DEFAULT false, UNIQUE (event_id, ac_id));
CREATE UNIQUE INDEX one_baseline_per_ac ON election (ac_id) WHERE is_baseline;   -- closes B9
```

For LS, `election` rows are AC *segments* of the PC contest; `candidate` rows are shared per PC via `pc_candidate_id`.

### 2.4 Party / alliance by election

Alliances change (JVM merged into BJP 2020; AJSU in/out of NDA; JLKM new in 2024). Replace `party.alliance_2024` with:

```sql
CREATE TABLE party_alliance (party_id INT, event_id INT, alliance TEXT, PRIMARY KEY (party_id, event_id));
CREATE TABLE party_alias    (alias TEXT PK, party_id INT, script TEXT);   -- 'झामुमो','JMM','Jharkhand Mukti Morcha','JMM(S)'…
```

`party_alias` is what fixes audit finding C1 properly: Form 20 headers, ECI result pages, news text and TCPD exports all spell parties differently.

### 2.5 Materialized views

Every MV gains `ac_id` as a leading column in its unique index and `GROUP BY`. Add `mv_ac_summary` (one row per AC per election: winner, margin, turnout, electors, new voters, JLKM share, booth count, crosswalk coverage %) — this is the row the constituency switcher and the comparison screen read.

---

## 3. What to record beyond what we have — findings from public data projects

Probed: TCPD Lok Dhaba / TCPD-PPI (Ashoka), DataMeet india-election-data, Raphael Susewind's india-religion-politics (booth-level UP/Delhi/AP), ADR/MyNeta scrapers, SHRUG (Development Data Lab), ECI's own forms. What they record that we don't, and whether we should:

### 3.1 Candidate layer — **add** (high value, low cost)

| Field | Source | Why it matters here |
|---|---|---|
| Incumbent flag, times contested, times won, previous party (turncoat) | TCPD Lok Dhaba candidate-level 1962→ | Champai Soren, Sudesh Mahto, Jairam Mahato — party-switching and re-runs are the story in every one of these six seats |
| Deposit forfeited, ENOP (effective number of parties), vote share of #3 | TCPD | Formalises "third-front split" instead of eyeballing it |
| Declared assets, liabilities, criminal cases (pending / convicted), education, age, profession | ADR / MyNeta (scraped from affidavit.eci.gov.in) | Campaign messaging + opposition research; public record |
| Candidate `myneta_id`, `tcpd_id` cross-references | — | Lets you join external datasets without re-matching names |

```sql
CREATE TABLE candidate_profile (candidate_id INT PK, incumbent BOOLEAN, contests_prior SMALLINT, wins_prior SMALLINT,
  prev_party_id INT, turncoat BOOLEAN, deposit_forfeited BOOLEAN, assets_declared BIGINT, liabilities BIGINT,
  criminal_cases SMALLINT, criminal_serious SMALLINT, education TEXT, age SMALLINT, profession TEXT,
  affidavit_url TEXT, myneta_id TEXT, tcpd_id TEXT, source TEXT, fetched_at TIMESTAMPTZ);
```

### 3.2 Booth attributes — **add**

| Field | Source | Why |
|---|---|---|
| Booth category: urban/rural; ECI "critical"/"vulnerable" flag; auxiliary booth | CEO PS list + district election office notifications | Critical booths get force deployment and have historically different turnout behaviour |
| Building type (school / panchayat bhawan / anganwadi / private), floor, AMF (ramp, toilet, water) | PS list "Assured Minimum Facilities" columns | Accessibility correlates with elderly/women turnout |
| Poll-day recorded votes per booth (Form 17C part I) and hourly turnout | ECI Voter Turnout app / CEO releases on poll day | This is the *only* live official number on polling day — build the loader now, wire it to the turnout tracker |
| LGD codes for village / panchayat / ward | lgdirectory.gov.in | Universal key to join scheme data, Census, SHRUG |
| Susewind-style name-based **religion** estimate (aggregate) | Our own roll scan, same method as caste | Muslim share is decisive in Giridih urban wards and Gandey; our surname dict already covers it — just expose it as its own bucket with confidence |

```sql
ALTER TABLE booth ADD COLUMN category TEXT, ADD COLUMN is_critical BOOLEAN, ADD COLUMN is_auxiliary BOOLEAN,
  ADD COLUMN building_type TEXT, ADD COLUMN amf JSONB, ADD COLUMN lgd_village_code INT, ADD COLUMN lgd_panchayat_code INT;
CREATE TABLE poll_day_turnout (election_id INT, booth_uid TEXT, as_of TIMESTAMPTZ, votes_recorded INT, source TEXT,
  PRIMARY KEY (election_id, booth_uid, as_of));
```

### 3.3 Village / ward development layer — **add** (this is what "influencing factors at ground level" actually is, in data)

| Field | Source | Notes |
|---|---|---|
| Amenities: school (primary/secondary), PHC/sub-centre, all-weather road, tap water, electricity hours, bank/post office, mobile coverage | Census 2011 Village Directory + Mission Antyodaya (2019–2022 survey, village-level, free CSV) | Mission Antyodaya is newer and better than Census for this |
| Scheme saturation: PMAY-G houses, Ujjwala, Jal Jeevan tap connections, MGNREGA job cards & person-days, PM-Kisan beneficiaries, ration cards | Scheme dashboards on data.gov.in and ministry portals — village/panchayat-level CSV, scrapeable | Beneficiary *counts only*, never beneficiary lists |
| Night-time light intensity, PMGSY road completion, SECC deprivation indicators | SHRUG v2 (Development Data Lab) — village-keyed, free | Best single proxy for relative prosperity change 2011→now |
| Migration proxy: share of households with an absent working-age male | Census 2011 + Mission Antyodaya | Giridih and Dumri are heavy out-migration blocks; migrant return drives turnout |
| Mining / forest / land-acquisition footprint | Manual geo-tag per panchayat | Parasnath, coal belt (Tundi/Dumri), Kanke land-use conflicts |

```sql
CREATE TABLE area_indicator (area_id INT, indicator TEXT, period TEXT, value NUMERIC, unit TEXT, source TEXT,
  fetched_at TIMESTAMPTZ, PRIMARY KEY (area_id, indicator, period));
```

One long-format table; the UI pivots. Don't build a wide table per source.

### 3.4 Local political structure — **add**

| Field | Source |
|---|---|
| Sitting mukhiya, ward member, panchayat samiti member, zila parishad member per panchayat/ward, with informal party tag and tag source | SEC Jharkhand results + district unit |
| ULB councillors and mayor/chairperson per ward | SEC / ULB site |
| Leader / influencer registry: person, role (mukhiya, ex-MLA, religious head, contractor, union leader, teacher association…), community, area(s), alignment, influence 1–5, last contact, notes | Manual — from your block in-charges |
| Organisation registry: caste sabhas, kisan sanghs, majdoor unions, mahila mandals, youth clubs, SHG federations, with area and alignment | Manual |
| Event log: rally, padyatra, defection, protest, incident, scheme launch, court order — date, area, actors, effect (+/−) on which party | Manual + news pipeline auto-suggests |

```sql
CREATE TABLE local_office_holder (id SERIAL PK, ac_id INT, area_id INT, office TEXT, name TEXT, tagged_party_id INT,
  tag_source TEXT, term_start DATE, term_end DATE);
CREATE TABLE influencer (id SERIAL PK, ac_id INT, name TEXT, role TEXT, community_id INT, alignment_party_id INT,
  influence SMALLINT CHECK (influence BETWEEN 1 AND 5), notes TEXT, updated_by INT, updated_at TIMESTAMPTZ);
CREATE TABLE influencer_area (influencer_id INT, area_id INT, PRIMARY KEY (influencer_id, area_id));
CREATE TABLE organisation (id SERIAL PK, ac_id INT, name TEXT, kind TEXT, community_id INT, alignment_party_id INT, notes TEXT);
CREATE TABLE political_event (id SERIAL PK, ac_id INT, area_id INT NULL, booth_uid TEXT NULL, occurred_on DATE, kind TEXT,
  title TEXT, detail TEXT, actors TEXT[], effect_party_id INT, effect_sign SMALLINT, source TEXT, news_id INT NULL);
```

`influencer` holds names of public-role individuals and party contacts, not voters. Restrict to `strategist`/`admin`; never expose through any public or block-level surface.

### 3.5 What NOT to add
- Individual voter records of any kind, including "just names for matching". The aggregate-only rule holds across all six ACs.
- Scheme *beneficiary lists*. Counts only.
- Social-media follower scraping of private individuals.

---

## 4. Live news and signal layer

Current: RSS every 4 h, batch-labelled nightly. Required: closer to real time, per constituency, with alerts. Still no paid APIs.

| Source | Mechanism | Cost | Notes |
|---|---|---|---|
| Hindi dailies (Prabhat Khabar, Dainik Bhaskar, Hindustan, Jagran) district editions | RSS + HTML crawl, **every 30 min** | Free | One crawler config per AC with its own keyword set (block names, panchayat names, leader names, "गिरिडीह", "गांडेय", "डुमरी", "टुंडी", "सिल्ली", "कांके") |
| Google News RSS per AC query | `news.google.com/rss/search?q=...&hl=hi&gl=IN` | Free, unofficial | Good recall; dedupe against direct feeds |
| Local news YouTube channels (Giridih/Ranchi/Dhanbad news channels, party channels) | **YouTube Data API v3** — free 10,000 units/day | Free with Google Cloud project key | Titles + descriptions + publish time; enough to detect events. ~50 channels × 48 polls/day fits the quota |
| Telegram channels of local news portals and party units | **Telegram Bot API** (add bot to public channels) | Free | Many Jharkhand local portals push here first |
| X / Twitter handles (MLAs, DCs, SP, district party units) | Official API Basic tier | ~$100/mo | **Defer.** Nitter is dead/unreliable. Revisit when budget allows; YouTube+Telegram+RSS cover 80% |
| Official: district administration press notes, ECI/CEO press notes, SEC notifications | HTML crawl, daily | Free | Poll schedule, PS changes, roll revision notices — feed `ac.bypoll_due` and the PS-list watcher |

**Labelling** moves from nightly batch to **hourly micro-batches** (Haiku, Batch API still, but submitted hourly) so the dashboard is at most ~90 min behind. Each item gets `ac_ids[]`, `area_ids[]`, `issues[]`, `parties[]`, `persons[]`, `sentiment_by_party`, `event_candidate BOOLEAN` (does this look like a `political_event` worth logging).

**Alert rules** (new table `alert_rule`): keyword/issue/area/party match → notification to a webhook (Telegram bot message to a private group is the cheapest reliable channel). Examples: "defection", "FIR + candidate name", "Parasnath", "बूथ + विवाद", any item with `event_candidate = true` in a target AC.

---

## 5. Scraper robustness (fetch layer rewrite)

Applies to `fetch_ceo`, `fetch_sec`, `crawl_rss`, and every new fetcher. All fetchers implement one interface.

1. **Config-driven discovery.** `scrapers/sources.yaml`: per state/AC, the portal URL patterns, expected document types, CSS/XPath selectors, and a `fingerprint` (hash of the page's structural skeleton). No URLs in Python.
2. **Structure drift detection.** On every run, recompute the skeleton fingerprint; if it differs from the stored one, mark the source `drifted`, stop parsing, raise an alert. Never parse a page whose structure you did not expect.
3. **Content addressing.** Every downloaded file → `sha256`; store `(source_id, url, sha256, fetched_at, http_status, bytes)` in `source_doc`. Re-parse only on new hash. Never delete raw files — archive under `raw/{state}/{ac}/{doc_type}/{year}/`. (Roll PDFs: retain the PDF; never cache extracted page text — audit C3.)
4. **Retries and politeness.** Exponential backoff (1s→60s, 5 tries), respect `robots.txt`, one request in flight per host, randomised 2–5 s delay, identify with a real User-Agent and contact email.
5. **JS portals.** Where CEO/SEC pages need JavaScript (some do), use Playwright headless as a fallback path, recorded HAR for fixtures.
6. **Recorded fixtures.** Every scraper ships with a saved HTML/PDF fixture and a test that parses it to a known result. When a site changes, the fixture is updated deliberately, not the parser blindly.
7. **Parse status lifecycle.** `source_doc.parse_status`: `new → extracted → parsed → validated → loaded | failed | drifted`. Every transition sets a timestamp. `/admin/sources` shows it per AC.
8. **Dry run everywhere.** `--dry-run` prints what would be fetched/parsed/written and the validation result, writes nothing.
9. **Validation gates are per AC.** AC total reconciliation uses that AC's `result_ac_total` row; a failure blocks that AC's load only.
10. **Watchers.** Daily: new PS list, new roll supplement, new Form 20, new SEC notification, per AC. Each new document → alert + auto-extract + review-queue entry for a human to trigger load.

---

## 6. Future scope — schema stubs now, UI later

Build the tables and minimal endpoints now so the data model doesn't need another migration wave; build the interfaces after UAT.

### 6.1 Representative / party-worker interface
- `worker_user` extends `app_user` with `assigned_booths TEXT[]`, `assigned_areas INT[]`, `supervisor_id`.
- `ground_report` gains: `kind` (turnout_estimate | issue | sentiment | event | logistics), `structured JSONB` (e.g. `{"turnout_pct_est": 42, "queue_len": 30}`), `photo_url` (object store, optional), `gps POINT`, `submitted_offline BOOLEAN`, `client_ts`.
- `work_log`: (worker_id, date, area/booth, activity kind, count — households visited, pamphlets, voters contacted **as counts**), for supervisor dashboards.
- Mobile web form works offline (service worker + IndexedDB queue, sync on reconnect) — Pirtand and Tundi have poor coverage.
- **PII screen on every free-text field** (EPIC regex, 10-digit phone regex, Aadhaar 12-digit regex) — reject with a message.

### 6.2 Public issue log with timeline
```sql
CREATE TABLE public_issue (id SERIAL PK, ac_id INT, area_id INT, booth_uid TEXT NULL, category TEXT, title TEXT, detail TEXT,
  reported_on DATE, reporter_kind TEXT CHECK (reporter_kind IN ('worker','public','news')), status TEXT
  CHECK (status IN ('open','acknowledged','in_progress','resolved','closed','rejected')), priority SMALLINT, gps POINT NULL);
CREATE TABLE issue_event (id SERIAL PK, issue_id INT, at TIMESTAMPTZ, actor_id INT NULL, kind TEXT, note TEXT, new_status TEXT NULL);
```
Public submissions collect **no name or phone** by default — an optional contact field, encrypted at rest, visible to admin only, purged 90 days after closure. Timeline view = `issue_event` ordered by `at`; resolution SLA per category; heat map of open issues per panchayat.

---

## 7. UI — what "functional" means here

Views in priority order for UAT and after. Every view has an AC switcher in the header and a "compare ACs" mode where it makes sense.

1. **Constituency overview** (per AC): 2024 headline, 20-year trend, electors growth, JLKM share, bypoll status, data-health strip (Form 20 loaded ✔, crosswalk 94% ✔, roll rev. 2026-07 ✔, census ✖).
2. **Booth table — the workhorse.** One row per booth, every column available: PS no., name, area, block, category, electors, turnout, each party's votes and share (2024 and 2019), margin, swing, new voters since 2019, additions since last revision, caste blend top-3 with confidence, priority score, crosswalk confidence, source page link. Column chooser, sticky header, filter by any column, sort, CSV. This replaces half the "modules".
3. **Map**: booth points + panchayat/ward polygons where available; metric selector, year selector, block/area filter (audit F2), diverging ramp signed by winner (F1), marker size by electors (F3).
4. **Booth card** (drawer): everything about one booth, all years, roll, caste, news tagged to its area, ground reports, influencers in its area.
5. **Area rollup**: panchayat/ward table with the same columns aggregated; drill to booths.
6. **Voters**: additions/deletions per booth per revision, age/gender split, SIR flags.
7. **Caste & community**: aggregate share by booth/area with confidence bands; **scatter of community % vs party share** (F4) with ecological-regression line and a plain-language caveat.
8. **Transfer**: LS↔VS per booth per year; NULL, not 50%, where one leg is missing (D4).
9. **Candidates**: profile cards from §3.1 for every contest, side by side.
10. **Local politics**: office holders, influencers, organisations, event timeline, per area.
11. **News**: filterable stream per AC/area/issue/party with sentiment; "events suggested" tab for promotion to `political_event`.
12. **Development indicators**: `area_indicator` pivot per panchayat with AC and district medians for comparison.
13. **Scenario**: per AC; winner = argmax (D5); band labelled as sensitivity range (D6).
14. **Compare ACs**: `mv_ac_summary` table + small multiples (margin trend, JLKM share, turnout, new-voter %).
15. **Admin**: sources per AC with parse status, review queue, crosswalk editor, surname dictionary, alert rules, jobs, usage.
16. *(placeholder, parked)* Chat panel — hidden behind `CHAT_ENABLED=false`.

Numbers rendered in Indian grouping; Hindi default; every derived figure carries a source or confidence indicator; empty states name the CLI command that fills them.

---

## 8. UAT-1 scope (realistic for tomorrow)

**Must pass**
- Stack boots from clean checkout with one command (PostGIS image fixed).
- Six ACs seeded with 2024 and 2019 AC-level results (`result_ac_total`) and the correct block/area lists.
- Giridih: real 2024 Form 20 loaded, reconciled to ECI totals to the vote; booth table, map and booth card correct; margin 1.85% reproduces.
- Giridih: crosswalk 2019→2024 with review queue visible; no booth silently dropped (coverage check = 100% of `ps_list_entry`).
- Giridih: one roll revision loaded, additions per booth shown, no roll text on disk (filesystem EPIC scan passes).
- AC switcher works; the other five ACs show AC-level data and honest "not loaded" states at booth level.
- No chatbot code required to boot; `CHAT_ENABLED=false` hides the panel.
- Auth refuses to start with a weak/placeholder JWT secret; login rate-limited.

**Demonstrated, not judged**
- News stream for all six ACs (RSS only), alert rule → Telegram message.
- Candidate profiles for 2024 contests (manually seeded CSV is acceptable).
- Scenario for Giridih.

**Explicitly out of UAT-1**
- Form 20 for the other five ACs (load in the week after; the pipeline is proven on Giridih).
- YouTube/Telegram ingestion, development indicators, influencer registry UI, worker mobile form, public issue log.

---

*Every figure in §1 is from public secondary sources and must be verified against ECI/CEO Jharkhand before seeding. Outputs of the system should be reviewed for accuracy and completeness before decisions rely on them.*
