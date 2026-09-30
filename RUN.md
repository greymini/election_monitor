# RUN.md — How to run the Jharkhand Election Monitor

This is the plain-language guide: what the system is, what each part does, and every command you need in the order you need it. The `README.md` is the technical reference; this file is for getting it running and using it.

---

## 1. What this is, in one page

A constituency intelligence platform for six Jharkhand assembly seats — Giridih, Gandey, Dumri, Tundi, Silli and Kanke. It takes public election documents that only exist as PDFs on government websites, turns them into a clean database, and shows the result as a dashboard where every number can be traced back to the page it came from.

**What goes in**
- Booth-wise results (ECI Form 20) for every Vidhan Sabha and Lok Sabha election since 2009
- Electoral rolls and their supplements — only *counts* per booth (total, gender, age band, additions, deletions). No voter's name, EPIC number or address is ever stored.
- Polling-station lists, which give the booth → village/ward → panchayat → block mapping
- Panchayat and municipal election results (State Election Commission)
- Census 2011 and other village-level development indicators
- Local news (Hindi dailies, Google News, later YouTube/Telegram), labelled for place, issue, party and sentiment
- Manual ground data: booth coordinators' reports, local office-holders, influencers, events

**What comes out**
- A booth table with everything in one row (results, swing, turnout, new voters, estimated community mix with confidence, priority score, source page)
- A map, area rollups, booth cards, a voters view, caste/community view, LS↔VS transfer, candidate profiles, local politics, news stream, development indicators, a scenario model, and a cross-constituency comparison
- Alerts when a new document appears on a government portal or a news item matches a rule

**Two rules that shape everything**
1. **Nothing is stored about an individual voter.** Caste and religion are estimated at booth level from aggregate patterns and always shown with a confidence score.
2. **Nothing enters the serving tables unless it reconciles.** Booth totals must sum to the ECI-published constituency total to the vote, or the load is refused.

**Parked:** a chatbot panel exists in the code but is switched off (`CHAT_ENABLED=false`). The system runs fully without it.

---

## 2. Tech stack

| Layer | Technology | Why |
|---|---|---|
| Database | PostgreSQL 16 + PostGIS (geo) + pgvector (news search) | One database for everything; free |
| Backend API | Python 3.11, FastAPI, psycopg3 | Fast to write, typed, async |
| Background jobs | APScheduler in a worker container | Ten cron jobs on one box; no Airflow needed |
| PDF handling | pdfplumber (text PDFs), Tesseract hin+eng via poppler (scanned pages) | Free; Devanagari-capable |
| Text matching | Jaro-Winkler (jellyfish), custom Devanagari normalisation | Matching booth names across years |
| News labelling | Claude Haiku via Batch API (optional key) | Cheap, Hindi-capable |
| Embeddings | `intfloat/multilingual-e5-small`, local on CPU | Free news search |
| Scrapers | httpx + selectolax, Playwright fallback | No public APIs exist for this data |
| Frontend | React 18, Vite, TypeScript, Tailwind, Leaflet (OSM/CARTO tiles), Recharts, React Query, i18next | Hindi-first UI, fast, no paid map service |
| Web server | nginx (serves the built frontend, proxies `/api`, TLS via certbot) | Standard |
| Packaging | Docker Compose — `db`, `api`, `worker`, `web` | Single VPS deployment |
| Backups | nightly `pg_dump` → local volume → off-host (rclone/B2) | Recoverable |

Runs comfortably on one 4 vCPU / 8 GB VPS.

---

## 3. First-time setup

Prerequisites: Docker and Docker Compose installed; a domain pointed at the server if you want TLS.

