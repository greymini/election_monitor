# Giridih AC-32 Election Monitor

Booth-level election analytics for the **Giridih Vidhan Sabha constituency (AC-32), Jharkhand**,
built for the by-election that follows the sitting MLA's death on 6 September 2026.

Implements `Giridih_AC32_Election_Monitor_HLD.md` v0.1 and `..._LLD.md` v1.0.

---

## What it does

| Area | What is here |
|---|---|
| Ingestion | Form 20, electoral rolls, polling-station lists, SEC results — PDF text layer first, Tesseract only for pages without one, everything gated by validation |
| Booth crosswalk | Matches renumbered polling stations across years onto a stable `booth_uid`, with confidence and a review queue |
| Analytics | Swing, LS↔VS transfer, floating vote, volatility, new-voter share, booth priority, Monte-Carlo scenarios |
| Community estimates | Booth-level only, blended from surname inference, Census 2011 and ground survey, with a confidence score on every figure |
| News | Hindi/English crawl, dedupe, nightly Batch-API labelling, local embeddings, vector search |
| Dashboard | React, Hindi-first with English toggle, Leaflet map, exportable tables |
| Assistant | Two-tier routing, guarded read-only SQL, cited answers, hard refusals on individual-voter questions |

## What it deliberately does not do

- **It holds no individual voter records.** Rolls are parsed into per-booth counts and the names,
  EPIC numbers and addresses are discarded in memory. `tests/test_roll_privacy.py` asserts this on
  realistic input, and `ingest/validate.py` asserts the schema has nowhere to put one.
- **It does not state anyone's caste.** Community figures are estimates at booth level with a
  confidence score; anything below 0.4 renders greyed and labelled *अनुमान अपर्याप्त*.
- **It does not forecast.** The scenario engine is arithmetic on assumptions you state. The
  sympathy effect after a sitting member's death is an input, not an estimate.
- **It does not invent geography.** Panchayat names come from the published polling-station list,
  not from a seed file. See `db/seed/README.md` for why.

---

## Quick start

### Deployment (the intended path)

```bash
cp .env.example .env          # fill in DB credentials, JWT_SECRET, ANTHROPIC_API_KEY
docker compose up -d db
docker compose run --rm worker python -m db.apply_migrations --seed
docker compose run --rm worker python -m scripts.create_admin \
    --phone 9999999999 --name "Analyst" --role admin --generate-password
docker compose up -d
```

The dashboard is then on port 80 and the API on `127.0.0.1:8000`.

### Local development

```bash
python -m venv .venv && .venv/Scripts/activate      # or source .venv/bin/activate
pip install -r requirements-dev.txt -r requirements-worker.txt
pytest -q                                           # 96 tests, no database needed

cd web && npm install && npm run dev                # http://localhost:5173
uvicorn api.main:app --reload                       # http://localhost:8000/docs
```

The test suite covers the parsing, matching, blending, projection and guard logic and never
touches a database, so it runs anywhere.

---

## Loading data (Phase 0)

Order matters: the polling-station list creates the booths that everything else attaches to.

```bash
# 1. Find and download source PDFs. Discovery prints what it found and how it
#    classified each file - check that before you let it download.
python -m ingest.fetch_ceo --discover <ceo-page-url>
python -m ingest.fetch_ceo --discover <ceo-page-url> --kind ps_list --download

# 2. The newest PS list becomes the anchor: it creates booth_uids and panchayats.
python -m ingest.parse_pslist raw/ps_list/<file>.pdf --election VS-2024 --dry-run
python -m ingest.parse_pslist raw/ps_list/<file>.pdf --election VS-2024 --load --anchor --block 2

# 3. Form 20. Nothing loads until every row adds up and the booth sums match the
#    published AC totals.
python -m ingest.parse_form20 raw/form20/<file>.pdf --election VS-2024 --dry-run
python -m ingest.parse_form20 raw/form20/<file>.pdf --election VS-2024 --load

# 4. Older elections: load their PS list, then crosswalk onto the anchor booths.
python -m ingest.parse_pslist raw/ps_list/<2019>.pdf --election VS-2019 --load
python -m ingest.crosswalk --election VS-2019             # dry run, prints the bands
python -m ingest.crosswalk --election VS-2019 --apply

# 5. Rolls. Counts only - the parser discards names as it goes.
python -m ingest.parse_roll raw/rolls/<mother>.pdf --revision 2026-SSR --date 2026-01-01 --load
python -m ingest.parse_roll raw/rolls/<supp>.pdf --revision 2026-SUP-1 --date 2026-06-01 \
    --supplement --load

# 6. Derive everything and check it.
python -m ingest.geocode
python -m analytics.caste_estimate
python -m analytics.refresh
python -m ingest.validate --strict --verbose
```

`ingest/validate.py` is the gate to trust: it checks booth sums against published AC totals, row
arithmetic, crosswalk coverage and quality, roll continuity, and that no column capable of holding
an individual voter has appeared.

---

## Repository layout

```
db/           migrations (0001-0012) and seed data
common/       config, database, logging, Devanagari text handling, similarity
ingest/       fetchers, PDF extraction, OCR, parsers, crosswalk, geocoding, validation
analytics/    caste blending, scenario engine, materialized-view refresh, metric crib sheet
news/         crawler, dedupe, Batch-API labelling, local embeddings
chatbot/      router, tools, SQL guard, agent loop, budget, cached prompts
api/          FastAPI app, auth and role scoping, booth card, routers
worker/       APScheduler, job registry, ops jobs
web/          React + Vite + Tailwind + Leaflet + Recharts, Hindi-first
tests/        parsing, matching, blending, projection and guard tests
docs/         runbook and design notes
```

---

## Where this departs from the LLD

Three changes, each forced by something the LLD could not have known:

1. **`temperature` is rejected by Sonnet 5** with a 400. LLD §8.3 asks for temperature 0 on both
   models. Haiku 4.5 still accepts it, so the router and lookup calls keep temperature 0; answer
   depth on Sonnet is controlled by `output_config.effort` via `ANALYSIS_EFFORT` instead.
2. **The canonical Haiku 4.5 model id carries no date suffix** (`claude-haiku-4-5`).
3. **Party colours are not the conventional shades at conventional lightness.** Green and saffron at
   similar lightness are indistinguishable under red-green colour blindness (ΔE 3.2), which would
   have made the single most important comparison in the dashboard unreadable for roughly one male
   viewer in twelve. The greens are darkened and the saffrons lightened, which raises separation to
   ΔE 20 while keeping both recognisable. For the same reason the margin and swing scales are
   blue↔red rather than green↔saffron: a diverging ramp needs lightness to carry magnitude, so the
   two arms would collapse back together. See the header comment in `web/src/styles/tokens.css`.

Panchayat names are also left unseeded, for the reason in `db/seed/README.md`.

---

## Operating cost

Roughly **$65–100 a month** at the LLD's assumptions, hard-capped by `LLM_MONTHLY_CAP_USD`. At 80%
of the cap the assistant stops using Sonnet; at 100% the chat goes read-only and the dashboard is
unaffected. `/admin/usage` shows spend and the prompt-cache hit rate, which should stay above 90% —
if it drops, something volatile has crept into the cached prefix.

---

*Every figure this system produces — especially the community estimates and scenario projections —
must be reviewed for accuracy and completeness before any decision relies on it.*
