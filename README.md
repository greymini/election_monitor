# Jharkhand Election Monitor

Booth-level election intelligence for six Jharkhand assembly constituencies: Giridih (32), Gandey (31), Dumri (33), Tundi (42), Silli (61) and Kanke (65).

It turns ECI Form 20 results, polling-station lists, electoral-roll counts, Census and local-election data, and local news into a single dashboard where every number traces back to the document and page it came from.

**Status:** under active development; booth-level figures are currently mock data. See `UAT_READINESS.md` and `PROGRESS.md` for exactly what works.

---

## Key rules

- **No individual voter data is stored.** Rolls are reduced to per-booth counts. Community composition is an aggregate estimate with a confidence score.
- **Results must reconcile.** A Form 20 load is refused unless it matches the ECI constituency totals to the vote.
- **Missing means missing.** Absent data shows as "—", never as 0.
- **One formula, one place.** Metric definitions live in `analytics/metric_sql.py`; see `docs/METRICS.md`.

## Tech stack

| Layer | Technology |
|---|---|
| Database | PostgreSQL 16 + pgvector (PostGIS optional) |
| API | Python 3.11, FastAPI, psycopg 3 |
| Background jobs | APScheduler (optional worker) |
| Ingestion | pdfplumber, Tesseract (hin+eng), jellyfish |
| Frontend | React 18, Vite, TypeScript, Tailwind, Leaflet, Recharts, React Query, i18next |
| Tests | pytest, Playwright |
| Hosting (planned) | Vercel (frontend), Railway (API), Supabase (Postgres + storage) |

## Repository layout

```
backend/    Python: api, analytics, ingest, news, worker, chatbot (parked), common, db, scripts, tests
frontend/   React + Vite + TypeScript dashboard, Vitest unit tests, Playwright e2e
docs/       design/ (HLD, LLD, decisions, metrics, endpoints) · operations/ · status/ · archive/
docker-compose.yml, Makefile, .env.example
```

## Quick start

Requires Python 3.11 and Node 20+. Full instructions, including Windows PowerShell and
Docker: **[docs/GETTING_STARTED.md](docs/GETTING_STARTED.md)**.

```bash
make setup          # .venv, Python deps, npm deps, Playwright Chromium
make dev-fixtures   # frontend only, mock data:            http://localhost:5173
make dev-stack      # backend on an embedded Postgres + mock data, API on :8000 (prints logins)
make dev-frontend   # in a second terminal: frontend talking to that API
make test           # backend unit + DB tests, frontend unit tests, build, i18n, Playwright
```

## Configuration

Backend settings live in the root `.env` (see `.env.example`); the app reads the process
environment, so export them (Docker Compose and `dev_stack.py` do this for you). Frontend
`VITE_*` settings live in `frontend/.env` (see `frontend/.env.example`).

| Variable | Where | Purpose |
|---|---|---|
| `DATABASE_URL` | backend | PostgreSQL 16 with pgvector |
| `JWT_SECRET` | backend | Required; the API refuses weak or placeholder values |
| `CHAT_ENABLED` | backend | `false`; the chatbot is parked |
| `STORAGE_BACKEND` | backend | `local` or `s3` (roll PDFs always stay local) |
| `ANTHROPIC_API_KEY` | backend | Optional; news labelling only |
| `VITE_FIXTURES` | frontend | `1` = mock data, no backend |
| `VITE_TILE_URL` | frontend | Map tiles; defaults to OpenStreetMap |
| `VITE_SOURCE_DOCS_URL` | frontend | Where source PDFs are published; unset = no source links |

## Documentation

| File | What it is |
|---|---|
| `docs/GETTING_STARTED.md` | How to install, run and test |
| `docs/status/KNOWN_ISSUES.md` | Every issue found in the October 2026 review, fixed or open |
| `docs/status/REMAINING_WORK.md` | What is incomplete, what to do next, resources needed |
| `docs/TESTING.md` | Test layout and feature → test map |
| `docs/design/HLD.md`, `docs/design/LLD.md` | High- and low-level design (v2) |
| `docs/design/METRICS.md`, `docs/design/ENDPOINTS.md` | Metric definitions; every API route and caller |
| `docs/design/DECISIONS.md` | Design decisions and why |
| `docs/operations/RUN.md`, `docs/operations/RUNBOOK.md` | Operator guide; operations procedures |
| `docs/status/` | Progress ledger, UAT readiness, audit report, baseline |

Review every figure against its source before relying on it.