```bash
# 1. Get the code
git clone <your-private-remote> election-monitor
cd election-monitor

# 2. Configure
cp .env.example .env
# Edit .env — at minimum:
#   POSTGRES_PASSWORD=<strong>
#   JWT_SECRET=$(openssl rand -hex 32)        # the API refuses to start with a weak or placeholder secret
#   READONLY_DB_PASSWORD=<strong>
#   CHAT_ENABLED=false
#   STORAGE_BACKEND=local                    # where source PDFs live
#   STORAGE_BACKEND_FORM20=s3                # published documents can go to a bucket
#   S3_ENDPOINT= / S3_BUCKET= / S3_ACCESS_KEY= / S3_SECRET_KEY= / S3_PREFIX=raw
#   ANTHROPIC_API_KEY=                       # optional — only for news labelling
#   TELEGRAM_BOT_TOKEN= / TELEGRAM_CHAT_ID=  # optional — alerts
#   DOMAIN=monitor.example.in                # for TLS

# 3. Build and start the database
docker compose build
docker compose up -d db
docker compose logs -f db          # wait for "database system is ready to accept connections"

# 4. Create the schema and load reference data (parties, communities, surnames, the six ACs, blocks/wards)
docker compose run --rm worker python -m db.apply_migrations --seed

# 5. Create the first admin user
docker compose run --rm worker python scripts/create_admin.py --phone 91XXXXXXXXXX --name "Admin"

# 6. Start everything
docker compose up -d
docker compose ps                  # all four services should be "running (healthy)"

# 7. Open
#   http://<server>/           dashboard (Hindi by default; toggle EN top right)
#   http://<server>/api/health returns 200 with {"status":"ok","database":true},
#                              or 503 if it cannot reach the database
```

### Running without the worker

The API reads everything from the database and needs no worker: `docker compose up -d db api web`
is a complete serving stack. What you give up is the schedule. **Nothing then refreshes the
materialized views**, so after every load you must run the refresh yourself:

```bash
docker compose run --rm worker python -m analytics.refresh   # or from a laptop, see 4.6
```

Until it runs, the overview, booth table, map, area rollup, transfer and priority pages return
empty rather than stale — they read materialized views, and an unrefreshed view has no rows. The
news crawl, nightly backup and portal watchers also do not run; see §5 for running any of them
by hand.

TLS (once DNS points at the box): `docker compose --profile tls up -d certbot` then `docker compose restart web`.

---

## 4. Loading data — the Phase 0 sequence

All commands run inside the worker. Prefix each with `docker compose run --rm worker` (or open a shell once: `docker compose run --rm worker bash`).

Every loader supports `--dry-run` (shows what would happen, writes nothing). Run dry first, then
for real.

> **`--ac` status.** `ingest.crosswalk` requires it: a PS number is only unique within a
> constituency, so crosswalking without one would match stations across ACs. The parsers
> (`parse_form20`, `parse_pslist`, `parse_roll`) resolve their constituency from the election
> label and the document instead, and reject `--ac` as an unknown argument - leave it off there.
> `ingest.validate` and `analytics.caste_estimate` do not accept it yet.

Each parser takes the document three ways. A path works as it always did; the other two exist so
ingestion can run from a laptop against a remote `DATABASE_URL`, without a local copy of `raw/`:

```bash
python -m ingest.parse_form20 raw/form20/2024-VS/32/giridih.pdf --election VS-2024   # a path
python -m ingest.parse_form20 --doc 3f9a2c1e --election VS-2024                      # by sha256
python -m ingest.parse_form20 --key form20/2024-VS/32/giridih.pdf --election VS-2024  # by key
```

`--doc` takes the sha256 (or an unambiguous prefix) of a registered document and asks the database
where the bytes are, so it works whichever backend holds them. It verifies the bytes against the
digest that was registered and refuses to parse on a mismatch — an overwritten object is not the
document your AC totals were reconciled against.

### 4.1 Polling-station list (the backbone — do this first)

```bash
python -m ingest.fetch_ceo --ac 32 --doc pslist --year 2024        # downloads to raw/JH/32/pslist/2024/
python -m ingest.extract_pdf raw/JH/32/pslist/2024/*.pdf
python -m ingest.parse_pslist --ac 32 --election VS-2024 --anchor   # mints booth UIDs like 32-B0147
```
The anchor list is the "current truth" for booth identity. Re-anchoring later needs `--re-anchor` and goes through the crosswalk; it will not silently rebind booths.

### 4.2 Booth-wise results (Form 20)

```bash
python -m ingest.fetch_ceo --ac 32 --doc form20 --year 2024 --type VS
python -m ingest.extract_pdf raw/JH/32/form20/2024-VS/*.pdf
python -m ingest.parse_form20 --ac 32 --election VS-2024 --dry-run   # shows per-booth checks and AC reconciliation
python -m ingest.parse_form20 --ac 32 --election VS-2024 --load
```
The load refuses if any candidate column can't be matched to a party, if any booth's arithmetic fails, or if the constituency total differs from the ECI figure by even one vote. Failures go to the review queue (`/admin/review-queue`) with the page number.

