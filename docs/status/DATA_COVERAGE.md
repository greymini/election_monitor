# Data coverage: what is real, what is secondary, what is missing

Giridih (AC 32), the default build (`python scripts/dev_stack.py`, real mode), 2 October 2026.
The synthetic build (`--synthetic`) fills the gaps below with generated data; this file is about
the real build only.

Legend: **Real** = from a primary source listed below · **Secondary** = a real number from a
secondary document, not yet checked against ECI · **Missing** = shown as "—" or empty.

## 1. Sources we have

| # | Source | What it holds | Where it is | Loaded by |
|---|---|---|---|---|
| S1 | **ECI Form 20, Giridih VS-2024** (booth-wise result), tables extracted from the published PDF | 367 polling stations (PS 1–367); votes for each of 14 candidates; valid, rejected, NOTA, total, tendered; footer rows Total EVM Votes, Total Postal Ballot Votes, Total Votes Polled | `backend/db/seed/form20/giridih_vs2024_form20.xlsx` (SHA-256 `6c836117…17125fe`). The same file, byte for byte, is `data_giridih/giridih_extracted_tables_2024.xlsx` on `restructure-and-tests` | `ingest.load_form20_tables --create-booths` |
| S2 | **ECI Form 20, Giridih VS-2019**, same extraction | 367 polling stations; 12 candidates; same columns and footers | `backend/db/seed/form20/giridih_vs2019_form20.xlsx` (SHA-256 `744ce366…0b1e0da`) = `data_giridih/giridih_extracted_tables_2019.xlsx` | `ingest.load_form20_tables` (mapped to 2024 stations by PS number) |
| S3 | **News**: Google News RSS queries and state news feeds | Headlines and summaries, last 30 days, Jharkhand politics plus constituency places | `backend/db/seed/news_sources.csv` (26 sources) | `news.crawl_rss`, then `news.label_rules` |
| S4 | HLD 1.1 (project design document) | Electors 2019/2024, 2014 result, party tags of the top candidates, contest pairs | `backend/db/seed/ac_totals.csv`, `ac_contest.csv`, `form20/candidate_parties.csv` | `db/seed/load_seed.py` |
| S5 | DataMeet AC outlines, geoBoundaries block outlines | Map boundaries (marked `verified: false`) | `backend/db/seed/geo/boundaries.json` | seed step `boundaries` |

Every check in `ingest/form20_tables.problems()` passes on S1 and S2:
- each row adds up;
- each column sums to the EVM footer;
- EVM plus postal equals "Total Votes Polled".

| Result | VS-2019 (S2) | VS-2024 (S1) |
|---|---|---|
| Winner | Sudivya Kumar (JMM) 80,871 | Sudivya Kumar (JMM) 94,042 |
| Runner-up | Nirbhay Kumar Shahabadi (BJP) 64,987 | Nirbhay Kumar Shahabadi (BJP) 90,204 |
| Margin | 15,884 | 3,838 (1.85%) |
| Votes polled (incl. postal) | 1,67,893 | 2,07,821 |
| NOTA | 2,773 | 2,004 |
| Postal ballots | 342 | 2,044 |

**What S1 and S2 do not contain:**
- station names and buildings;
- villages, panchayats, wards or blocks;
- locations;
- electors;
- party affiliations.

## 2. By page

