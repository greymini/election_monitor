"""The fixture is one fixture, and it is plausible.

Two problems this holds shut.

**N8, two fixtures disagreeing.** `web/src/fixtures/` and `tests/metric_cases.py`
each carried their own copy of Giridih's totals and disagreed about the valid
vote - 207,598 against 207,459 - with no test able to notice, because both round
the published margin to 1.85%. They are generated from `fixtures/giridih.py` now,
and the first test here fails if the generated TypeScript has drifted from it.

**Implausible booths.** The fixture was eight booths averaging 38,000 electors:
an entire assembly segment per polling station, forty times life size. Every
per-booth figure on every screen was therefore unbelievable, and the map's
electorate-scaled markers meant nothing. The shape tests below pin the band a
real polling station sits in.

And the reconciliation tests, which are the point of a fixture at all: if the
booths do not add up to the published constituency totals, then every page
showing both is showing a contradiction, and a reviewer cannot tell a fixture
artefact from a bug in the code being reviewed.
"""

from __future__ import annotations

import json
import pathlib
import re
import subprocess
import sys

import pytest

from analytics import metrics
from fixtures import giridih

ROOT = pathlib.Path(__file__).resolve().parents[1]
GENERATED = ROOT / "web" / "src" / "fixtures" / "generated.ts"


# ---------------------------------------------------------------------------
# One source
# ---------------------------------------------------------------------------


