# RUN.md — How to run the Jharkhand Election Monitor

The operator's guide: every command to set up, run, load data into and operate the system.
`docs/GETTING_STARTED.md` is the short version for developers.

**How each section was checked (1 October 2026, branch `restructure-and-tests`, macOS):**

- **§2 and §3A–B (set up and run locally):** run in full. A Playwright live suite then checked
  every page as each role on the real Form 20 data (`frontend/e2e/live/`, 12 passed, 1 skipped).
- **§3C (Docker Compose):** not run, because no Docker daemon was available. The Compose file
  passes `docker compose config`.
- **§5 (loading real data):** every flag shown comes from the loader's own `--help`. The Form 20
  table loader (`ingest.load_form20_tables`) has run on the real Giridih VS-2024 and VS-2019
  files. The PDF loaders have run only on the synthetic documents `dev_stack.py --synthetic`
  generates.

All Python commands run **from `backend/`** with the virtualenv's Python. That is `../.venv/bin/python`
on macOS/Linux and `..\.venv\Scripts\python` on Windows; it is written `python` below.

---

## 1. What this is

A booth-level intelligence platform for six Jharkhand assembly constituencies: Giridih (32), Gandey
(31), Dumri (33), Tundi (42), Silli (61) and Kanke (65). It turns public election documents that exist
only as PDFs (ECI Form 20, polling-station lists, electoral rolls) into a database. It shows them as a
Hindi/English dashboard where every figure traces back to its source page.

| Folder | What it holds |
|---|---|
| `backend/` | FastAPI API, loaders (`ingest/`), analytics, news, worker, the parked chatbot, migrations and seeds (`db/`), scripts, tests |
| `frontend/` | React + Vite dashboard, unit tests, Playwright specs |
| `docs/` | Design, operations (this file), status |

Two rules shape everything:
1. **No individual voter data is stored.** Rolls become per-booth counts, and roll PDFs never leave
   the host.
2. **Nothing loads unless it reconciles.** Form 20 booth totals must equal the published constituency
   totals to the vote.

## 2. One-time setup

Requirements: Python 3.11, Node 20+ (22 tested), Git. Docker is optional (§3C). No PostgreSQL install
is needed for local work, because `pgserver` provides an embedded PostgreSQL 16 with pgvector.

**macOS / Linux** — from the repository root:
```bash
make setup
```
That runs:
```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements-dev.txt -r backend/requirements-worker.txt
npm --prefix frontend ci
cd frontend && npx playwright install chromium
```

**Windows (PowerShell)** — from the repository root:
```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r backend\requirements-dev.txt -r backend\requirements-worker.txt
npm --prefix frontend ci
cd frontend; npx playwright install chromium; cd ..
```
If the Chromium download is blocked, use an installed browser for Playwright with
`PLAYWRIGHT_CHANNEL=msedge` (or `chrome`).

## 3. Running it

### A. Frontend only, on mock data (no backend)

```bash
make dev-fixtures                        # = cd frontend && VITE_FIXTURES=1 npm run dev
```
PowerShell: `cd frontend; $env:VITE_FIXTURES="1"; npm run dev`

Open <http://localhost:5173>. You are a fixture admin and every page carries a "fixture data" banner.

### B. Full local stack (the normal way to develop and demo)

**Terminal 1 — database and API:**
```bash
make dev-stack                           # = cd backend && ../.venv/bin/python scripts/dev_stack.py
```
PowerShell: `cd backend; ..\.venv\Scripts\python scripts\dev_stack.py`

The first run takes about a minute. By default it builds the **real Giridih data**:

| Step | What it does |
|---|---|
| `migrate` | applies all migrations, 0001–0023 |
| `seed` | loads parties, constituencies, blocks, boundaries, knowledge cards and the Form 20 totals |
| `form20_real` | loads `db/seed/form20/giridih_vs2024_form20.xlsx` (creating 367 booths) and then the VS-2019 file, through `ingest.load_form20_tables` |
| `refresh` | refreshes the 14 views |
| `users` | creates one user per role |