| Page / section | Real (source) | Secondary (S4) | Missing |
|---|---|---|---|
| **Overview**: winner, margin, votes polled, NOTA, postal for 2019 and 2024 | S1, S2 | | |
| Overview: electors, turnout | Votes side: S1, S2 | Electors 3,04,898 (2024), 2,64,814 (2019) | |
| Overview: 2014 and older results | | HLD 1.1 | |
| Overview: by-poll countdown | `ac.csv` (vacancy 6 Sep 2026, verified) | | |
| **Results / Booths**: per station votes per candidate, NOTA, rejected, tendered, valid, winner, runner-up, margin | S1, S2 | | |
| Booths: swing 2019 → 2024, margin volatility | S1 + S2 (2019 linked by PS number, confidence 0.95, **not reviewed**) | | |
| Booths: station name, building, locality, area, block | | | All 367 sit in the placeholder block "PS list not loaded" |
| Booths: electors, turnout, new voters, additions | | | No roll |
| Booths: floating vote | | | No Lok Sabha booth results |
| **Booth drawer**: results per election, source file and page | S1, S2 | | |
| Booth drawer: roll, new voters, caste | | | No roll |
| **Map**: margin, signed margin, volatility, swing metrics | S1, S2 (the signed-margin party pair is S4) | | |
| Map: booth markers | | | **No locations, so no markers** |
| Map: turnout, electors, new-voter metrics | | | No roll |
| Map: priority score | Closeness and volatility (S1, S2) | | New-voter and floating terms |
| Map: boundaries | | S5 (unverified) | Ward and panchayat polygons |
| **Candidates**: names and vote totals 2019/2024 | S1, S2 | | |
| Candidates: party | JMM, BJP, JLKM | (tags from HLD 1.1) | 10 candidates in 2019 and 11 in 2024 show "party not in source" |
| Candidates: age, education, assets, cases | | | No affidavit data |
| **Scenario**: baseline booth votes | S1 | Contest pair, alliances | New-voter term |
| **Compare** (six ACs) | AC 32 winner and margin (S1, S2) | AC 32 turnout | ACs 31, 33, 42, 61, 65: no Form 20 |
| **Transfer** (LS vs VS) | VS side (S1) | | LS-2024 booth results |
| **Voters** | | | No roll |
| **Caste**, **Caste scatter** | | | No roll, so no estimate |
| **Local polls** | | | No SEC results |
| **Local politics** | | | No office holders, events or organisations |
| **Factors** (knowledge cards) | Cards 01 and 07: votes from S1, S2 | Electors and the other cards cite HLD/LLD | |
| **News** | S3, keyword-labelled (parties, issues, candidates, relevance) | | Tone (sentiment) is not analysed; panchayat tagging waits for real panchayat names |

## 3. What is missing, what it unlocks, where to get it

| Missing data | Unlocks | Source to get it | Loader |
|---|---|---|---|
| **Polling-station list 2024** (and 2019) | Station names and buildings, real blocks, wards and panchayats, map markers (after geocoding), block-scoped users, review of the 2019 → 2024 mapping | CEO Jharkhand / DEO Giridih PS list PDFs | `ingest.parse_pslist`, then `ingest.geocode` |
| **Electoral roll** (mother roll and supplements) | Booth electors and turnout, new voters, Voters page, caste estimates, priority and Scenario new-voter terms | CEO Jharkhand roll PDFs (needs Tesseract `hin`+`eng` and poppler) | `ingest.parse_roll --link-election` |
| **Electors 2019/2024 from ECI** | Turns AC turnout from secondary to real | ECI statistical reports | `ac_totals.csv` |
| **Party of every candidate** | Removes "party not in source" | ECI candidate list / Form 7A, MyNeta affidavits | `form20/candidate_parties.csv`, then `scripts/build_form20_seeds.py` |
| **LS-2024 Form 20, Giridih segment** | Transfer page, floating vote, priority floating term | ECI Form 20 for Giridih PC | `ingest.ls_segment` / `parse_form20` |
| **Gram panchayats (LGD)** | Panchayat filter, news tagged to panchayats | LGD (ramSeraph mirror); news plan task 4 | `db/seed/areas_panchayats.csv` |
| **SEC panchayat / ULB 2022 results** | Local polls | jharkhandsec.gov.in, transcribed to CSV | `ingest.fetch_sec --load-csv` |
| **Census 2011 village data** | Census blend in community estimates | Census 2011 PCA + Village Directory | `ingest.load_csv demography` |
| **Candidate affidavits, local office holders** | Candidate profiles, Local politics | MyNeta/ADR, TCPD | `ingest.load_csv candidate_profile` / `local_office_holder` |
| **Form 20 for ACs 31, 33, 42, 61, 65** | Compare page, every page for those ACs | ECI Form 20 PDFs | `ingest.load_form20_tables` |
| Original Form 20 PDF pages | A hand check that the xlsx extraction matches print | ECI | — |

The 2024 polling-station list unlocks the most: places, the map and block filters all depend on it.
