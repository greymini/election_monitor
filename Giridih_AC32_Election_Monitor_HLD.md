# Giridih Assembly (AC-32) Election Monitor — High-Level Design

**Version:** 0.1 (draft for review) · **Date:** 20 Sep 2026 · **Scope:** Giridih Vidhan Sabha constituency, Jharkhand

---

## 1. Context and why this matters now

| Item | Detail |
|---|---|
| Constituency | AC-32 Giridih (General seat), Giridih district |
| Parent Lok Sabha seat | PC-11 Giridih (segments: Giridih, Dumri, Gomia, Bermo, Tundi, Baghmara) |
| Sitting MLA | Sudivya Kumar "Sonu" (JMM), elected 2019 and 2024 — **died 6 Sep 2026** |
| Status | Seat vacant → **by-election due (ECI must hold it within 6 months, i.e. by ~early March 2027)** |

### 1.1 Baseline results (verified from public records; re-verify against ECI Form 20 before use)

**Vidhan Sabha — Giridih AC**

| Year | Winner | Runner-up | Margin | Electors | Turnout |
|---|---|---|---|---|---|
| 2005 | Munna Lal (JMM) | — | — | — | — |
| 2009 | Nirbhay Kr. Shahabadi (JVM-P) | — | — | — | — |
| 2014 | Nirbhay Kr. Shahabadi (BJP) 57,450 / 38.3% | Sudivya Kumar (JMM) 47,517 / 31.7% | 9,933 | — | — |
| 2019 | Sudivya Kumar (JMM) 80,871 / 48.2% | Shahabadi (BJP) 64,987 / 38.7% | 15,884 | 2,64,814 | 63.4% |
| 2024 | Sudivya Kumar (JMM) 94,042 / 45.3% | Shahabadi (BJP) 90,204 / 43.4% | **3,838 (1.85%)** | 3,04,898 | 68.1% |

2024 others: JLKM (Navin Anand) 10,787 / 5.2%; NOTA 2,004. Electors grew **+15%** between 2019 and 2024 — new-voter analysis is therefore central.

**Lok Sabha 2024 — Giridih PC:** AJSU (C.P. Choudhary) 4,51,139 / 35.7% · JMM (Mathura Mahato) 3,70,259 / 29.3% · JLKM (Jairam Mahato) 3,47,322 / 27.5%. **AJSU led in the Giridih AC segment in LS 2024, yet JMM held the seat in VS 2024 six months later** — this LS↔VS split is the single most important dynamic to model.

### 1.2 Key analytical questions the system must answer
1. Where did the 2024 margin of 3,838 come from — which booths/wards/panchayats swung, and by how much?
2. Who are the ~40,000 net new electors added 2019→2024 (and additions since), booth by booth?
3. What is the estimated caste/community composition of each booth, and how does it correlate with vote share?
4. How did the JLKM/Jairam Mahato vote (27% in LS, 5% in VS) behave, and where does it go in a bypoll?
5. What local issues, incidents and personalities are shaping sentiment right now (news + ground reports)?

---

## 2. Users and use cases

| User | Needs |
|---|---|
| Campaign strategist / candidate team | Booth prioritisation, swing analysis, caste × vote overlays, scenario simulation |
| Block / mandal in-charges | Their own panchayat/ward view, booth lists, new voter lists, turnout targets |
| Analyst / researcher | Longitudinal trends 2005→2026, LS vs VS comparison, export |
| Anyone on the team | Ask questions in Hindi/English to the domain chatbot ("पिरटांड के किस बूथ पर 2024 में सबसे ज़्यादा स्विंग हुआ?") |

---

## 3. Geographic model (the backbone)

```
AC-32 Giridih
├── Giridih Municipal Corporation ─── 36 wards ─── booths (PS)
├── Giridih Block (rural) ─────────── 15 panchayats ─── villages ─── booths (PS)
└── Pirtand Block ─────────────────── 17 panchayats ─── villages ─── booths (PS)
```

**Design rule:** *Booth (Polling Station) is the atomic unit.* Everything — results, roll counts, new voters, caste estimates, news tags — attaches to a booth, then rolls up to panchayat/ward → block → AC.

**Hard problem — booth crosswalk across years.** Polling stations are re-numbered and split/merged in every revision (rationalisation). A `booth_crosswalk` table (PS number by year → stable `booth_uid`, matched on PS building name + village/ward + roll part) is mandatory before any multi-year comparison. Expect 10–20% of booths to need manual matching.

---

## 4. Data sources and honest availability assessment

