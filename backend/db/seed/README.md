# Seed data

Loaded by `python -m db.seed.load_seed` (or `python -m db.apply_migrations --seed`).

| File | Contents | Provenance |
|---|---|---|
| `blocks.csv` | The three blocks of AC-32 | HLD §3 |
| `areas_wards.csv` | GMC wards 1–36 | HLD §3. Wards are numbered in the published ward list; fill `name_hi`/`census_code` when the GMC ward notification is at hand. |
| `areas_panchayats.csv` | **Empty template — fill before use** | Panchayat names are NOT seeded. See note below. |
| `parties.csv` | Parties and 2024 alliance | Public record |
| `communities.csv` | Community taxonomy for caste estimation | HLD §5 |
| `surname_dict.csv` | Surname → community inference dictionary | Derived heuristic, see caveat below |
| `elections.csv` | Election rows 2005–2024 | HLD §1.1 |
| `ac_totals.csv` | AC-level totals used as the Form 20 validation target | Giridih VS-2024 and VS-2019: generated from the ECI Form 20 in `form20/` by `scripts/build_form20_seeds.py` (votes incl. postal, postal, NOTA, rejected, polled). Electors and every other row: HLD §1.1, **secondary sources; re-verify against ECI before relying on them** |
| `form20/` | The Giridih VS-2019 and VS-2024 Form 20 as extracted tables (xlsx, SHA-256 in its README), plus `candidate_parties.csv` | ECI Form 20; party affiliations each carry their own source |
| `news_sources.csv` | Crawl list | HLD §4 |
| `knowledge_cards/` | Curated cards for the chatbot | Written by the analyst team |

## Why panchayat names are not seeded

The HLD says Giridih Block has 15 panchayats and Pirtand Block has 17. Seeding 32
invented names would silently corrupt the geographic backbone that every rollup
depends on, and the error would be invisible once booths were attached to them.

Two supported ways to populate them, both authoritative:

1. **Automatic (preferred).** `ingest/parse_pslist.py` reads the `area_hint`
   column of the published polling-station list and creates panchayats from it,
   putting anything it cannot match confidently into `review_queue`.
2. **Manual.** Fill `areas_panchayats.csv` from the Jharkhand SEC panchayat
   notification or the CEO PS list and re-run the seed loader. It is idempotent.

## Caveat on `surname_dict.csv`

This dictionary drives an **estimate**, not a fact. Surnames map to communities
only probabilistically; ambiguous ones (Singh, Kumar, Das, Sinha) carry
`weight < 1` and are split, and some are deliberately left unmapped. Nothing in
this file is applied to an individual voter: `parse_roll.py` counts surnames into
per-booth aggregates and discards the names (HLD §5, LLD §12). Every figure it
produces must be shown with its confidence band and reviewed before use.