The results are the ECI's. No PS list, roll or locations are loaded, so the booths sit in a
placeholder block "PS list not loaded" (3299), the map shows outlines only, and per-booth
electors, turnout and community estimates are "not available".

`--synthetic` builds the old mock dataset instead (generated Form 20 / PS-list / roll PDFs,
loaded through the PDF loaders, plus community estimates and synthetic booth points). Every page
shows a "synthetic data" banner in that mode. Switching between modes needs `--rebuild`.

It then serves the API at <http://localhost:8000>, with `/health` and API docs at `/docs`.
The last thing it prints is the logins, which are also saved in `backend/.devstack/users.json`:

| Role | Phone | Sees |
|---|---|---|
| admin | 9000000001 | everything, including Admin (review queue, jobs, usage) |
| strategist | 9000000002 | all analysis including community estimates; no Admin |
| block | 9000000003 | only their block: "PS list not loaded" (all 367 booths) on the real data, Giridih Block on `--synthetic`; no community estimates, Transfer or Scenario |

Passwords are regenerated by `--rebuild`.

**Terminal 2 — dashboard** (a fresh terminal, so `VITE_FIXTURES` is not set):
```bash
make dev-frontend                        # = npm --prefix frontend run dev
```
Open <http://localhost:5173> and sign in. The dev server forwards `/api` to `:8000`.

Only Giridih (AC 32) has booth data. The other five constituencies have seeded constituency-level
figures (marked unverified) and show "not loaded" states.

**`dev_stack.py` options** (run from `backend/`):
```bash
python scripts/dev_stack.py --rebuild          # drop the database and build again (new passwords)
python scripts/dev_stack.py --no-serve         # build, leave PostgreSQL running, do not start the API
python scripts/dev_stack.py --synthetic        # the mock dataset instead of the real Form 20 (with --rebuild to switch)
python scripts/dev_stack.py --from-step refresh  # resume a build from one step
python scripts/dev_stack.py --url              # print DATABASE_URL for the embedded database
python scripts/dev_stack.py --stop             # stop the embedded PostgreSQL
```
Ctrl-C in terminal 1 stops the API and PostgreSQL. All the stack's state lives in
`backend/.devstack/` (git-ignored); deleting that folder resets everything.

### C. Docker Compose (single server)

> Not run on this branch (no Docker daemon was available); `docker compose config` validates.
> Run it once before relying on it — tracked as O-19 in `docs/status/KNOWN_ISSUES.md`.

```bash
cp .env.example .env
#   POSTGRES_PASSWORD=<strong>   JWT_SECRET=$(openssl rand -hex 32)   READONLY_DB_PASSWORD=<strong>
#   DATABASE_URL / READONLY_DB_URL must use host `db` and the same passwords
docker compose build
docker compose up -d db
docker compose run --rm worker python -m db.apply_migrations --seed
docker compose run --rm api python -m scripts.create_admin --phone 9XXXXXXXXX --name "Admin" \
    --role admin --generate-password
docker compose up -d
docker compose ps                         # db, api, worker, web
```
The dashboard is on port 80, and the API on `127.0.0.1:8000` (and via nginx at `/api/`).

Notes for running without the worker, and for TLS:
- **Without the `worker` service** (`docker compose up -d db api web`), nothing refreshes the views.
  Run `docker compose run --rm worker python -m analytics.refresh` after every load.
- **TLS** is not configured. nginx listens on 80 only (open item O-17).

## 4. Configuration

**Backend** settings live in the root `.env` (template: `.env.example`). The Python code reads the
process environment only. Compose passes `.env` through, and `dev_stack.py` sets what it needs, so
you export variables yourself only when running a command by hand against another database.

