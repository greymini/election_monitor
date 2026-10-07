# Form 20 source files

Booth-wise results (ECI Form 20) as tables, one worksheet per printed page, read by
`ingest/form20_tables.py` and loaded by `python -m ingest.load_form20_tables --all-seed`.
Named `<ac>_<vs|ls><year>_form20.xlsx`.

| Files | Election | Source |
|---|---|---|
| `giridih_vs2019_form20.xlsx`, `giridih_vs2024_form20.xlsx` | VS-2019, VS-2024, AC 32 | ECI Form 20, extracted (VS-2024 identical to the CEO PDF) |
| `{gandey,dumri,tundi,silli,kanke}_vs2024_form20.xlsx` | VS-2024, ACs 31, 33, 42, 61, 65 | `ceo.jharkhand.gov.in/Form20_GLVS2024/Form20/<ac>.pdf` |
| `*_ls2024_form20.xlsx` | LS-2024 segment of each AC | `ceo.jharkhand.gov.in/AllForm20LS2024/<ac>.pdf` |
| `giridih_ls2019_form20.xlsx` | LS-2019, AC-32 segment of Giridih PC | `ceojh.jharkhand.gov.in/Lokshabha19/Form20_GELS19/06-Giridih.pdf` |

PDFs are converted with `scripts/form20_pdf_to_xlsx.py` (`--pc-ac 32 --names giridih_ls2019_candidates.csv`
for the PC-wide LS-2019 layout). Every file passes `form20_tables.problems()`: rows add up, columns equal
"Total EVM Votes", EVM + postal = "Total Votes Polled". LS segments print no postal ballots (counted for the
whole PC), so their postal rows are 0. LS-2019 prints no rejected/tendered columns and no segment footer;
its footers are the column sums.

Parties: `candidate_parties.csv` (every candidate, IndiaVotes from ECI; small parties as `OTH` with the full
name). After changing it, run `python scripts/build_form20_seeds.py` to rewrite `ac_totals.csv`.

Silli LS-2024 is not linked to booths: its numbering differs from VS-2024 by one added station.