| Layer | Source | Format | Coverage | Notes |
|---|---|---|---|---|
| VS booth-wise results | CEO Jharkhand — **Form 20** | PDF (scanned/text) | 2009, 2014, 2019, 2024 | 2005 Form 20 may not be online; AC-level only from ECI statistical reports |
| LS booth-wise results | CEO Jharkhand — Form 20 (AC segment) | PDF | 2009, 2014, 2019, 2024 | Segment-wise for PC-11 Giridih |
| Electoral rolls | CEO Jharkhand / ECI — mother roll + **supplementary lists** (additions, deletions, modifications) per PS | PDF, Hindi (Devanagari) + English | Current + prior revisions | Supplement "Additions" list = **new enrolled voters booth-wise** ✔ |
| Polling station list | CEO Jharkhand — PS list with building, village/ward, roll part | PDF/XLS | Every revision | Gives booth → panchayat/ward mapping |
| Panchayat elections | State Election Commission Jharkhand | PDF/portal | 2010, 2015, 2022 | Party-less elections; candidate affiliation must be tagged manually |
| GMC ward elections | SEC Jharkhand | PDF | 2018 (+ any later ULB poll — verify) | Ward-level results with party symbols |
| Demography | Census 2011 village/ward PCA (SC/ST, literacy, workers, households) | CSV | Village/ward | Census 2027 data not yet available; use 2011 + roll growth as proxy |
| Caste composition | **Not in any official dataset** — see §5 | — | — | Derived, not sourced |
| News | Google News RSS / site RSS: Prabhat Khabar, Dainik Bhaskar (Giridih), Hindustan, Dahad India, local portals, X/Twitter handles | HTML/RSS | Rolling | Mostly Hindi; needs Hindi NLP |
| Ground intelligence | Field forms from booth in-charges / BLAs | App form | Rolling | Highest signal, lowest volume |

**Roll-revision watch:** confirm whether the bypoll roll will be post-**Special Intensive Revision (SIR)**. If yes, *deletions* by booth matter as much as additions and should be a first-class metric.

---

## 5. Caste data — approach, limits and guardrails

Electoral rolls do **not** record caste. Anyone claiming a "caste-wise voter list" is deriving it. The system will:

1. **Estimate at booth level only** using three inputs, each with a confidence score:
   - Surname-based inference (Devanagari surname → community lookup, Giridih-specific dictionary: Mahato/Kurmi, Yadav, Verma/Barnwal (Baniya), Pandey/Mishra/Tiwari, Ansari/Muslim names, Turi/Dusadh/Rajwar (SC), Santhal/Munda names (ST), etc.)
   - Census 2011 SC/ST village proportions
   - Booth in-charge ground survey (samikaran sheet) — overrides inference when available
2. **Store aggregates only** (`booth_uid, community, est_count, confidence, source`). **No individual voter is tagged with caste in the database.** This keeps the system on the right side of the DPDP Act 2023 and ECI conditions on roll usage, and avoids creating a sensitive personal-data asset.
3. Show confidence bands on every caste chart; never present inferred numbers as fact.

Communities of relevance in Giridih (to be confirmed by ground team): Kurmi/Mahato, Yadav, Baniya (Barnwal/Sahu), upper castes (Brahmin/Rajput/Bhumihar), Muslims (large urban presence), SC (Turi, Dusadh, Rajwar, Bhuiyan), ST (Santhal — significant in Pirtand/Parasnath belt).

---

## 6. System architecture

```
┌────────────────────────────────────────────────────────────────────┐
│ 1. INGESTION                                                       │
│  PDF fetchers (CEO/SEC) → OCR (Tesseract hin+eng / Google Vision)  │
│  → parsers (Form 20, roll supplements, PS list) → validation queue │
│  News crawler (RSS/X) → Hindi NER + sentiment + geo-tag to ward/   │
│  panchayat → dedupe                                                 │
│  Field app (mobile form) → ground reports                          │
├────────────────────────────────────────────────────────────────────┤
│ 2. STORAGE                                                         │
│  PostgreSQL + PostGIS (structured: booths, results, roll counts,   │
│  demography, caste estimates, news tags)                           │
│  Object store (raw PDFs, OCR text — audit trail)                   │
│  Vector store (pgvector) — news articles, ground notes, docs       │
├────────────────────────────────────────────────────────────────────┤
│ 3. ANALYTICS ENGINE (Python)                                       │
│  Swing / turnout / margin per booth · LS↔VS transfer matrix ·      │
│  new-voter share · caste × vote regression (ecological, aggregate) │
│  · booth priority score · bypoll scenario simulator                 │
├────────────────────────────────────────────────────────────────────┤
│ 4. API (FastAPI)  —  auth (role-based: admin / strategist / block) │
├──────────────────────────────┬─────────────────────────────────────┤
│ 5a. DASHBOARD (React +       │ 5b. DOMAIN CHATBOT                  │
│  Mapbox/Leaflet + Recharts)  │  LLM (Claude) + tool use:           │
│  Hindi/English toggle        │   • text-to-SQL over analytics DB   │
│                              │   • RAG over news + ground notes    │
│                              │   • chart generation                │
│                              │  Grounded, cites booth/source IDs   │
└──────────────────────────────┴─────────────────────────────────────┘
```

