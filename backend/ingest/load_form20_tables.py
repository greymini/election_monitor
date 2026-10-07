"""Load a Form 20 delivered as extracted tables (xlsx, one sheet per page).

    python -m ingest.load_form20_tables db/seed/form20/giridih_vs2024_form20.xlsx \\
        --ac 32 --election VS-2024 --create-booths
    python -m ingest.load_form20_tables db/seed/form20/giridih_vs2019_form20.xlsx \\
        --ac 32 --election VS-2019
    ... --dry-run          parse and check everything, write nothing

The PDF path (ingest/parse_form20.py) extracts text and then has to guess
columns; here the columns are already tables, so this reads them with
ingest/form20_tables.py and reuses parse_form20's write path. Nothing is
written unless every check passes:

* the file's own integrity (form20_tables.problems): every station present
  once, every row adds up, columns equal "Total EVM Votes", EVM + postal =
  "Total Votes Polled";
* every candidate header matches a candidate seeded for this election in
  db/seed/ac_totals.csv (scripts/build_form20_seeds.py writes those from the
  same file), so booth votes land on the candidate the published total hangs off;
* each candidate's booth sum + postal ballots = its published total, at zero
  tolerance - the LLD 4.2 gate, postal-aware (0023).

Then, in one transaction: the document in source_doc (sha256, pages, real -
not synthetic), booth rows and NOTA (parse_form20.load), the declared AC
totals, `candidate.is_winner` and `election.votes_polled_published`.

**Booths without a polling-station list.** A Form 20 numbers stations but
does not name or place them. With `--create-booths` (only when the AC has no
booths yet) every station gets a booth `32-B0001...` from next_booth_uid,
filed under an "Unassigned (PS list pending)" area in a "PS list not loaded"
block, and this election's crosswalk is the anchor. Loading the real
polling-station list later replaces those placeholders with names, areas and
blocks.

**A second election onto the same booths.** Station n is mapped to the booth
anchored at station n only if the data says the numbering is stable: booth
sizes must correlate at r >= 0.9 between the two elections at the same
number, and clearly better than at a one-station offset. The measured
correlations are written to crosswalk_audit for every station and the
mapping is recorded as 'exact' with confidence 0.95, unreviewed, so swing is
computed and a reviewer can see why. If the test fails, nothing is written.
"""

from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

from common.logging_setup import get_logger, setup_logging
from ingest import form20_tables
from ingest.acscope import resolve_election

log = get_logger(__name__)

PLACEHOLDER_BLOCK_OFFSET = 99          # block 3299 for AC 32
PLACEHOLDER_BLOCK_NAME = ("PS list not loaded", "मतदान केंद्र सूची लोड नहीं")
PLACEHOLDER_AREA_NAME = ("Unassigned (PS list pending)", "अनिर्धारित (मतदान केंद्र सूची लंबित)")
SAME_PS_MIN_R = 0.9
SAME_PS_CONFIDENCE = 0.95


class LoadRefused(Exception):
    """A check failed; nothing was written."""


def _correlation(xs: list[float], ys: list[float]) -> float:
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sx = sum((x - mx) ** 2 for x in xs) ** 0.5
    sy = sum((y - my) ** 2 for y in ys) ** 0.5
    if not sx or not sy:
        return 0.0
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / (sx * sy)


def numbering_evidence(this: dict[int, int], anchor: dict[int, int]) -> dict:
    """Whether station n here is station n in the anchor election.

    `this` and `anchor` map PS number -> votes polled. Returns the same-number
    correlation, the best correlation at a +-1/2 offset, and the verdict.
    """
    common = sorted(set(this) & set(anchor))
    same = _correlation([this[p] for p in common], [anchor[p] for p in common])
    offsets = {}
    for k in (-2, -1, 1, 2):
        pairs = [(this[p], anchor[p + k]) for p in common if p + k in anchor]
        offsets[k] = _correlation(*map(list, zip(*pairs, strict=True)))
    best_offset = max(offsets.values())
    ok = same >= SAME_PS_MIN_R and same - best_offset >= 0.3
    return {"same_ps_r": round(same, 3), "best_offset_r": round(best_offset, 3),
            "stations_compared": len(common), "stable": ok}


def _seeded_candidates(cur, election_id: int) -> dict[str, dict]:
    cur.execute(
        "SELECT c.candidate_id, c.name_en, p.abbr FROM candidate c "
        "LEFT JOIN party p ON p.party_id = c.party_id "
        "WHERE c.election_id = %s AND p.abbr IS DISTINCT FROM 'NOTA'", (election_id,))
    return {form20_tables.name_key(r["name_en"]): r for r in cur.fetchall()}


