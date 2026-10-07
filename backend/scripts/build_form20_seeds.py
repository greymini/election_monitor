"""Regenerate db/seed/ac_totals.csv rows from every Form 20 workbook in db/seed/form20/.

    python scripts/build_form20_seeds.py            # rewrite the rows
    python scripts/build_form20_seeds.py --check    # exit 1 if they are stale

The published totals come from the Form 20 workbooks in db/seed/form20/, not
from a secondary source: every candidate (not just the top three), votes
including postal ballots, postal votes per candidate, NOTA, valid votes
(NOTA included, the project's definition), rejected and postal totals. Parties
come from db/seed/form20/candidate_parties.csv; a candidate it does not list is
seeded under UNK ("party not recorded in source") rather than guessed.

Workbooks are named `<ac name>_<vs|ls><year>_form20.xlsx` (giridih_vs2024,
gandey_vs2024, tundi_ls2024, ...); the AC number comes from db/seed/ac.csv.
A Lok Sabha workbook is the assembly segment's own Form 20: its postal ballots
are counted for the whole PC, so its postal rows are 0 and its totals are the
segment's EVM votes.

Rows for elections without a workbook (VS-2014, ...) and the `electors` rows
(which Form 20 does not print) are left exactly as they are.
"""

from __future__ import annotations

import argparse
import csv
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ingest import form20_tables  # noqa: E402

SEED = Path(__file__).resolve().parents[1] / "db" / "seed"
FORM20 = SEED / "form20"
HEADER = ["ac_number", "election_label", "candidate_name", "party_abbr", "metric", "value", "source"]


def files() -> list[tuple[str, str, str, Path]]:
    """(ac_number, AC name, election label, workbook) for every Form 20 workbook."""
    with (SEED / "ac.csv").open(encoding="utf-8") as fh:
        acs = {r["name_en"].lower(): (r["ac_number"], r["name_en"]) for r in csv.DictReader(fh)}
    out = []
    for path in sorted(FORM20.glob("*_form20.xlsx")):
        name, kind = path.stem.removesuffix("_form20").rsplit("_", 1)
        ac, ac_name = acs[name]
        out.append((ac, ac_name, f"{kind[:2].upper()}-{kind[2:]}", path))
    return out


def parties() -> dict[tuple[str, str, str], str]:
    with (FORM20 / "candidate_parties.csv").open(encoding="utf-8") as fh:
        return {(r.get("ac_number") or "32", r["election"], form20_tables.name_key(r["candidate"])):
                r["party_abbr"] for r in csv.DictReader(fh)}


def form20_rows() -> list[dict]:
    known = parties()
    out = []
    for ac, ac_name, label, path in files():
        table = form20_tables.read(path)
        bad = form20_tables.problems(table)
        if bad:
            raise SystemExit(f"{path.name} fails its integrity checks: {bad[:5]}")
        source = f"ECI Form 20, {ac_name} {label} ({path.name}, Total Votes Polled row)"
        polled, postal = table.polled, table.postal
        for name, total, by_post in zip(table.candidates, polled.votes, postal.votes, strict=True):
            party = known.get((ac, label, form20_tables.name_key(name)), "UNK")
            display = form20_tables.display_name(name)
            out.append(dict(ac_number=ac, election_label=label, candidate_name=display,
                            party_abbr=party, metric="votes", value=total, source=source))
            out.append(dict(ac_number=ac, election_label=label, candidate_name=display,
                            party_abbr=party, metric="postal", value=by_post, source=source))
        # NOTA's postal ballots, against the same NOTA candidate row the booth
        # loader uses (ingest.parse_form20.NOTA_CANDIDATE_NAME), so the AC total
        # can add them (0023).
        out.append(dict(ac_number=ac, election_label=label, candidate_name="NOTA",
                        party_abbr="NOTA", metric="postal", value=postal.nota, source=source))
        for metric, value in (("nota", polled.nota),
                              ("total_valid", polled.valid + polled.nota),
                              ("rejected", polled.rejected),
                              ("postal", postal.valid + postal.nota),
                              ("postal_rejected", postal.rejected),
                              ("votes_polled", polled.total)):
            out.append(dict(ac_number=ac, election_label=label, candidate_name="",
                            party_abbr="", metric=metric, value=value, source=source))
    return out


def rebuilt(text: str) -> str:
    rows = list(csv.DictReader(io.StringIO(text)))
    covered = {(ac, label) for ac, _name, label, _path in files()}
    replaced = [r for r in rows if not ((r["ac_number"], r["election_label"]) in covered
                                        and r["metric"] != "electors")]
    # Insert the Form 20 rows where the first replaced row was.
    position = next((i for i, r in enumerate(rows)
                     if (r["ac_number"], r["election_label"]) in covered), len(rows))
    keep_before = [r for r in rows[:position] if r in replaced]
    keep_after = [r for r in rows[position:] if r in replaced]
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=HEADER, lineterminator="\n")
    writer.writeheader()
    writer.writerows(keep_before + form20_rows() + keep_after)
    return buf.getvalue()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="exit 1 if ac_totals.csv is stale")
    args = ap.parse_args(argv)
    target = SEED / "ac_totals.csv"
    current = target.read_text(encoding="utf-8")
    new = rebuilt(current)
    if args.check:
        if new != current:
            print("db/seed/ac_totals.csv is out of date. Run: python scripts/build_form20_seeds.py")
            return 1
        print("ac_totals.csv matches the Form 20 workbooks")
        return 0
    target.write_text(new, encoding="utf-8")
    print(f"wrote {target.relative_to(SEED.parent.parent)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