| Variable | Required | Purpose |
|---|---|---|
| `DATABASE_URL` | yes | PostgreSQL 16 with the `vector` extension |
| `JWT_SECRET` | yes | ≥ 32 bytes and not a placeholder; the API refuses to start otherwise |
| `CHAT_ENABLED` | | default `false`; the chatbot is parked |
| `ANTHROPIC_API_KEY` | | news labelling (and chat if enabled) |
| `READONLY_DB_URL`, `READONLY_DB_PASSWORD` | | the chatbot's read-only role |
| `STORAGE_BACKEND`, `STORAGE_BACKEND_<KIND>`, `S3_*` | | where source PDFs live; roll PDFs are always local |
| `RAW_DIR`, `OCR_DIR`, `BACKUP_DIR` | | default `raw`, `ocr`, `backups`, relative to `backend/` |
| `NOMINATIM_USER_AGENT` | | needed for `ingest.geocode` |

**Frontend** settings live in `frontend/.env` (template: `frontend/.env.example`; Vite reads only that
folder). The keys are:

| Variable | Purpose |
|---|---|
| `VITE_FIXTURES` | mock data |
| `VITE_API_BASE` | API path, default `/api` |
| `VITE_API_URL` | dev proxy target, default `http://localhost:8000` |
| `VITE_SOURCE_DOCS_URL` | where source PDFs are published; unset = source shown as text |
| `VITE_TILE_*` | map tiles; default OpenStreetMap |

## 5. Loading real data

Run from `backend/` with `DATABASE_URL` set to the target database. Check it first with
`python scripts/preflight.py`, which writes nothing.

Every loader has `--dry-run`, so run it dry, read the report, then run it for real. Most loaders take
`--ac N`, which is required when the election label exists in several constituencies (it does for all
six seeded ones). Parsers accept a PDF as a path, as `--doc <sha256>` or as `--key <storage key>`.

**5.1 Find and download source PDFs**
```bash
python -m ingest.fetch_ceo --discover <CEO-Jharkhand page URL>                 # list what it finds
python -m ingest.fetch_ceo --discover <URL> --kind ps_list --download          # kinds: form20, roll_mother, roll_supplement, ps_list, other
python -m ingest.extract_pdf raw/ps_list/<file>.pdf --kind ps_list --register  # text layer, OCR fallback, source_doc row
```

**5.2 Polling-station list — do this first.** The newest list creates the booths.
```bash
python -m ingest.parse_pslist raw/ps_list/<2024>.pdf --ac 32 --election VS-2024 --dry-run
python -m ingest.parse_pslist raw/ps_list/<2024>.pdf --ac 32 --election VS-2024 --load --anchor --block <block_id>
```

**5.3 Form 20 (booth results).** Refused unless every booth adds up and the totals equal the published
AC totals; failures go to the Admin review queue with the page number.
```bash
python -m ingest.parse_form20 raw/form20/<2024>.pdf --ac 32 --election VS-2024 --dry-run
python -m ingest.parse_form20 raw/form20/<2024>.pdf --ac 32 --election VS-2024 --load
```

**5.3a Form 20 delivered as extracted tables (xlsx, one sheet per page).** This is how Giridih
VS-2024 and VS-2019 are loaded. The run is refused, and nothing is written, unless:
- every row adds up;
- the columns equal the printed "Total EVM Votes" row;
- EVM + postal equals "Total Votes Polled";
- the booth sum + postal equals the seeded published totals.

`--create-booths` makes placeholder booths, numbered by PS, when the AC has none (no PS list
yet). A second election is mapped by PS number only when the vote correlation shows the
numbering is stable.
```bash
python -m ingest.load_form20_tables db/seed/form20/giridih_vs2024_form20.xlsx --ac 32 --election VS-2024 --create-booths --dry-run
python -m ingest.load_form20_tables db/seed/form20/giridih_vs2024_form20.xlsx --ac 32 --election VS-2024 --create-booths
python -m ingest.load_form20_tables db/seed/form20/giridih_vs2019_form20.xlsx --ac 32 --election VS-2019
python scripts/build_form20_seeds.py --check   # the seeded AC totals still match the files
```
A candidate's party is not printed on Form 20. Record known affiliations in
`db/seed/form20/candidate_parties.csv`, then run `python scripts/build_form20_seeds.py` and reseed.
Use `--replace` to reload an election. Use `--skip-ac-check` only where no published total exists.