def _published(cur, election_id: int) -> dict[int, dict[str, int]]:
    cur.execute("SELECT candidate_id, metric, value FROM result_ac_total "
                "WHERE election_id = %s AND candidate_id IS NOT NULL", (election_id,))
    out: dict[int, dict[str, int]] = {}
    for r in cur.fetchall():
        out.setdefault(r["candidate_id"], {})[r["metric"]] = r["value"]
    return out


def _ensure_placeholder_area(cur, ac_id: int, ac_number: int) -> int:
    block_id = ac_number * 100 + PLACEHOLDER_BLOCK_OFFSET
    cur.execute(
        "INSERT INTO block (block_id, name_en, name_hi, kind, ac_id) VALUES (%s, %s, %s, 'rural', %s) "
        "ON CONFLICT (block_id) DO NOTHING",
        (block_id, *PLACEHOLDER_BLOCK_NAME, ac_id))
    cur.execute(
        "INSERT INTO area (block_id, kind, name_en, name_hi, ac_id) "
        "VALUES (%s, 'panchayat', %s, %s, %s) "
        "ON CONFLICT (block_id, kind, name_en) DO UPDATE SET name_hi = EXCLUDED.name_hi "
        "RETURNING area_id",
        (block_id, *PLACEHOLDER_AREA_NAME, ac_id))
    return cur.fetchone()["area_id"]


def _create_booths(cur, ac_id: int, ac_number: int, election_id: int,
                   stations: list[int]) -> int:
    area_id = _ensure_placeholder_area(cur, ac_id, ac_number)
    for ps in stations:
        cur.execute("SELECT next_booth_uid(%s) AS uid", (ac_number,))
        uid = cur.fetchone()["uid"]
        cur.execute(
            "INSERT INTO booth (booth_uid, ac_id, area_id, current_ps_number, notes) "
            "VALUES (%s, %s, %s, %s, %s)",
            (uid, ac_id, area_id, ps,
             "Created from the Form 20 station number. Name, area, block and location "
             "need the polling-station list."))
        cur.execute(
            "INSERT INTO booth_crosswalk (election_id, ac_id, ps_number, booth_uid, "
            "match_method, confidence, reviewed) VALUES (%s, %s, %s, %s, 'anchor', 1.0, true)",
            (election_id, ac_id, ps, uid))
    return len(stations)


def _anchor_totals(cur, ac_id: int, election_id: int) -> tuple[dict[int, str], dict[int, int]]:
    """Booth uid and votes polled per PS number, for the anchor election."""
    cur.execute(
        "SELECT x.ps_number, x.booth_uid, m.total_valid + COALESCE(m.rejected, 0) AS polled "
        "FROM booth_crosswalk x JOIN election e ON e.election_id = x.election_id "
        "JOIN result_booth_meta m ON m.election_id = x.election_id AND m.ps_number = x.ps_number "
        "WHERE x.ac_id = %s AND x.match_method = 'anchor' AND x.election_id <> %s",
        (ac_id, election_id))
    rows = cur.fetchall()
    return ({r["ps_number"]: r["booth_uid"] for r in rows},
            {r["ps_number"]: r["polled"] for r in rows})


