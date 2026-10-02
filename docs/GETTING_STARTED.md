# Getting started

How to install, run and test the Jharkhand Election Monitor on a laptop. For operating a
deployed server, see `docs/operations/RUN.md` and `docs/operations/RUNBOOK.md`.

Giridih's VS-2024 and VS-2019 results are the **real ECI Form 20** (`backend/db/seed/form20/`):
all 367 polling stations, every candidate, postal ballots. Nothing else at booth level is loaded
yet: there are no locations, rolls or community estimates. `dev_stack.py --synthetic` builds a
mock dataset with all of those instead, and every page labels it synthetic. The other five
constituencies have seeded totals only, marked as needing verification.

---

## 1. Prerequisites

| Tool | Version | Needed for |
|---|---|---|
| Python | 3.11 | backend, tests, the local database |
| Node.js | 20 or later (22 tested) | frontend |
| Git | any | — |
| Docker + Compose | optional | the single-server deployment only |

No PostgreSQL install is needed locally: `pgserver` (a pip package) ships an embedded
PostgreSQL 16 with pgvector. Any other Postgres must be **16+ with the `vector` extension**.

## 2. One-time setup

From the repository root.

**macOS / Linux**
```bash
make setup
# which is:
python3.11 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements-dev.txt -r backend/requirements-worker.txt
npm --prefix frontend ci
cd frontend && npx playwright install chromium
```

**Windows (PowerShell)**
```powershell
python -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
pip install -r backend\requirements-dev.txt -r backend\requirements-worker.txt
cd frontend; npm ci; npx playwright install chromium; cd ..
```
If the Chromium download is blocked, use an installed browser instead:
`$env:PLAYWRIGHT_CHANNEL="msedge"` (or `chrome`).

## 3. Three ways to run it

### A. Frontend only, mock data (no backend)

```bash
make dev-fixtures                       # or: cd frontend && VITE_FIXTURES=1 npm run dev
```
PowerShell: `cd frontend; $env:VITE_FIXTURES="1"; npm run dev`

Open <http://localhost:5173>. You are signed in as a fixture admin; every page shows a
"fixture data" banner. Good for UI work; it proves nothing about the API.

### B. Full local stack (recommended for development)

Terminal 1 — backend:
```bash
make dev-stack                          # or: cd backend && ../.venv/bin/python scripts/dev_stack.py
```
PowerShell: `cd backend; python scripts\dev_stack.py`

The first run takes about a minute. It starts an embedded PostgreSQL under
`backend/.devstack/`, applies all migrations, loads the seed, loads the real Giridih Form 20
for 2024 and 2019 through `ingest.load_form20_tables`, refreshes the views, creates one user
per role and serves the API on <http://localhost:8000> (health check: `/health`, API docs:
`/docs`). Add `--synthetic` (with `--rebuild` when switching) for the mock dataset.
It prints the logins; they are also in `backend/.devstack/users.json`.

Terminal 2 — frontend (a fresh terminal, so `VITE_FIXTURES` is not set):
```bash
make dev-frontend                       # or: npm --prefix frontend run dev
```
Open <http://localhost:5173> and sign in with one of the printed users:

| Role | Sees |
|---|---|
| admin | everything, including Admin (review queue, jobs, usage) |
| strategist | all analysis including community estimates; no Admin |
| block | only their own block; no community estimates, Transfer or Scenario |

Useful `dev_stack.py` flags: `--rebuild` (throw the database away; new passwords),
`--no-serve` (build then exit), `--url` (print `DATABASE_URL`), `--stop` (stop Postgres),
`--from-step refresh` (resume a build), `--synthetic` (mock dataset).

To load a Form 20 delivered as extracted tables yourself:
```bash
cd backend
../.venv/bin/python -m ingest.load_form20_tables db/seed/form20/giridih_vs2024_form20.xlsx \
    --ac 32 --election VS-2024 --create-booths --dry-run   # check only; drop --dry-run to load
../.venv/bin/python -m analytics.refresh
```

Only Giridih (AC 32) has booth data. The other five constituencies have seeded
constituency-level figures only (marked unverified) and show honest "not loaded" states.

### C. Docker Compose (single server)