**5.4 Older elections: load their PS list, then crosswalk onto the anchor booths**
```bash
python -m ingest.parse_pslist raw/ps_list/<2019>.pdf --ac 32 --election VS-2019 --load
python -m ingest.crosswalk --ac 32 --election VS-2019 --report      # dry run: auto / review / new counts
python -m ingest.crosswalk --ac 32 --election VS-2019 --apply
python -m ingest.parse_form20 raw/form20/<2019>.pdf --ac 32 --election VS-2019 --load
```
Matches with a score of at least 0.85 are accepted automatically. Those from 0.65 to 0.85 go to the
review queue, and anything lower becomes a new booth. **Clear the queue (Admin → review)** before
trusting any swing figure.

**5.5 Electoral rolls (counts only; the PDF stays on this host)**
```bash
python -m ingest.parse_roll raw/rolls/<mother>.pdf --ac 32 --election VS-2024 \
    --revision 2026-SSR --date 2026-01-01 --link-election VS-2024 --load
python -m ingest.parse_roll raw/rolls/<supplement>.pdf --ac 32 --election VS-2024 \
    --revision 2026-SUP-1 --date 2026-06-01 --supplement --load
python -m ingest.validate --privacy        # scans disk and database for EPIC / phone / Aadhaar patterns; must be clean
```
`--link-election` records which roll an election was fought on. Turnout and new-voter share need it.
If old roll text is found on disk the loader refuses to run; clear it with
`python scripts/purge_roll_cache.py --delete`.

**5.6 Everything else**
```bash
python -m ingest.fetch_sec --load-csv data/sec/<file>.csv --election PANCHAYAT-2022 --ac 32   # local results (hand-transcribed CSV)
python -m ingest.load_csv demography          data/<census>.csv     --ac 32   # Census 2011 village figures
python -m ingest.load_csv candidate_profile   data/<candidates>.csv --ac 32
python -m ingest.load_csv local_office_holder data/<office>.csv     --ac 32
python -m ingest.geocode                         # Nominatim, 1 request/s, every AC; --pin <uid> --lat --lon to set one by hand
python -m analytics.caste_estimate               # community estimate, every AC
python -m analytics.refresh                      # rebuild the views (the worker does this daily)
python -m ingest.validate --strict --verbose     # the full check report
python -m scripts.build_boundaries               # rebuild map outlines from DataMeet/geoBoundaries (--offline: use raw/geo only)
```
CSV columns for each `load_csv` kind are listed by `python -m ingest.load_csv --help`.

On the default (real) dev stack, `python -m ingest.validate --strict` passes every check,
including the Form 20 totals (booth sum + postal = declared) and `--privacy`.

On `--synthetic`, two checks fail, and both are expected:
- **`crosswalk_quality`:** the synthetic station names are deliberately perturbed, so fewer than
  90% of stations auto-match.
- **`roll_continuity`:** the check compares every roll revision, supplements included, across all
  constituencies (open item O-06).

Then repeat §5.2–5.6 for each other constituency with its own `--ac`.

## 6. Day-to-day operations

```bash
python -m worker.run --list                    # job names
python -m worker.run news.crawl                # run one job now (Docker: docker compose run --rm worker python -m worker.run <job>)
python -m db.apply_migrations --status         # pending migrations
python -m db.apply_migrations                  # apply them (add --seed to reload seeds)
python -m scripts.create_admin --phone 9XXXXXXXXX --name "Name" --role strategist --generate-password
python -m scripts.create_admin --phone 9XXXXXXXXX --name "Name" --role block --block <block_id> --generate-password
```

