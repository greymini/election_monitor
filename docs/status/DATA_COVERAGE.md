# Data coverage: real, modelled, missing

Giridih (AC 32) first, then the other five constituencies. State as of 7 October 2026, real build
(`python scripts/dev_stack.py`, or `scripts/load_supabase.py` for Supabase).

**Real** = from a published primary source below. **Modelled** = a deterministic estimate fitted to
published totals (`source_doc.method = 'modelled'`, shown as such in the UI). **Missing** = shown as "—".

## 1. Sources

| # | Source | What it holds | In the repo | Loaded by |
|---|---|---|---|---|
| S1 | ECI Form 20, Giridih VS-2024 and VS-2019 | Booth-wise votes, 367 stations each | `backend/db/seed/form20/giridih_vs20{19,24}_form20.xlsx` | `ingest.load_form20_tables --all-seed` |
| S2 | CEO Jharkhand Form 20, LS-2024, every segment (`ceo.jharkhand.gov.in/AllForm20LS2024/<ac>.pdf`) | Booth-wise LS-2024 votes for ACs 31, 32, 33, 42, 61, 65 | `*_ls2024_form20.xlsx` (via `scripts/form20_pdf_to_xlsx.py`) | same |
| S3 | CEO Jharkhand Form 20, LS-2019 Giridih PC (`ceojh.jharkhand.gov.in/Lokshabha19/Form20_GELS19/06-Giridih.pdf`) | Booth-wise LS-2019 votes, AC-32 segment | `giridih_ls2019_form20.xlsx` (`--pc-ac 32`, names in `giridih_ls2019_candidates.csv`) | same |
| S4 | CEO Jharkhand Form 20, VS-2024 (`ceo.jharkhand.gov.in/Form20_GLVS2024/Form20/<ac>.pdf`) | Booth-wise VS-2024 votes for Gandey, Dumri, Tundi, Silli, Kanke | `{gandey,dumri,tundi,silli,kanke}_vs2024_form20.xlsx` | same |
| S5 | IndiaVotes (from ECI) | Party of all 233 candidates in S1–S4 | `form20/candidate_parties.csv` | `scripts/build_form20_seeds.py` |
| S6 | CEO Jharkhand polling-station register (`ceojh.jharkhand.gov.in/PSdetails`) | All 367 AC-32 stations, 2024 numbering, English and Hindi names | `ps_list/giridih_ps2024.csv` (+ village, panchayat, ward, point) | `scripts/build_ps_list_2024.py`, `ingest.load_ps_list` |
| S7 | CEO Jharkhand roll figures: VS-2024 elector count (2nd phase), LS-2024 electorate (phase VI), VS-2019 statistical report, Form 20 headers | AC electors, men, women, third gender, 18–19 | `ac_totals.csv` (`electors`), `ingest/modelled_overlay.py` `ROLLS` | seed, overlay |
| S8 | Census 2011 PCA (censusindia.gov.in NADA 41027, 6540, 11341) | Village population, SC, ST, literates, households, sex ratio; block and town religion | `db/seed/census/` | `scripts/build_demography.py`, `ingest.load_csv demography` |
| S9 | Lokniti-CSDS post-poll 2024, Axis My India exit poll 2024 | Vote share by community, Jharkhand VS-2024 | `db/seed/modelled/vote_by_community.csv` | overlay |
| S10 | MyNeta (ADR affidavits) | Age, education, assets, liabilities, cases: top 3 candidates of VS-2019 and VS-2024 | `db/seed/candidates/candidate_profiles_ac32.csv` | `ingest.load_csv candidate_profile` |
| S11 | LGD (01 Oct 2026), india-geodata (CC0), OpenStreetMap (ODbL) | Panchayats, villages, polygons, 36 ward polygons, town localities | `areas_panchayats.csv`, `area_aliases.csv`, `geo/areas.json`; raw sources in `backend/raw/` | `scripts/build_panchayats.py`, seed |
| S12 | SEC Jharkhand panchayat gazette 2022, grampanchayat.jharkhand.gov.in | Panchayat election results, office holders | `data_giridih/` | `ingest.fetch_sec`, `ingest.load_office_holders` |
| S13 | Google News / state RSS | News, last 30 days | `news_sources.csv` | `news.crawl_rss`, `news.label_rules` |
| S14 | CEO Jharkhand AC/PC list | PC of each AC: Giridih 6 (ACs 32, 33, 42), Kodarma 5 (31), Ranchi 8 (61, 65) | `ac.csv`, migration 0030 | seed |

Every Form 20 passes `ingest/form20_tables.problems()`: each row adds up, columns equal the EVM footer,
EVM + postal = votes polled. Kanke's and Dumri's margins from S4 equal the spec's (968, 10,945).

## 2. Giridih (AC 32), page by page

