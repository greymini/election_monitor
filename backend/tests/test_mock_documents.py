"""ingest/mock_documents.py: the generator's own invariants.

Two of these matter more than the rest:

  * **apportionment is exact.** The Form 20 gate compares each column's booth
    sum with the published AC total at tolerance zero (LLD 4.2). If the
    generator is off by a vote, the stack cannot build and the gate takes the
    blame for a generator bug.
  * **a generated roll line is one the roll parser reads.** `parse_roll`'s
    regexes anchor on `Name:`, `Age:` and `Gender:` with specific lookaheads,
    and a PDF text layer does not promise to preserve runs of spaces, so the
    line shape is load-bearing rather than cosmetic.
"""

from __future__ import annotations

import pytest

from ingest import mock_documents as mock
from ingest.parse_roll import scan_mother_roll, scan_supplement

# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

def test_unit_is_stable_and_in_range():
    assert mock.unit("a", 1) == mock.unit("a", 1)
    assert mock.unit("a", 1) != mock.unit("a", 2)
    for parts in (("x",), ("y", 2), (32, "VS-2024", 7)):
        assert 0.0 <= mock.unit(*parts) < 1.0


def test_pick_is_stable_and_within_the_list():
    items = list("abcdef")
    assert mock.pick(items, "seed", 3) == mock.pick(items, "seed", 3)
    assert all(mock.pick(items, "s", i) in items for i in range(50))


# ---------------------------------------------------------------------------
# Apportionment - the Form 20 gate depends on this being exact
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("total", [0, 1, 7, 2004, 94042, 304898])
@pytest.mark.parametrize("n", [1, 2, 3, 17, 120, 391])
def test_apportion_sums_to_the_total_exactly(total, n):
    weights = [1.0 + mock.unit("w", total, n, i) for i in range(n)]
    parts = mock.apportion(total, weights)
    assert len(parts) == n
    assert sum(parts) == total
    assert all(p >= 0 for p in parts)


def test_apportion_respects_the_weights():
    parts = mock.apportion(1000, [1.0, 9.0])
    assert parts == [100, 900]


def test_apportion_handles_degenerate_weights_without_losing_votes():
    # All-zero or negative weights must still distribute every vote: dropping
    # them would make the generator produce a document its own loader refuses.
    assert sum(mock.apportion(101, [0.0, 0.0, 0.0])) == 101
    assert sum(mock.apportion(101, [-1.0, -1.0])) == 101


def test_apportion_of_no_columns_is_empty():
    assert mock.apportion(500, []) == []


# ---------------------------------------------------------------------------
# Stations
# ---------------------------------------------------------------------------

BLOCKS = [
    {"block_id": 3201, "name_en": "Giridih Municipal Corporation", "kind": "ulb"},
    {"block_id": 3202, "name_en": "Giridih Block", "kind": "rural"},
    {"block_id": 3203, "name_en": "Pirtand Block", "kind": "rural"},
]
WARDS = [f"Ward {i}" for i in range(1, 37)]


def build(count=36):
    return mock.build_stations(32, BLOCKS, WARDS, count, seed=7)


def test_stations_are_numbered_from_one_without_gaps():
    stations = build(36)
    assert [s.ps_number for s in stations] == list(range(1, 37))


def test_stations_are_spread_across_every_block():
    stations = build(36)
    per_block = {b["block_id"]: 0 for b in BLOCKS}
    for s in stations:
        per_block[s.block_id] += 1
    assert all(n > 0 for n in per_block.values()), per_block
    assert sum(per_block.values()) == 36


def test_urban_stations_use_a_seeded_ward_so_the_area_alias_resolves():
    urban = [s for s in build(36) if s.block_id == 3201]
    assert urban
    assert all(s.area in WARDS for s in urban)


def test_rural_stations_use_an_obviously_invented_panchayat():
    rural = [s for s in build(36) if s.block_id != 3201]
    assert rural
    for s in rural:
        assert s.area.endswith(" Panchayat")
        assert any(s.area.startswith(stem) for stem in mock.PLACE_STEMS)