def load_table(path: Path, ac_number: int, election: str, dry_run: bool = False,
               create_booths: bool = False) -> dict:
    """Check and (unless dry_run) load one workbook. Raises LoadRefused on any failure."""
    from common.db import connection
    from ingest.parse_form20 import Form20Document, Form20Row, load, nota_candidate_id

    table = form20_tables.read(path)
    issues = form20_tables.problems(table)
    if issues:
        raise LoadRefused(f"{path.name} fails its integrity checks: " + "; ".join(issues[:10]))

    stations = sorted(b.ps_number for b in table.booths)
    with connection() as conn, conn.cursor() as cur:
        scope = resolve_election(cur, election, ac_number)
        seeded = _seeded_candidates(cur, scope.election_id)
        unmatched = [c for c in table.candidates if form20_tables.name_key(c) not in seeded]
        if unmatched:
            raise LoadRefused(
                f"{len(unmatched)} candidate(s) are not seeded for {scope}: {unmatched}. "
                "Run python scripts/build_form20_seeds.py and python -m db.seed.load_seed.")
        candidate_ids = [seeded[form20_tables.name_key(c)]["candidate_id"]
                         for c in table.candidates]

        # The published gate: booth sum + postal = published total, per candidate.
        published = _published(cur, scope.election_id)
        sums = dict(zip(candidate_ids, (sum(b.votes[i] for b in table.booths)
                                        for i in range(len(table.candidates))), strict=True))
        mismatches = []
        for name, cid in zip(table.candidates, candidate_ids, strict=True):
            pub = published.get(cid, {})
            if "votes" in pub and sums[cid] + pub.get("postal", 0) != pub["votes"]:
                mismatches.append(f"{name}: booths {sums[cid]} + postal {pub.get('postal', 0)} "
                                  f"!= published {pub['votes']}")
        if mismatches:
            raise LoadRefused("published totals do not reconcile: " + "; ".join(mismatches))

        cur.execute("SELECT COUNT(*) AS n FROM booth WHERE ac_id = %s", (scope.ac_id,))
        booths_exist = cur.fetchone()["n"] > 0
        cur.execute("SELECT COUNT(*) AS n FROM booth_crosswalk WHERE election_id = %s",
                    (scope.election_id,))
        has_crosswalk = cur.fetchone()["n"] > 0

        evidence = None
        if not has_crosswalk and booths_exist:
            anchor_uid, anchor_polled = _anchor_totals(cur, scope.ac_id, scope.election_id)
            if not anchor_uid:
                raise LoadRefused(f"AC {ac_number} has booths but no anchored election with "
                                  "results to map these stations onto.")
            mine = {b.ps_number: b.valid + b.nota + b.rejected for b in table.booths}
            evidence = numbering_evidence(mine, anchor_polled)
            if not evidence["stable"]:
                raise LoadRefused(
                    f"station numbering does not look stable against the anchor election "
                    f"(same-number r = {evidence['same_ps_r']}, best offset r = "
                    f"{evidence['best_offset_r']}); load the polling-station list and use "
                    "ingest.crosswalk instead.")
            missing = [p for p in stations if p not in anchor_uid]
            if missing:
                raise LoadRefused(f"stations with no anchored booth: {missing[:10]}")
        elif not has_crosswalk and not create_booths:
            raise LoadRefused(f"AC {ac_number} has no booths. Pass --create-booths to create "
                              "placeholder booths from the station numbers.")

        summary = {
            "file": path.name, "sha256": table.sha256, "election": scope.label,
            "stations": len(stations), "candidates": len(table.candidates),
            "evm_valid": table.evm.valid + table.evm.nota,
            "valid_incl_postal": table.polled.valid + table.polled.nota,
            "votes_polled": table.polled.total, "postal": table.postal.valid + table.postal.nota,
            "crosswalk": ("existing" if has_crosswalk else
                          "anchor (new booths)" if not booths_exist else "same station number"),
            "numbering_evidence": evidence,
        }
        if dry_run:
            conn.rollback()
            summary["written"] = False
            return summary

        # -- writes, all in this transaction ---------------------------------
        if not has_crosswalk and not booths_exist:
            _create_booths(cur, scope.ac_id, ac_number, scope.election_id, stations)
        elif not has_crosswalk:
            note = (f"PS numbering stable vs the anchor election: booth size r = "
                    f"{evidence['same_ps_r']} at the same number, {evidence['best_offset_r']} "
                    f"at the best +-1/2 offset ({evidence['stations_compared']} stations). "
                    f"Mapped by number by ingest/load_form20_tables.py.")
            cur.executemany(
                "INSERT INTO booth_crosswalk (election_id, ac_id, ps_number, booth_uid, "
                "match_method, confidence, reviewed) VALUES (%s, %s, %s, %s, 'exact', %s, false)",
                [(scope.election_id, scope.ac_id, ps, anchor_uid[ps], SAME_PS_CONFIDENCE)
                 for ps in stations])
            cur.executemany(
                "INSERT INTO crosswalk_audit (ac_id, election_id, ps_number, action, "
                "new_booth_uid, confidence, note) VALUES (%s, %s, %s, 'accept', %s, %s, %s)",
                [(scope.ac_id, scope.election_id, ps, anchor_uid[ps], SAME_PS_CONFIDENCE, note)
                 for ps in stations])

        cur.execute("DELETE FROM ps_list_entry WHERE election_id = %s", (scope.election_id,))
        cur.executemany("INSERT INTO ps_list_entry (election_id, ac_id, ps_number, source_doc) "
                        "VALUES (%s, %s, %s, %s)",
                        [(scope.election_id, scope.ac_id, ps, path.name) for ps in stations])

        year = scope.label.split("-")[-1]
        cur.execute(
            "INSERT INTO source_doc (kind, filename, sha256, bytes, pages, parse_status, "
            "election_id, ac_id, storage_backend, storage_key, is_synthetic, parsed_at, "
            "status_changed_at, status_changed_by, note) "
            "VALUES ('form20', %s, %s, %s, %s, 'loaded', %s, %s, 'local', %s, false, now(), now(), "
            "'ingest.load_form20_tables', %s) "
            "ON CONFLICT (sha256) DO UPDATE SET parse_status = 'loaded', is_synthetic = false, "
            "election_id = EXCLUDED.election_id, ac_id = EXCLUDED.ac_id, parsed_at = now(), "
            "status_changed_at = now(), status_changed_by = EXCLUDED.status_changed_by",
            (path.name, table.sha256, path.stat().st_size, table.pages, scope.election_id,
             scope.ac_id, f"form20/{year}-VS/{ac_number}/{path.name}",
             "ECI Form 20, extracted tables (one sheet per printed page)"))

        doc = Form20Document(source_doc=path.name, candidate_columns=table.candidates,
                             tail_order=["total_valid", "rejected", "nota", "total", "tendered"])
        for b in table.booths:
            doc.rows.append(Form20Row(
                ps_number=b.ps_number, votes=b.votes,
                total_valid=b.valid + b.nota,          # this project's valid: NOTA included
                rejected=b.rejected, nota=b.nota, total=b.total, tendered=b.tendered,
                page_no=b.page))
        nota_id = nota_candidate_id(scope.election_id, scope.ac_id, cur)
        load(doc, scope.election_id, scope.ac_id, candidate_ids, nota_id, replace=True, cur=cur)

        # Declared result: winner flag and votes polled.
        totals = table.published_votes()
        winner = max(totals, key=totals.get)
        cur.execute("UPDATE candidate SET is_winner = (candidate_id = %s) WHERE election_id = %s",
                    (seeded[form20_tables.name_key(winner)]["candidate_id"], scope.election_id))
        cur.execute("UPDATE election SET votes_polled_published = %s WHERE election_id = %s",
                    (table.polled.total, scope.election_id))

    summary["written"] = True
    return summary