```bash
cp .env.example .env        # set POSTGRES_PASSWORD, JWT_SECRET (openssl rand -hex 32), READONLY_DB_PASSWORD
docker compose build
docker compose up -d db
docker compose run --rm worker python -m db.apply_migrations --seed
docker compose run --rm api python -m scripts.create_admin --phone 9XXXXXXXXX --name "Admin" --role admin --generate-password
docker compose up -d
```
The dashboard is on port 80; the API on `127.0.0.1:8000`. Without the `worker` service
nothing refreshes the materialized views — run `docker compose run --rm worker python -m
analytics.refresh` after every load. `docker compose build` has **not** been run since the
repository was restructured (no Docker daemon was available); treat this path as unverified.

## 4. Environment variables

**Backend** — root `.env` (template: `.env.example`). The Python code reads the process
environment only; it does not load `.env` itself. Compose passes it with `env_file`, and
`dev_stack.py` sets what it needs, so you only export variables yourself when running
`uvicorn` or a loader by hand.

Required: `DATABASE_URL`, `JWT_SECRET` (≥ 32 bytes, not a placeholder — the API refuses to
start otherwise). Optional: `CHAT_ENABLED` (default false), `ANTHROPIC_API_KEY` (news
labelling), `READONLY_DB_URL`/`READONLY_DB_PASSWORD` (chat only), `STORAGE_BACKEND*` and
`S3_*` (document storage), `RAW_DIR`/`OCR_DIR`/`BACKUP_DIR` (default `raw`, `ocr`,
`backups` relative to the working directory — run commands from `backend/`).

**Frontend** — `frontend/.env` (template: `frontend/.env.example`). Vite reads only this
folder. `VITE_FIXTURES`, `VITE_API_BASE` (default `/api`), `VITE_API_URL` (dev proxy
target, default `http://localhost:8000`), `VITE_SOURCE_DOCS_URL` (where source PDFs are
published; unset = source shown as text), `VITE_TILE_*` (map tiles; default OpenStreetMap).

## 5. Running the tests

| Command | What runs | Needs |
|---|---|---|
| `make test-backend` | ~960 Python unit tests | nothing |
| `make test-db` | ~560 SQL and API tests (every route × role, views, loaders, validation) | builds its own embedded Postgres |
| `make test-frontend` | Vitest (~60), `tsc` + build, i18n check | nothing |
| `make test-e2e` | Playwright on fixtures (42) | Chromium |
| `make test-e2e-live` | Playwright against the real API: every page × role, drawer, map, scenario, CSV, review queue (8) | `make dev-stack` running |
| `make test` | all of the above | — |
| `make lint` | ruff + ESLint | — |

Raw commands: `cd backend && ../.venv/bin/pytest -q` (everything, ~1 minute);
`cd frontend && npm test`, `npm run build`, `npm run check:i18n`,
`npx playwright test --project=fixtures`.

To run the DB tests against your own Postgres instead of pgserver, set
`E2E_DATABASE_URL`; its database name must contain `test` because the suite drops the
schema. Details and the feature → test map: `docs/TESTING.md`.

## 6. Loading real data

See `docs/operations/RUN.md` §5 (every command, with real flags) and each loader's `--help`, run from `backend/`.
In short: polling-station list (`ingest.parse_pslist --anchor`) → Form 20
(`ingest.parse_form20`) → crosswalk older years (`ingest.crosswalk`) → rolls
(`ingest.parse_roll --link-election`) → other data (`ingest.fetch_sec`, `ingest.load_csv`) →
`analytics.caste_estimate` → `analytics.refresh` → `ingest.validate`. A Form 20 load is refused
unless booth totals match the published constituency totals exactly. Pass `--ac N` to every
loader: election labels exist in all six seeded constituencies.
No real document has been loaded yet; see `docs/status/REMAINING_WORK.md`.

## 7. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| API exits at startup mentioning `JWT_SECRET` | Set a random value of ≥ 32 bytes: `python -c "import secrets; print(secrets.token_hex(32))"` |
| `dev_stack.py` says the port is in use | Another API on :8000, or a stale run: `python scripts/dev_stack.py --stop` |
| Frontend shows "Could not load the list of constituencies" | The API is not running or not on :8000 (`VITE_API_URL`) |
| Every page empty right after a load | Materialized views not refreshed: `python -m analytics.refresh` |
| Playwright cannot download Chromium | `PLAYWRIGHT_CHANNEL=msedge` (or `chrome`) |
| `npm run dev` fails: port 5173 in use | Vite uses a strict port; stop the other dev server |
| Map tiles show "API key required" | A keyed provider in `VITE_TILE_URL`; unset it for OpenStreetMap |
