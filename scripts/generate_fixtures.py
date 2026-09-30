#!/usr/bin/env python
"""Emit web/src/fixtures/generated.ts from fixtures/giridih.py.

One source, two consumers: the frontend imports the generated TypeScript, and
`tests/metric_cases.py` imports the Python. Finding N8 was the two of them
disagreeing about Giridih's valid votes with no test able to notice, which is
what happens to any two fixtures kept in step by intention.

    python scripts/generate_fixtures.py          # write it
    python scripts/generate_fixtures.py --check  # fail if it is out of date

`tests/test_fixture_parity.py` runs the `--check` form, so editing the Python and
forgetting to regenerate fails the suite rather than drifting - the same
arrangement as `analytics/metric_sql.py` and the metrics migration.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from fixtures import giridih  # noqa: E402

TARGET = (
    pathlib.Path(__file__).resolve().parents[1]
    / "web" / "src" / "fixtures" / "generated.ts"
)

HEADER = """/* GENERATED FROM fixtures/giridih.py - DO NOT EDIT BY HAND
 *
 * Regenerate with:  python scripts/generate_fixtures.py
 *
 * The Python module is the single source, shared with tests/metric_cases.py so
 * the frontend fixture and the metric tests cannot disagree about the same
 * constituency. They did: 207,598 against 207,459 valid votes, with neither
 * wrong enough to fail a test because both round the margin to the published
 * 1.85%. That was finding N8.
 *
 * Only the AC totals are real. Every per-booth number is invented,
 * deterministically, to add up to them - no Form 20 has been parsed, so a
 * booth-level figure here is not evidence of anything. The rural area names are
 * synthetic and say so: db/seed/areas_panchayats.csv is header-only for all six
 * ACs, so the real panchayat names are not known to this system (N9).
 */

"""


def ts(value: object) -> str:
    """A TypeScript literal. `json.dumps` is valid TS for these shapes, and
    `None` becoming `null` is exactly what is wanted."""
    return json.dumps(value, ensure_ascii=False)


def render() -> str:
    rows = giridih.booths_with_priority()
    prev = giridih.booths_2019()
    totals = giridih.ac_totals()
    totals_2019 = giridih.ac_totals_2019()
    area_rows = [
        {
            "area_en": a.area_en,
            "area_hi": a.area_hi,
            "kind": a.kind,
            "block_en": a.block_en,
            "block_hi": a.block_hi,
            "real": a.real,
        }
        for a in giridih.areas()
    ]

    parts = [HEADER]

    parts.append("export interface GeneratedBooth {\n")
    for name, type_ in [
        ("booth_uid", "string"), ("ps_numbers", "string"),
        ("area_en", "string"), ("area_hi", "string"), ("block_en", "string"),
        ("building", "string"),
        ("lat", "number | null"), ("lon", "number | null"),
        ("electors", "number"),
        ("jmm", "number"), ("bjp", "number"), ("jlkm", "number"),
        ("others", "number"), ("nota", "number"), ("rejected", "number"),
        ("source_page", "number"),
        ("crosswalk_confidence", "number | null"),
        ("crosswalk_reviewed", "boolean"),
        ("lineage_kind", "string | null"),
        ("additions", "number"),
        ("floating_pct", "number | null"),
        ("margin_stddev", "number | null"),
        # Derived by analytics/metrics.py at generation time, not recomputed per
        # endpoint. The map tooltip and the booth card read these same stored
        # fields, so they cannot disagree about one booth.
        ("valid_votes", "number"),
        ("votes_polled", "number"),
        ("turnout_pct", "number | null"),
        ("winner_party", "string | null"),
        ("runner_party", "string | null"),
        ("margin_votes", "number | null"),
        ("margin_pct", "number | null"),
        ("signed_margin_pct", "number | null"),
        ("jmm_share_pct", "number | null"),
        ("jmm_swing_pct", "number | null"),
        ("new_voter_pct", "number | null"),
        # From analytics.metrics.priority_score over percentile ranks
        # computed within this AC, as mv_booth_priority does. It was absent
        # entirely, so the Overview panel that ranks by it rendered empty.
        ("priority_score", "number | null"),
        ("priority_inputs_used", "string[]"),
        ("priority_weight_used", "number"),
    ]:
        parts.append(f"  {name}: {type_}\n")
    parts.append("}\n\n")

    parts.append(
        "/** The published Giridih 2024 constituency totals. The only real\n"
        " *  numbers in this file; still unverified against a document. */\n"
        f"export const AC_TOTALS = {ts(totals)} as const\n\n"
    )
    parts.append(f"export const AC_TOTALS_2019 = {ts(totals_2019)} as const\n\n")

    parts.append(
        "/** Blocks and areas. `real: false` marks the synthetic rural areas. */\n"
        f"export const AREAS = {ts(area_rows)} as const\n\n"
    )

    parts.append(f"export const BOOTH_COUNT = {len(rows)}\n\n")
    parts.append("export const GENERATED_BOOTHS: GeneratedBooth[] = [\n")
    for row in rows:
        parts.append(f"  {ts(row)},\n")
    parts.append("]\n\n")

    parts.append(
        "/** Prior-election votes, keyed by the 2024 booth_uid. Deliberately\n"
        " *  absent for split booths and a scatter of others, so every reason a\n"
        " *  swing is withheld has a booth that demonstrates it. */\n"
        "export const GENERATED_BOOTHS_2019: Record<string, "
        "{ jmm: number; bjp: number; jvm: number; nota: number }> =\n"
        f"  {ts(prev)}\n"
    )
    return "".join(parts)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true",
                    help="exit non-zero if the generated file is out of date")
    args = ap.parse_args(argv)

    report = giridih.check()
    if report["problems"]:
        print("the fixture does not reconcile:", file=sys.stderr)
        for problem in report["problems"]:
            print(f"  {problem}", file=sys.stderr)
        return 2

    wanted = render()

    if args.check:
        current = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""
        if current != wanted:
            print(
                f"{TARGET.relative_to(TARGET.parents[3])} is out of date. "
                "Run: python scripts/generate_fixtures.py",
                file=sys.stderr,
            )
            return 1
        print("generated fixtures are current")
        return 0

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(wanted, encoding="utf-8", newline="\n")
    print(
        f"wrote {TARGET.name}: {report['booths']} booths across "
        f"{report['areas']} areas, electors {report['electors_min']}-"
        f"{report['electors_max']} (mean {report['electors_mean']}), "
        f"{report['ungeocoded']} ungeocoded, {report['weak_crosswalk']} weakly "
        f"crosswalked, {report['split']} split"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