**Deployment:** single VPS/cloud VM (Docker Compose) is enough — data volume is small (~350 booths, ~3 lakh roll rows, ~20 election-years). Nightly news crawl; roll/result loads are one-off batch jobs.

---

## 7. Dashboard modules

| # | Module | What it shows | Filters |
|---|---|---|---|
| 1 | **Constituency Overview** | Headline results 2005–2024, margin trend, electors & turnout trend, bypoll countdown | Election type (VS/LS/Panchayat/Ward) |
| 2 | **Booth Map & Explorer** | Choropleth of ~350 booths: winner, margin %, swing, turnout, new-voter %. Click → booth card | Block, panchayat/ward, metric, year |
| 3 | **2024 Booth-wise Results** *(key focus)* | Full Form 20 table for VS 2024 + LS 2024 side by side; sortable; export CSV | Block, panchayat/ward, party |
| 4 | **New Voters** *(key focus)* | Additions (and deletions) per booth per revision; share of electorate; age-band split (18–19, 20–29…) from roll; gender ratio | Revision date, block |
| 5 | **Caste Composition** *(key focus)* | Estimated community % per booth/panchayat/ward with confidence; overlay against 2024 vote share (scatter: e.g., Kurmi % vs JLKM+AJSU %) | Community, confidence threshold |
| 6 | **LS ↔ VS Transfer** | Where AJSU/JLKM LS-2024 votes went in VS-2024, booth by booth; "floating vote" heat map | Booth cluster |
| 7 | **Local Elections** | Panchayat 2010/15/22 mukhiya & zila parishad winners with tagged affiliation; GMC 2018 ward results; overlap with VS booth performance | Block, ward |
| 8 | **News & Sentiment** | Timeline of Giridih-tagged news; issue clusters (water, roads, Parasnath/Marang Buru, coal/mining, employment, crime); sentiment by party; ward/panchayat tag | Date, issue, source |
| 9 | **Influencing Factors** | Curated knowledge cards: alliance history (JMM–INC–RJD vs BJP–AJSU), JLKM/Jairam Mahato rise, incumbency/sympathy factor after MLA's death, candidate profiles, local strongmen, migration patterns | — |
| 10 | **Booth Priority & Scenarios** | Priority score (margin × new voters × swing volatility); scenario sliders (turnout ±, JLKM transfer %, sympathy swing) → projected margin | Block |
| 11 | **Chatbot panel** | Persistent side panel; every answer links to the underlying table/chart | — |

---

## 8. Domain chatbot design

- **Grounding first:** the model may only answer from (a) SQL results over the analytics DB, (b) retrieved news/ground documents, (c) the curated knowledge cards. It must say "data not available" otherwise.
- **Tools:** `run_sql(query)` (read-only, row-limited, schema-scoped), `search_news(query, date_range, area)`, `get_booth_card(booth_uid)`, `make_chart(spec)`.
- **Language:** Hindi, English and Hinglish input; answers in the user's language; Devanagari place-name normalisation (गिरिडीह/Giridih, पीरटांड/Pirtand).
- **Every answer cites** booth IDs, election year, source document (Form 20 page / roll supplement / article URL).
- **Guardrails:** refuses individual-voter lookups and individual caste attribution; caveats all caste estimates; no fabricated numbers (temperature 0, SQL-verified).
- **Example questions it must handle:**
  - "2024 में BJP ने किन 20 बूथों पर सबसे ज़्यादा लीड ली?"
  - "Pirtand block में 2019 से 2024 के बीच कितने नए वोटर जुड़े, पंचायत-वार?"
  - "Which wards in GMC voted AJSU in LS 2024 but JMM in VS 2024?"
  - "What were the top three local issues in the news last month?"
  - "If 60% of JLKM's 2024 votes shift to BJP in the bypoll, what's the projected margin?"

---

## 9. Core data model (simplified)