def seed_loads() -> list[tuple[Path, int, str, bool]]:
    """Every Form 20 workbook in db/seed/form20/, in load order.

    (workbook, AC number, election, create booths). Per AC the VS-2024 file
    comes first and creates the booths; the others map onto them by station
    number (checked by `numbering_evidence`).
    """
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    from build_form20_seeds import files

    order = []
    for ac, _name, label, path in files():
        order.append((path, int(ac), label, label == "VS-2024"))
    return sorted(order, key=lambda t: (t[1], not t[3], t[2]))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Load a Form 20 delivered as extracted tables (xlsx)")
    ap.add_argument("xlsx", nargs="?", help="workbook, one sheet per Form 20 page")
    ap.add_argument("--all-seed", action="store_true",
                    help="load every workbook in db/seed/form20/ (seed_loads order); a refused "
                         "file is reported and skipped, and fails the run only with --strict")
    ap.add_argument("--strict", action="store_true", help="with --all-seed: exit 1 on any refusal")
    ap.add_argument("--ac", type=int, help="AC number, e.g. 32")
    ap.add_argument("--election", help="election label, e.g. VS-2024")
    ap.add_argument("--create-booths", action="store_true",
                    help="create placeholder booths from station numbers (AC with no booths)")
    ap.add_argument("--dry-run", action="store_true", help="check everything, write nothing")
    args = ap.parse_args(argv)
    setup_logging()
    if args.all_seed:
        failed = 0
        for path, ac, label, create in seed_loads():
            if args.ac and ac != args.ac:
                continue
            try:
                summary = load_table(path, ac, label, args.dry_run, create)
                log.info("%s AC %s %s: %s stations, crosswalk %s", path.name, ac, label,
                         summary["stations"], summary["crosswalk"])
            except LoadRefused as exc:
                # e.g. Silli LS-2024: a station was added between the LS and VS
                # polls, so its numbers do not line up with the VS-2024 booths.
                log.warning("not loaded %s: %s", path.name, exc)
                failed += 1
        return 1 if failed and args.strict else 0
    if not (args.xlsx and args.ac and args.election):
        ap.error("give a workbook with --ac and --election, or --all-seed")
    try:
        summary = load_table(Path(args.xlsx), args.ac, args.election, args.dry_run,
                             args.create_booths)
    except LoadRefused as exc:
        log.error("refused: %s", exc)
        return 1
    for key, value in summary.items():
        log.info("%-20s %s", key, value)
    return 0


if __name__ == "__main__":
    sys.exit(main())