def test_an_ac_with_no_blocks_is_refused_rather_than_guessed():
    with pytest.raises(ValueError, match="no blocks seeded"):
        mock.build_stations(99, [], [], 10, seed=1)


# ---------------------------------------------------------------------------
# Churn
# ---------------------------------------------------------------------------

def test_churn_keeps_the_station_count_and_the_numbering():
    stations = build(36)
    older = mock.churn_stations(stations, "VS-2019", seed=7, renamed=4, replaced=2)
    assert len(older) == len(stations)
    assert [s.ps_number for s in older] == list(range(1, 37))


def test_churn_renumbers_so_the_match_cannot_come_from_the_number():
    stations = build(36)
    older = mock.churn_stations(stations, "VS-2019", seed=7, renamed=0, replaced=0)
    before = {s.ps_number: s.building for s in stations}
    moved = sum(1 for s in older if before[s.ps_number] != s.building)
    assert moved > len(stations) // 2, "reversing within a block should move most stations"


def test_churn_changes_exactly_the_requested_number_of_places():
    stations = build(36)
    older = mock.churn_stations(stations, "VS-2019", seed=7, renamed=4, replaced=2)
    original_buildings = {s.building for s in stations}
    original_villages = {s.village for s in stations}
    # The replaced ones get a village nothing else has; the renamed ones keep
    # their village and change only the building kind.
    replaced = [s for s in older if s.village not in original_villages]
    assert len(replaced) == 2
    changed_building = [s for s in older if s.building not in original_buildings]
    assert len(changed_building) == 6


def test_churn_does_not_mutate_the_anchor_list():
    stations = build(36)
    snapshot = [(s.ps_number, s.building, s.village) for s in stations]
    mock.churn_stations(stations, "VS-2019", seed=7, renamed=4, replaced=2)
    assert [(s.ps_number, s.building, s.village) for s in stations] == snapshot


def test_churn_is_deterministic():
    stations = build(36)
    a = mock.churn_stations(stations, "VS-2019", seed=7, renamed=4, replaced=2)
    b = mock.churn_stations(stations, "VS-2019", seed=7, renamed=4, replaced=2)
    assert [(s.ps_number, s.building, s.village) for s in a] == \
           [(s.ps_number, s.building, s.village) for s in b]


# ---------------------------------------------------------------------------
# Election plan
# ---------------------------------------------------------------------------

CONTESTANTS = [
    mock.Contestant("Sudivya Kumar", 94042, True),
    mock.Contestant("Nirbhay Kumar Shahabadi", 90204, True),
    mock.Contestant("Navin Anand", 10787, True),
    mock.Contestant("Rajesh Mandal (IND)", None, False),
]


def test_every_seeded_column_sums_to_its_published_total():
    stations = build(60)
    plan = mock.plan_election("VS-2024", CONTESTANTS, 2004, stations, seed=7, ac_number=32)
    for index, c in enumerate(CONTESTANTS):
        if c.published_votes is None:
            continue
        got = sum(plan.rows[s.ps_number]["votes"][index] for s in stations)
        assert got == c.published_votes, c.header


def test_nota_sums_to_its_published_total():
    stations = build(60)
    plan = mock.plan_election("VS-2024", CONTESTANTS, 2004, stations, seed=7, ac_number=32)
    assert sum(plan.rows[s.ps_number]["nota"] for s in stations) == 2004


def test_each_row_satisfies_the_arithmetic_the_parser_validates():
    """candidate votes + NOTA == printed valid total, for every row.

    This is `parse_form20.validate`'s per-row check. A generator that fails it
    produces a document that goes to the review queue instead of loading.
    """
    stations = build(60)
    plan = mock.plan_election("VS-2024", CONTESTANTS, 2004, stations, seed=7, ac_number=32)
    for s in stations:
        row = plan.rows[s.ps_number]
        assert sum(row["votes"]) + row["nota"] == row["total_valid"]
        assert row["total_valid"] + row["rejected"] == row["total"]
        assert row["rejected"] >= 0


