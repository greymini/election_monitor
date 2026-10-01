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

## Quick start (Windows PowerShell)

Requires Python 3.11 and Node 20.

```powershell
python -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
pip install -r requirements-api.txt      # plus the other requirements-*.txt files in the root
cd web; npm install; cd ..
copy .env.example .env                   # set JWT_SECRET to a long random value
```

**Frontend only, mock data:**
```powershell
cd web
$env:VITE_FIXTURES="1"; npm run dev      # http://localhost:5173
```

**Full stack on a local database (no Docker):**
```powershell
python scripts/dev_stack.py              # window 1: builds the DB, loads mock data, starts the API on :8000
cd web; npm run dev                      # window 2: frontend talking to the API
```

Docker Compose is also supported for a single-server setup.

Full operator guide: `RUN.md`. Loading real data and troubleshooting: `docs/RUNBOOK.md`.

## Configuration

Main `.env` settings (see `.env.example` for the full list):

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | PostgreSQL connection |
| `JWT_SECRET` | Required; the API refuses weak or placeholder values |
| `CHAT_ENABLED` | `false`; the chatbot is parked |
| `STORAGE_BACKEND` | `local` or `s3` (roll PDFs always stay local) |
| `VITE_TILE_URL` | Map tile source; defaults to OpenStreetMap |
| `ANTHROPIC_API_KEY` | Optional; news labelling only |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Optional; alerts |

## Tests

```powershell
pytest
cd web; npx playwright test              # add channel: 'msedge' in playwright.config.ts if Chromium can't download
```

## Documentation

| File | What it is |
|---|---|
| `NEXT_STEPS.md` | What to do next, in order |
| `RUN.md` | Plain-language run guide |
| `Giridih_AC32_Election_Monitor_HLD.md` | High-level design (v2) |
| `Giridih_AC32_Election_Monitor_LLD.md` | Low-level design (v2) |
| `MULTI_AC_EXPANSION_SPEC.md` | Multi-constituency spec and data points |
| `docs/METRICS.md` | Every metric definition and NULL rule |
| `docs/ENDPOINTS.md` | Every API route and its callers |
| `docs/RUNBOOK.md` | Operations and incident procedures |
| `DECISIONS.md` | Design decisions and why |
| `PROGRESS.md`, `UAT_READINESS.md` | Current status and test evidence |
| `AUDIT_REPORT.md` | Original code audit; finding IDs used throughout |

Review every figure against its source before relying on it.
