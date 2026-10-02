# Jharkhand Election Monitor — High-Level Design v2

**Version:** 2.0 · **Date:** 1 Oct 2026 · **Supersedes:** HLD v0.1 (Giridih-only)
Filename kept for continuity; the scope is now six constituencies.

---

## 1. Purpose

A constituency intelligence platform for Jharkhand assembly seats. It turns public election documents that exist only as PDFs on government portals into a clean, traceable database, and presents booth-level results, voter-roll changes, estimated community composition, local politics and news on one dashboard. Every number can be traced to the document and page it came from.

Immediate driver: the Giridih (AC-32) by-election, due by about 6 March 2027 after the sitting MLA's death on 6 September 2026. The 2024 margin there was 3,838 votes (1.85%).

## 2. Scope

| AC | No. | District | Parliamentary seat | Status in system |
|---|---|---|---|---|
| Giridih | 32 | Giridih | Giridih | Primary seat; booth-level mock data; AC-level figures checked against ECI |
| Gandey | 31 | Giridih | Kodarma | AC-level seeds, `verified=false` |
| Dumri | 33 | Giridih, Bokaro | Giridih | AC-level seeds, `verified=false` |
| Tundi | 42 | Dhanbad | Giridih | AC-level seeds, `verified=false` |
| Silli | 61 | Ranchi | Ranchi | AC-level seeds, `verified=false` |
| Kanke (SC) | 65 | Ranchi | Ranchi | AC-level seeds, `verified=false` |

A seat stays `verified=false` until a person has checked its figures against official ECI results.

## 3. Users

| Role | Sees |
|---|---|
| admin | Everything, including data operations (sources, review queue, crosswalk editor, jobs) |
| strategist | All analysis, including community estimates and local politics; no admin tools |
| block | Only their own constituency and block; no community estimates |

## 4. Principles (non-negotiable)

1. **No individual voter data is stored anywhere.** Rolls are parsed into per-booth counts; names, EPIC numbers, addresses and phone numbers are never written to the database, disk cache, logs, fixtures or API responses. Roll PDFs never go to remote storage.
2. **Nothing is shown unless it reconciles.** Form 20 loads are refused unless booth totals equal the ECI-published constituency totals to the vote.
3. **Missing data is shown as missing.** NULL renders as "—" with a reason, never as 0 or a default.
4. **Every metric formula exists once.** One module generates both the Python and SQL implementations, with a parity test.
5. **Estimates are labelled.** Community composition is an aggregate booth-level estimate with a confidence score, never presented as fact.
6. **Traceability.** Every loaded row records its source document and page.

## 5. Data

| Layer | Source | Notes |
|---|---|---|
| Booth-wise results | ECI Form 20 (CEO Jharkhand / district election offices) | Vidhan Sabha and Lok Sabha (AC segment) |
| Booth list and mapping | Polling-station lists | Booth → village/ward → panchayat → block |
| Voter roll | Mother rolls and supplements | Counts only: total, gender, age bands, additions, deletions |
| Local elections | State Election Commission | Panchayat and municipal results |
| Demography and development | Census 2011, Mission Antyodaya and similar | Village-level, long-format indicators |
| Candidates | Affidavits (MyNeta/ADR), TCPD | Incumbency, party history, assets, cases |
| Local politics | Manual entry | Office holders, influencers, organisations, events |
| News | Hindi dailies' RSS, Google News RSS | Tagged to constituency, area, issue, party |

No paid data APIs are required. Government sources have no APIs and are scraped politely, with structure-drift detection.

## 6. Architecture

```
Government portals, RSS, CSV ─► Ingestion (fetch → extract → parse → validate → promote)
                                        │
                                        ▼
                         PostgreSQL 16 + pgvector (PostGIS optional)
                         tables · materialized views · generated metric functions
                                        │
                                        ▼
                               FastAPI (JWT, role-scoped)
                                        │
                                        ▼
                    React dashboard (Hindi/English, Leaflet map, charts)
```

- **Constituency-keyed throughout.** Every table, view and route is scoped by constituency; booth IDs carry the AC number (e.g. `32-B0147`).
- **Booth identity across years** is maintained by a crosswalk with confidence scores and split/merge lineage; unreviewed weak matches are excluded from swing figures.
- **Contest pair per seat.** Margins and scenarios compare the two parties that actually contest each seat (e.g. JMM–BJP in Giridih, JLKM–JMM in Dumri, INC–BJP in Kanke), configured per election.
- **Worker optional.** Scheduled jobs (news crawl, portal watchers, view refresh, backups) run in a worker; the API serves everything from the database without it. With no worker, view refresh is a manual step after each load.

## 7. Dashboard

Overview (headline, trends, priority booths, swings, new-voter hotspots, community snapshot, latest news) · Booth table · Booth map · Booth card · Area rollups · New voters · Community composition and community × vote · LS vs VS transfer · Candidates · Local politics · News · Indicators · Scenarios · Compare constituencies · Admin (data sources, review queue, crosswalk editor, verification).

## 8. Deployment

| Environment | Setup |
|---|---|
| Local development | `scripts/dev_stack.py` (embedded PostgreSQL, no Docker) or Docker Compose |
| Demo / hosted | Frontend on Vercel; API on Railway; Postgres and file storage on Supabase |

Map tiles default to OpenStreetMap, configurable via `VITE_TILE_URL`.

## 9. Parked and future

- **Chatbot:** code present, disabled (`CHAT_ENABLED=false`); the app runs without its dependencies.
- **Future:** party-worker interface for ground reports, public issue log with timelines (tables exist; no forms yet), YouTube/Telegram news sources.

## 10. Key risks

| Risk | Mitigation |
|---|---|
| Form 20 parsing errors | Reconciliation gate; review queue with page references |
| Booth renumbering across years | Crosswalk with confidence; human review; re-anchor guard |
| Community estimates mistaken for fact | Aggregate only; confidence bands; greyed when weak |
| Portal layout changes | Fingerprint drift detection; raw documents archived |
| Seed figures for five seats unverified | `verified=false` badges until checked |

---

All figures and derived estimates must be reviewed by a person against their sources before decisions rely on them.