| Page / section | Real | Modelled | Missing |
|---|---|---|---|
| Overview: winner, margin, votes, NOTA, postal (VS-2019, VS-2024, LS-2019, LS-2024) | S1–S3 | | |
| Overview: electors, turnout | Electors S7 (305,172 VS-2024 incl. service) | | |
| Booths: name, building (English and Hindi) | S6 | | |
| Booths: block, panchayat or ward | S6 + S11 (matched; `locate_method` per booth) | | |
| Booths: votes per candidate, swing, volatility | S1–S3 | | 2019→2024 link is by station number (r = 0.96), not reviewed |
| Booths: electors, turnout, new voters, gender, 18–19 | AC totals S7 | Each booth's share (see §4) | Ages 30–39 / 40–49 / 50–59 (ECI publishes 30–59 as one band) |
| Map: booth markers | | Points at village / ward level (§4) | Exact buildings |
| Map: panchayat and ward polygons | S11 | | |
| Candidates: party | S5, all candidates | | |
| Candidates: age, education, assets, cases | S10 (top 3, 2019 and 2024) | | Other candidates |
| Transfer (LS vs VS), floating vote | S1, S2 | | |
| Voters | AC totals S7 | Booth roll (§4) | |
| Caste, caste scatter | Census SC/ST/religion S8 | Community mix (§4) | Any caste count |
| Demography (panchayat) | S8, 34 of 35 panchayats | | Wards (2011 wards do not map to today's 36) |
| Local polls, local politics | S12 | | Party tags of office holders |
| News | S13 | | Tone; Hindi panchayat names |

## 3. The other five ACs

| AC | Real now | Missing |
|---|---|---|
| 31 Gandey | VS-2024, LS-2024 booth results (S4, S2); parties; electors; 12 Giridih-block panchayats (D-013) | PS list, roll, community; VS-2019 |
| 33 Dumri | VS-2024, LS-2024 booth results; parties; electors | same |
| 42 Tundi | VS-2024, LS-2024 booth results; parties; electors | same |
| 61 Silli | VS-2024 booth results; parties; electors | LS-2024 not linked: a station was added between the polls (278 vs 279), so numbers do not line up (r = 0.886); needs Silli's PS list |
| 65 Kanke | VS-2024, LS-2024 booth results; parties; electors | same as Gandey |

## 4. How the modelled layers are made (`ingest/modelled_overlay.py`, D-013)

**Booth places** (`scripts/build_ps_list_2024.py`; real names, derived places). Each station's
locality is matched to an LGD village (strict spelling rule, then context from neighbouring stations
for shared names). 156 rural stations sit in their own village's polygon (confidence 0.5–0.6); town
stations use OpenStreetMap localities or absorbed-village polygons (0.45–0.5), otherwise a position
interpolated along the numbering (0.15–0.25). 158 stations are urban (Municipal Corporation, incl.
villages it absorbed in 2016), 209 rural.

**Roll.** AC totals are official (S7). A booth's electors = votes polled ÷ turnout of its setting,
averaged over the two elections on that roll, shrunk 25% to the setting mean, never below votes
polled, scaled to the exact total. Urban and rural turnout are set so both settings have the same
average roll (ECI sizes booths that way): VS-2024 gives 61.2% urban, 72.3% rural. Women: official total
spread by the Census sex ratio of the booth's village. 18–19: official total spread evenly. 20–29 and
60+: ECI state shares. VS-2019 → VS-2024 change: net per booth; gross additions at least the 18–19
year olds.

**Community.** Five groups: Muslim, ST, SC, Kurmi (Mahato), and upper caste with other OBC (they vote
too alike to separate). Each booth's mix best reproduces its real VS-2024 and LS-2024 vote shares
(INDIA / NDA / JLKM / others) under S9's vote-by-community figures, kept near the Census SC/ST of its
village, pulled towards its unit's average, and with Muslim share scaled to Census religion per unit
(town 30.3%, Giridih block 22.4%, Pirtand 8.3%). Result, AC-wide: other Hindu 44%, Muslim 22%, SC 14%,
ST 14%, Kurmi 6% (Census 2011 AC proxy: SC 14.3%, ST 16.9%, Muslim 21.1%). Confidence 0.2–0.55.

## 5. Still missing, and where it would come from

| Missing | Unlocks | Source |
|---|---|---|
| Electors per booth (real) | Replaces the modelled roll | Roll part PDFs (contain voter names; parse to counts only, never store names) |
| Exact booth coordinates | Exact markers | CEO GIS portal (login), ECI booth locator (captcha) |
| 2019 polling-station list | A reviewed 2019→2024 link | CEO Jharkhand archive |
| PS lists for ACs 31, 33, 42, 61, 65 | Places, areas and modelled layers there | CEO register, as S6 |
| VS-2019 Form 20 for the other ACs | Swing there | CEO Jharkhand VS-2019 Form 20 |
| Caste survey | Replaces the community model | Field survey (`caste_survey` table) |
| Hindi panchayat names | Hindi news → panchayat | SEC Jharkhand lists |
| Full affidavits | All candidate profiles | MyNeta |