Repeat for `--year 2019`, then LS: `--type LS --year 2024` (the Lok Sabha Form 20 is per parliamentary seat; the parser extracts this AC's segment).

### 4.3 Booth crosswalk (linking booths across years)

```bash
python -m ingest.fetch_ceo --ac 32 --doc pslist --year 2019
python -m ingest.extract_pdf raw/JH/32/pslist/2019/*.pdf
python -m ingest.parse_pslist --ac 32 --election VS-2019          # no --anchor: goes to ps_list_entry only
python -m ingest.crosswalk --ac 32 --election VS-2019 --dry-run    # shows auto / review / new counts
python -m ingest.crosswalk --ac 32 --election VS-2019 --apply
```
Matches ≥ 0.85 are auto-accepted; 0.65–0.85 are written with `reviewed=false` and queued for a human; below 0.65 become new booths. **Clear the review queue** in `/admin/crosswalk` before trusting any 2019→2024 swing — it's a few dozen booths and an afternoon.

### 4.4 Electoral roll (counts only)

```bash
python -m ingest.fetch_ceo --ac 32 --doc roll --revision latest
python -m ingest.parse_roll --ac 32 --revision 2026-07 --mother --dry-run
python -m ingest.parse_roll --ac 32 --revision 2026-07 --mother --load
python -m ingest.parse_roll --ac 32 --revision 2026-09 --supplement --load
python -m db.link_roll --election VS-2024 --revision 2024-10        # which roll each election was fought on
```
Roll PDFs are never cached as text, and the PDF itself is **kept** (`RETAIN_RAW_ROLLS=true`, now
the default) at 0600 with directories at 0700. The previous default deleted the source PDF while
the extracted page text stayed on disk, which destroyed the auditable original and retained the
personal data.

Electoral rolls are also refused any remote storage backend. `STORAGE_BACKEND=s3` does not carry
them with it: a roll load aborts with `RollStorageViolation` rather than uploading names, EPIC
numbers and addresses to a bucket. After any roll load run the privacy check:
```bash
python -m ingest.validate --ac 32 --privacy      # scans disk and DB for EPIC patterns; must report clean
```

### 4.5 Running ingestion from a laptop

Nothing needs to run on the server. Point the tools at the database and the document store:

```bash
export DATABASE_URL='postgresql://user:pass@host:5432/giridih?sslmode=require'
export STORAGE_BACKEND=local STORAGE_BACKEND_FORM20=s3
export S3_ENDPOINT=... S3_BUCKET=... S3_ACCESS_KEY=... S3_SECRET_KEY=... S3_PREFIX=raw

python scripts/preflight.py        # checks all of the above and says how to fix each problem
```

`preflight` checks the connection (without printing your password back), warns if a remote host
has no `sslmode`, confirms `postgis` and `vector` are present, reports pending migrations and row
counts, shows which backend each document kind routes to, reaches the bucket, and checks the PDF
toolchain. It writes nothing, so it is safe against production. Exit code 1 means something in the
list will stop a load.

Use `sslmode=require` at minimum for a remote database; `verify-full` with a CA bundle if you have
one. Without it the connection — and every booth-level figure crossing it — may be in cleartext.

### 4.6 Everything else

```bash
python -m ingest.fetch_sec --ac 32 --load-csv data/sec/giridih_panchayat_2022.csv   # local election results
python -m ingest.load_csv candidate_profile data/candidates/2024_ac32.csv
python -m ingest.load_csv area_indicator     data/indicators/census2011_ac32.csv
python -m ingest.load_csv local_office_holder data/local/ac32_mukhiya_2022.csv
python -m ingest.geocode --ac 32                                    # Nominatim, 1 req/s, cached
python -m analytics.caste_estimate --ac 32
python -m analytics.refresh                                         # rebuild all materialized views
python -m ingest.validate --ac 32                                   # full check report
```

Then repeat 4.1–4.5 with `--ac 31`, `33`, `42`, `61`, `65`.

---

## 5. Day-to-day operations

```bash
docker compose logs -f api worker            # live logs
docker compose run --rm worker python -m worker.run news.crawl     # run any scheduled job by hand
docker compose run --rm worker python -m worker.run --list         # list job names
docker compose run --rm worker python -m ingest.validate --all     # validation across all ACs
docker compose run --rm worker python -m worker.run ops.backup     # backup now
bash scripts/restore_drill.sh                                       # prove a backup restores (do this once a month)
docker compose pull && docker compose build && docker compose up -d # update after a code change
```

Scheduled jobs (all IST): news crawl every 30 min · news labelling hourly · portal watchers 06:00 · MV refresh 07:30 and after every load · backup 03:00 · usage report 08:00. Status at `/admin/jobs`; failures also go to the Telegram alert group if configured.

---

## 6. Using the dashboard

- **Pick a constituency** with the switcher top-left; the choice is in the URL so views can be shared.
- **Overview** shows the headline result, 20-year trend and a *data health strip* — green ticks for what's loaded, red for what isn't, with the command to fix it.
- **Booths** is the main working table. Pick columns, filter by block/panchayat/party/margin, sort, export CSV. The link icon on each row opens the exact Form 20 page.
- **Map** — choose metric, year, block. Blue/red is who leads; grey means the booth has no data for that year.
- **Voters** — additions and deletions per booth per roll revision, age and gender split.
- **Caste** — estimated community shares with confidence bands; grey = too uncertain to use. The scatter shows community share against party share with a plain-language caveat.
- **Transfer** — Lok Sabha vs Vidhan Sabha in the same year, booth by booth. Blank means one leg isn't loaded, not zero.
- **Candidates, Local politics, News, Indicators, Scenario, Compare** — as named.
- **Admin** — sources per constituency and their parse status, review queue, crosswalk editor, surname dictionary, alert rules, jobs, usage.

Roles: `admin` sees everything; `strategist` sees everything except admin tools; `block` sees only their block and cannot see the caste module.

---

## 7. When something goes wrong

| Symptom | Likely cause | Do |
|---|---|---|
| API won't start, log says JWT_SECRET | placeholder or short secret in `.env` | `openssl rand -hex 32` into `JWT_SECRET`, restart |
| Migration fails on `postgis` | wrong DB image | `docker compose build db && docker compose up -d db` |
| Form 20 load says "unresolved column" | a candidate/party spelling not in `party_alias` | add the spelling to `db/seed/party_alias.csv`, `--seed`, retry |
| Load says AC total mismatch | wrong `ac_totals.csv` row, or a missing page | check `/admin/review-queue`; compare with ECI page |
| Map shows all booths grey | that year not loaded or crosswalk not applied | run 4.2/4.3 for that year |
| Swing looks impossible | unreviewed crosswalk matches | clear `/admin/crosswalk` review items |
| New voters zero everywhere | `election_roll_link` not set | `python -m db.link_roll …` |
| Source shows `drifted` in `/admin/sources` | government site changed layout | update `scrapers/sources.yaml` selectors and the recorded fixture; do **not** force-parse |
| Privacy check fails | roll text on disk or a PII string in a free-text field | `scripts/purge_roll_cache.py`; find and delete the offending record; investigate how it got in |
| `/api/health` returns 503 | the API cannot reach the database | `docker compose ps db`; check `DATABASE_URL`. The body names the exception class only — the full error is in the API log |
| Roll load says `RollStorageViolation` | a roll kind is configured for a remote backend | Unset `STORAGE_BACKEND_ROLL_MOTHER` / `..._ROLL_SUPPLEMENT`, or set them to `local`. The guard is not the problem: rolls must not leave the host |
| `parse_form20 --doc` says the digest does not match | the stored object is not the document that was registered | Do not force it. Re-fetch, or register the new bytes as a separate document and reconcile its AC totals before loading |
| Every page is empty right after a load | the materialized views have not been refreshed | `python -m analytics.refresh`. With no worker deployed this is a manual step after every load |
| `--ac` is rejected as an unknown argument | the multi-AC spine is not in yet | Leave the flag off; the loaders work on the single seeded constituency |

---

## 8. Things to remember

- Raw government PDFs under `raw/` are kept forever. They are the audit trail and the only way to re-parse if a site disappears.
- Every figure the system shows should be verified against the source page before it is used in a decision. The system makes that a one-click check; it does not remove the responsibility.
- Seed rows for the five newer constituencies carry `verified=false` until someone has checked them against ECI. The overview shows a small "unverified" badge until then.