def test_some_booths_have_zero_rejected_votes_and_some_do_not():
    # A column that is never zero would hide a NULL-versus-zero mistake, and one
    # that is always zero would hide the arithmetic.
    stations = build(60)
    plan = mock.plan_election("VS-2024", CONTESTANTS, 2004, stations, seed=7, ac_number=32)
    rejected = [plan.rows[s.ps_number]["rejected"] for s in stations]
    assert any(r == 0 for r in rejected) and any(r > 0 for r in rejected)


def test_the_margin_varies_across_booths():
    """A flat generator would make the map and the diverging ramp meaningless."""
    stations = build(60)
    plan = mock.plan_election("VS-2024", CONTESTANTS, 2004, stations, seed=7, ac_number=32)
    margins = []
    for s in stations:
        votes = plan.rows[s.ps_number]["votes"]
        top, second = sorted(votes, reverse=True)[:2]
        margins.append((top - second) / max(plan.rows[s.ps_number]["total_valid"], 1))
    assert max(margins) - min(margins) > 0.05


def test_both_contest_parties_win_somewhere():
    """Otherwise signed_margin_pct only ever takes one sign and F1 is untested."""
    stations = build(60)
    plan = mock.plan_election("VS-2024", CONTESTANTS, 2004, stations, seed=7, ac_number=32)
    winners = {plan.rows[s.ps_number]["votes"].index(max(plan.rows[s.ps_number]["votes"]))
               for s in stations}
    assert {0, 1} <= winners


# ---------------------------------------------------------------------------
# Roll lines - must be readable by the real roll parser
# ---------------------------------------------------------------------------

def test_a_generated_elector_line_is_counted_by_the_roll_parser():
    lines = [mock._elector_line(i, 7, "VS-2024-MOTHER", 12) for i in range(20)]
    text = "Polling Station No. 12   Part No. 12\n" + "\n".join(lines)
    counts = scan_mother_roll(text)
    assert counts.ps_number == 12
    assert counts.roll_part == 12
    assert counts.electors == 20
    assert counts.male + counts.female + counts.other == 20
    bands = [counts.age_18_19, counts.age_20_29, counts.age_30_39,
             counts.age_40_49, counts.age_50_59, counts.age_60p]
    assert sum(bands) == 20, "every elector must land in exactly one age band"


def test_a_relatives_surname_is_not_counted_as_an_elector():
    """C9's neighbour: `Father Name:` contains `Name:`.

    `RELATIVE_RE` strips the relative's span before `NAME_RE` reads the elector,
    so the surname histogram must hold one entry per elector, not two.
    """
    lines = [mock._elector_line(i, 7, "R", 5) for i in range(10)]
    counts = scan_mother_roll("Polling Station No. 5\n" + "\n".join(lines))
    assert sum(counts.surnames.values()) == 10


def test_generated_surnames_are_all_in_the_seeded_dictionary():
    # The caste estimator keys on the surname dictionary, so a generator using
    # names outside it would silently produce zero coverage.
    import csv
    from pathlib import Path

    seed_file = Path(__file__).resolve().parents[1] / "db" / "seed" / "surname_dict.csv"
    with seed_file.open(encoding="utf-8-sig") as fh:
        known = {row["surname_en"] for row in csv.DictReader(fh) if row["surname_en"]}
    assert set(mock.SURNAMES) <= known, set(mock.SURNAMES) - known


def test_epic_numbers_use_a_prefix_the_eci_does_not_issue():
    line = mock._elector_line(3, 7, "VS-2024-MOTHER", 12)
    assert mock.EPIC_PREFIX in line
    assert mock.EPIC_PREFIX == "ZZZ"


def test_a_supplement_section_is_read_as_additions_deletions_and_modifications():
    body = ["ADDITIONS"]
    body += [mock._elector_line(k, 7, "S-add", 9) for k in range(5)]
    body.append("DELETIONS")
    body += [mock._elector_line(k, 7, "S-del", 9) + " Reason: DEAD" for k in range(3)]
    body.append("MODIFICATIONS")
    body += [mock._elector_line(k, 7, "S-mod", 9) for k in range(2)]
    counts = scan_supplement("Polling Station No. 9   Part No. 9\n" + "\n".join(body))
    assert (counts.additions, counts.deletions, counts.modifications) == (5, 3, 2)
    assert counts.del_death == 3