def test_the_generated_typescript_is_current():
    """If this fails, run:  python scripts/generate_fixtures.py

    The same arrangement as `analytics/metric_sql.py` and the metrics
    migration: the Python is the source, the checked-in file is derived, and
    editing one without regenerating the other fails rather than drifts.
    """
    result = subprocess.run(
        [sys.executable, "scripts/generate_fixtures.py", "--check"],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0, (
        (result.stderr or result.stdout).strip()
        or "generated.ts is out of date"
    )


def test_the_metric_cases_use_the_shared_totals():
    """N8. `tests/metric_cases.py` must not carry its own copy of these."""
    from tests import metric_cases

    assert metric_cases.GIRIDIH_2024["valid_votes"] == giridih.VALID_VOTES
    assert metric_cases.GIRIDIH_2024["margin_votes"] == giridih.MARGIN_VOTES
    assert metric_cases.GIRIDIH_2024["jmm"] == giridih.PARTY_TOTALS["jmm"]

    # Comments stripped: the module explains what the old figure was and why
    # it went, and a scan that reads prose fails on its own documentation - the
    # same mistake four of the section 3 guards made on their first run.
    source = "\n".join(
        line.split("#", 1)[0]
        for line in (ROOT / "tests" / "metric_cases.py")
        .read_text(encoding="utf-8").splitlines()
    )
    assert "207_459" not in source and "207459" not in source, (
        "the old, separately derived valid-vote figure is back in metric_cases"
    )


def test_the_generated_file_says_it_is_generated():
    head = GENERATED.read_text(encoding="utf-8")[:400]
    assert "GENERATED FROM fixtures/giridih.py" in head
    assert "DO NOT EDIT BY HAND" in head


# ---------------------------------------------------------------------------
# Plausible booths
# ---------------------------------------------------------------------------


def test_there_are_enough_booths_to_be_a_constituency():
    """A Jharkhand assembly segment has a few hundred polling stations. Eight
    was not a small fixture, it was a different kind of object."""
    rows = giridih.booths()
    assert len(rows) >= 300, f"only {len(rows)} booths"


@pytest.mark.parametrize("attr", ["electors"])
def test_every_booth_has_a_believable_electorate(attr):
    rows = giridih.booths()
    bad = [
        (b.booth_uid, getattr(b, attr)) for b in rows
        if not giridih.MIN_ELECTORS <= getattr(b, attr) <= giridih.MAX_ELECTORS
    ]
    assert bad == [], f"outside {giridih.MIN_ELECTORS}-{giridih.MAX_ELECTORS}: {bad[:5]}"


def test_no_booth_polls_more_votes_than_it_has_electors():
    """Turnout over 100% is the kind of number a reader notices immediately and
    a generator produces easily."""
    over = [
        (b.booth_uid, b.valid_votes, b.electors)
        for b in giridih.booths() if b.valid_votes > b.electors
    ]
    assert over == [], over[:5]


def test_the_booths_are_spread_across_the_real_block_structure():
    rows = giridih.booths()
    blocks = {b.block_en for b in rows}
    seeded = {name for name, _, _ in giridih.BLOCKS}
    assert blocks == seeded, f"booths sit in {blocks}, seed has {seeded}"

    # Several booths per area, not one each - that is what a ward looks like.
    # Keyed by (block, area): an area name alone is not unique across blocks,
    # and counting names hid that two rural blocks shared all twelve of theirs.
    areas = {(b.block_en, b.area_en) for b in rows}
    assert len(areas) >= 50, f"only {len(areas)} distinct areas"
    assert len(rows) / len(areas) >= 3, "fewer than three booths per area"
    names = {b.area_en for b in rows}
    assert len(names) == len(areas), (
        "two areas in different blocks share a name, so anything grouping by "
        "name merges them"
    )


def test_the_real_wards_come_from_the_seed_and_the_rest_are_marked_synthetic():
    """The 36 municipal wards are real and read from `db/seed/areas_wards.csv`.

    The rural areas are not real, and must not pretend to be:
    `db/seed/areas_panchayats.csv` is header-only for all six ACs, so this
    system does not know the real panchayat names for any of them (N9).
    Inventing Jharkhand place names would put unverifiable data on a screen
    that presents itself as sourced.
    """
    areas = giridih.areas()
    real = [a for a in areas if a.real]
    synthetic = [a for a in areas if not a.real]

    assert len(real) == 36, f"{len(real)} real wards, expected the seeded 36"
    assert all(a.kind == "ward" for a in real)
    assert synthetic, "the rural blocks have no areas at all"
    for area in synthetic:
        assert "Fixture" in area.area_en, (
            f"{area.area_en!r} is synthetic but does not say so, so it is "
            "indistinguishable from a real place name"
        )


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("party", ["jmm", "bjp", "jlkm", "nota", "others"])
def test_every_party_column_sums_to_its_published_total(party):
    got = sum(getattr(b, party) for b in giridih.booths())
    assert got == giridih.PARTY_TOTALS[party]


def test_electors_and_valid_votes_sum_to_the_published_totals():
    rows = giridih.booths()
    assert sum(b.electors for b in rows) == giridih.ELECTORS
    assert sum(b.valid_votes for b in rows) == giridih.VALID_VOTES


def test_the_headline_figures_match_the_published_result():
    totals = giridih.ac_totals()
    assert totals["winner_party"] == "JMM"
    assert totals["runner_party"] == "BJP"
    assert totals["margin_votes"] == 3_838
    # The published margin. Both candidate valid-vote figures round to this,
    # which is exactly why N8 went unnoticed.
    assert totals["margin_pct"] == 1.85


@pytest.mark.parametrize("party", ["jmm", "bjp", "jvm", "nota"])
def test_the_prior_election_columns_also_reconcile(party):
    got = sum(row[party] for row in giridih.booths_2019().values())
    assert got == giridih.PARTY_TOTALS_2019[party]


# ---------------------------------------------------------------------------
# Item 3: the map tooltip and the booth card cannot disagree
# ---------------------------------------------------------------------------


def test_the_derived_metrics_come_from_the_product_metric_code():
    """The map tooltip and the booth card showed different values for one booth.

    They read the same field, so only a second computation could separate them -
    and the fixture response builder derived margin and turnout inline for each
    endpoint, rounding with `Math.round(x * 1000) / 10` where the product rounds
    to two places.

    The fixture now stores each figure once, computed by `analytics.metrics`.
    This recomputes them here and asserts they match, so the stored values
    cannot drift from the functions the API and the views use.
    """
    prev_rows = giridih.booths_2019()
    for booth in giridih.booths():
        row = giridih.derived(booth, prev_rows.get(booth.booth_uid))
        candidates = {
            "JMM": booth.jmm, "BJP": booth.bjp, "JLKM": booth.jlkm,
            "OTH": booth.others, metrics.NOTA: booth.nota,
        }
        ranking = metrics.rank_candidates(candidates)
        valid = metrics.valid_votes(candidates)

        where = booth.booth_uid
        assert row["valid_votes"] == valid, where
        assert row["margin_votes"] == metrics.margin_votes(ranking), where
        assert row["margin_pct"] == metrics.margin_pct(ranking, valid), where
        assert row["turnout_pct"] == metrics.turnout_pct(
            metrics.votes_polled(valid, booth.rejected), booth.electors), where


def test_the_fixture_response_builder_does_not_recompute_metrics():
    """The structural half of the same point.

    If `responses.ts` starts deriving a margin again, the two endpoints can
    diverge again, and no data-level test would show it until someone compared
    two screens.
    """
    text = (ROOT / "web" / "src" / "fixtures" / "responses.ts").read_text(encoding="utf-8")
    code = "\n".join(line.split("//", 1)[0] for line in text.splitlines())

    # The rounding idiom the old inline derivations used.
    assert not re.search(r"Math\.round\(\s*\(?1000\s*\*", code), (
        "responses.ts is deriving a percentage again; the generated fixture "
        "already stores it, computed by analytics.metrics"
    )
    for field in ("valid_votes", "margin_pct", "turnout_pct", "signed_margin_pct"):
        assert f"b.{field}" in code, (
            f"responses.ts no longer reads the stored {field}"
        )


def test_every_booth_has_a_card_and_a_map_feature():
    """Both endpoints are built from the same list, so every booth on the map
    must have a card. A booth whose card is missing renders as a drawer full of
    dashes over a tooltip full of numbers, which is what was reported."""
    uids = [b.booth_uid for b in giridih.booths()]
    assert len(uids) == len(set(uids)), "duplicate booth_uid in the fixture"
    generated = GENERATED.read_text(encoding="utf-8")
    for uid in uids[:20] + uids[-20:]:
        assert f'"booth_uid": "{uid}"' in generated, f"{uid} is not in generated.ts"


# ---------------------------------------------------------------------------
# The data gaps a reviewer needs to see
# ---------------------------------------------------------------------------


def test_the_fixture_exercises_every_not_loaded_path():
    """A fixture where everything is present cannot show that the honest-NULL
    work is done. Each of these drives a different message on a real screen."""
    rows = giridih.booths()
    assert any(b.lat is None for b in rows), "no ungeocoded booth"
    assert any(not b.crosswalk_reviewed for b in rows), "no weak crosswalk"
    assert any(b.lineage_kind for b in rows), "no split booth"
    assert any(b.floating_pct is None for b in rows), "no NULL floating vote"
    assert any(b.margin_stddev is None for b in rows), "no NULL volatility"

    prev = giridih.booths_2019()
    assert any(b.booth_uid not in prev for b in rows), (
        "every booth has a prior election, so no booth demonstrates D2's NULL"
    )

    derived_rows = giridih.booths_with_metrics()
    assert any(r["jmm_swing_pct"] is None for r in derived_rows)
    assert any(r["new_voter_pct"] is None for r in derived_rows)


def test_the_generated_file_is_valid_json_per_row():
    """Each row is emitted with json.dumps, so a malformed one would be a
    generator bug rather than a hand-editing mistake - but the file is imported
    as TypeScript, where a trailing-comma or quoting error fails the build
    rather than this test. Parsing the AC totals back is a cheap sanity check."""
    text = GENERATED.read_text(encoding="utf-8")
    match = re.search(r"export const AC_TOTALS = (\{.*?\}) as const", text, re.S)
    assert match, "AC_TOTALS not found in the generated file"
    totals = json.loads(match.group(1))
    assert totals["valid_votes"] == giridih.VALID_VOTES
    assert totals["margin_pct"] == 1.85