```
booth            (booth_uid PK, ps_name, building, village_or_ward, panchayat_or_ward_id, block, lat, lon)
booth_crosswalk  (election_id, ps_number, booth_uid, match_method, confidence)
election         (election_id PK, type[VS|LS|PANCHAYAT|WARD], year, date, phase)
candidate        (candidate_id PK, election_id, name, party, alliance)
result_booth     (election_id, ps_number, candidate_id, votes, total_valid, nota, electors, turnout_pct)
roll_snapshot    (booth_uid, revision_date, electors, male, female, other, age_18_19, age_20_29, ...)
roll_change      (booth_uid, revision_date, additions, deletions, modifications, add_18_19, add_female, ...)
demography       (village_or_ward_id, census_year, population, sc, st, literacy, main_workers, ...)
caste_estimate   (booth_uid, community, est_count, est_pct, confidence, source, updated_at)  -- AGGREGATE ONLY
local_result     (election_id, seat_type[mukhiya|ZP|ward], area_id, winner, tagged_party, votes, margin)
news_item        (news_id, date, source, url, title_hi, summary, issues[], sentiment, parties[], area_ids[], embedding)
ground_report    (report_id, date, booth_uid, reporter, text, issues[], embedding)
knowledge_card   (card_id, topic, body_hi, body_en, sources[], last_reviewed)
```

---

## 10. Analytics definitions

| Metric | Definition |
|---|---|
| Swing (booth) | Δ vote-share % of party between two elections (after crosswalk) |
| Volatility | Std-dev of winner-margin % across 2014/2019/2024 per booth |
| New-voter share | additions since last GE ÷ current electors |
| LS→VS transfer | For each booth, (AJSU_LS − BJP_VS), (JLKM_LS − JLKM_VS), etc.; cluster booths by pattern |
| Caste–vote association | Ecological regression at booth level: party share ~ community %s (report as correlation, **not** individual behaviour) |
| Booth priority | Weighted score of small margin, high new-voter share, high volatility, high floating vote |
| Scenario projection | Baseline 2024 booth votes × (turnout multiplier) × (transfer assumptions) → summed margin, with range |

---

## 11. Phased delivery (aligned to bypoll timeline)

| Phase | Weeks | Deliverables |
|---|---|---|
| **0. Data acquisition** | 1–2 | Download all Form 20 (VS/LS 2009–24), current roll + supplements, PS list, Census 2011 extracts, SEC results. Build booth crosswalk. |
| **1. Core dashboard** | 3–5 | Modules 1–4 (overview, map, 2024 booth results, new voters). Chatbot v1 with text-to-SQL. |
| **2. Demography & caste** | 5–7 | Module 5; surname dictionary + ground survey form; confidence scoring. |
| **3. Local elections & LS↔VS** | 7–9 | Modules 6–7. |
| **4. News & factors** | 8–10 | Modules 8–9; Hindi crawler; knowledge cards. |
| **5. Scenarios & hardening** | 10–12 | Module 10; role-based access; exports; field-app integration. |

Bypoll is likely between Nov 2026 and Mar 2027 — phases 0–2 should be complete before the ECI announcement.

---

## 12. Risks and limitations

- **OCR quality** on scanned Form 20 and Devanagari rolls — plan for a manual validation queue (target ≥99% numeric accuracy on results).
- **Booth crosswalk errors** silently corrupt multi-year swing — keep match confidence visible.
- **Caste estimates are estimates** — legal and reputational exposure if presented as facts or built at individual level. Aggregate-only design is non-negotiable.
- **Panchayat elections are party-less** — affiliation tagging is subjective; record source of tag.
- **News bias & volume** — small local portals dominate; sentiment model will need Hindi fine-tuning or LLM-based labelling.
- **Data usage compliance** — electoral rolls are published for electoral purposes; do not redistribute roll extracts; restrict access by role; log queries.
- **Sympathy factor** in a bypoll after an MLA's death is real but hard to quantify — model as a scenario input, not a prediction.

---

## 13. Open questions for you

1. Who is the end user — a party/candidate team, a media house, or independent research? (Affects access control and what "priority booth" means.)
2. Do you already have booth-level ground contacts in all 32 panchayats + 36 wards for the caste survey and ground reports?
3. Do you have the 2024 Form 20 PDFs and the latest roll supplements already, or should Phase 0 include acquisition?
4. Hindi-first or English-first UI?
5. Hosting preference (own server vs cloud) and budget for OCR (Google Vision is paid; Tesseract is free but weaker on Devanagari).

---

*Figures in §1.1 are from public secondary sources and must be re-verified against ECI/CEO Jharkhand Form 20 before use in analysis. All outputs of this system should be reviewed for accuracy and completeness before decisions are taken on them.*