The worker's schedule (Asia/Kolkata):

| Job | When |
|---|---|
| `news.crawl` | every 4 h |
| `news.label_batch` | 01:00 (needs `ANTHROPIC_API_KEY`) |
| `news.label_collect` | 07:00 |
| `news.embed` | 07:15 |
| `ceo.check_new_supplement` | 06:00 |
| `analytics.refresh` | 07:30 |
| `analytics.caste_estimate` | 07:45 |
| `ops.backup` | 03:00 |
| `ops.purge_prompts` | 03:30 |
| `ops.usage_report` | 08:00 |

Job status is under **Admin → jobs**.

## 6B. Supabase (`election_monitor`)

Use the **session pooler** URL (`sslmode=require`, port 5432). Apply migrations once:

```bash
cd backend
export $(grep -v '^#' ../.env | xargs)   # DATABASE_URL, JWT_SECRET, APP_USERNAME, APP_PASSWORD
python -m db.apply_migrations
python scripts/load_supabase.py          # seed, Form 20, gazette, overlay, refresh
```

Run the API against that `.env`:

```bash
uvicorn api.main:app --host 0.0.0.0 --port 8000
```

Frontend: `VITE_API_BASE=http://localhost:8000 npm run dev` in `frontend/`.

Docker without a local Postgres:

```bash
docker compose -f docker-compose.yml -f docker-compose.supabase.yml up api web worker
```

## 7. Tests

```bash
make test           # backend unit + DB tests, Vitest, build, i18n check, Playwright on fixtures
make lint           # ruff + eslint
make test-e2e-live  # Playwright against a running `make dev-stack` (every page, every role)
```
See `docs/TESTING.md` for the layout and the feature → test map.

## 8. When something goes wrong

| Symptom | Cause / fix |
|---|---|
| API exits mentioning `JWT_SECRET` | Set ≥ 32 random bytes: `python -c "import secrets; print(secrets.token_hex(32))"` |
| `dev_stack.py`: address in use on :8000 | Another API is running: stop it, or `python scripts/dev_stack.py --stop` |
| `npm run dev`: port 5173 in use | Vite uses a strict port; stop the other dev server |
| Dashboard: "Could not load the list of constituencies" | The API is not running on :8000 (or `VITE_API_URL` points elsewhere) |
| Every page empty right after a load | Views not refreshed: `python -m analytics.refresh` |
| Form 20 load: "unresolved column" | A party/candidate spelling is missing from `db/seed/party_alias.csv`; add it, `python -m db.apply_migrations --seed`, retry |
| Form 20 load: AC total mismatch | Wrong `db/seed/ac_totals.csv` row or a missing page; see Admin → review |
| A loader says the label exists in several constituencies | Add `--ac N` |
| Map shows every booth grey | That election is not loaded, or its crosswalk is not applied (§5.4) |
| Swing looks impossible | Unreviewed crosswalk matches; clear Admin → review |
| New voters "—" everywhere | No roll linked to the election: `parse_roll ... --link-election <label>` |
| Roll load refuses to run | Old roll text on disk: `python scripts/purge_roll_cache.py --delete` |
| `/health` returns 503 | The API cannot reach the database; check `DATABASE_URL` (the log has the full error) |
| Playwright cannot download Chromium | `PLAYWRIGHT_CHANNEL=msedge` (or `chrome`) |

## 9. Things to remember

- Raw government PDFs under `raw/` are the audit trail; keep them.
- Giridih's VS-2024 and VS-2019 booth results are the real Form 20. Electorates, locations and
  rolls are not loaded, and everything in a `--synthetic` stack is mock. The five newer constituencies stay
  `verified=false` until someone checks their seeds against ECI.
- Check every figure against its source page before a decision relies on it.
