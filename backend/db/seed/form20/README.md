# Form 20 source files — Giridih (AC 32)

The ECI Form 20 (booth-wise results) for the Giridih assembly constituency, as tables extracted
from the published PDFs, one worksheet per printed page.

| File | Election | SHA-256 |
|---|---|---|
| `giridih_vs2019_form20.xlsx` | VS-2019 | `744ce366291e4d1c406980dc62cfb266f3fe9b8a8dee1e18296c6ba1e0b1e0da` |
| `giridih_vs2024_form20.xlsx` | VS-2024 | `6c836117fb0116a053de325511fc28b301217ae899e423e4ade8ce12f17125fe` |

What each holds, verified by `ingest/form20_tables.problems()` (tests/test_form20_tables.py):

- 367 polling stations (PS 1–367), votes per named candidate (12 in 2019, 14 in 2024),
  valid (excluding NOTA, as printed), rejected, NOTA, total, tendered.
- Footer rows `Total EVM Votes`, `Total Postal Ballot Votes`, `Total Votes Polled`.
- Every row adds up; column sums equal the EVM footer; EVM + postal = votes polled.
- 2019: the last station on pages 1–11 is interleaved with the page stamp ("P3a2", "1 2");
  the parser recovers it and the row arithmetic confirms the recovery.

What they do **not** hold: polling-station names, villages, panchayats or blocks (needs the
polling-station list), electors (needs the electoral roll), party affiliations.
`candidate_parties.csv` records the three affiliations the repository already had; every other
candidate is loaded under `UNK` ("party not recorded in source"). Add rows there (with a source)
and re-run `scripts/build_form20_seeds.py` and the loader to attribute more.

Load with `python -m ingest.load_form20_tables <file> --ac 32 --election VS-2024 --create-booths`
(the dev stack does this for both years). Regenerate the AC-level seed rows with
`python scripts/build_form20_seeds.py`.
