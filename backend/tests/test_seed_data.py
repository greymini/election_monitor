"""The seed CSVs, checked without a database.

Two kinds of assertion here. The first is referential: every party, AC and event
a CSV names must exist in the CSV that defines it, because a typo otherwise
becomes a warning in a log nobody reads and a silently missing row.

The second is about honesty, and matters more. The spec is explicit that
constituency facts must not be invented: the five ACs beyond Giridih are seeded
from secondary sources, so they carry `verified=false`, and where the spec gives
a margin but no vote totals there must be no totals row at all. A margin is not
a total, and deriving one from the other would put a fabricated number into the
validation target that Form 20 loads are checked against.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

SEED = Path(__file__).resolve().parents[1] / "db" / "seed"

# The six constituencies of MULTI_AC_EXPANSION_SPEC 1.
EXPECTED_ACS = {32: "Giridih", 31: "Gandey", 33: "Dumri",
                42: "Tundi", 61: "Silli", 65: "Kanke"}


def rows(name: str) -> list[dict[str, str]]:
    with (SEED / name).open(encoding="utf-8-sig", newline="") as fh:
        return [r for r in csv.DictReader(fh) if any((v or "").strip() for v in r.values())]


@pytest.fixture(scope="module")
def parties() -> set[str]:
    return {r["abbr"] for r in rows("parties.csv")}


@pytest.fixture(scope="module")
def acs() -> set[str]:
    return {r["ac_number"] for r in rows("ac.csv")}


@pytest.fixture(scope="module")
def events() -> set[str]:
    return {r["label"] for r in rows("election_event.csv")}


# --------------------------------------------------------------------------
# The six constituencies
# --------------------------------------------------------------------------


def test_all_six_constituencies_are_seeded():
    seeded = {int(r["ac_number"]): r["name_en"] for r in rows("ac.csv")}
    assert seeded == EXPECTED_ACS


def test_only_giridih_is_verified():
    """The spec forbids inventing constituency facts. Giridih's numbers have
    been reconciled against the published 2024 result; the other five come from
    secondary sources and must be flagged until a human checks them."""
    by_number = {int(r["ac_number"]): r["verified"].strip().lower() for r in rows("ac.csv")}
    assert by_number[32] == "true"
    for number in EXPECTED_ACS:
        if number != 32:
            assert by_number[number] == "false", f"AC {number} must be seeded verified=false"


def test_every_ac_has_a_devanagari_name_and_a_reservation():
    for r in rows("ac.csv"):
        assert any("ऀ" <= ch <= "ॿ" for ch in r["name_hi"]), r
        assert r["reservation"] in {"GEN", "SC", "ST"}, r


def test_kanke_is_marked_reserved():
    """Spec 1 lists Kanke as an SC seat. Getting a reservation wrong changes who
    can stand, so it is worth pinning."""
    by_number = {int(r["ac_number"]): r["reservation"] for r in rows("ac.csv")}
    assert by_number[65] == "SC"
    assert by_number[32] == "GEN"


def test_dumri_spans_two_districts():
    """The reason ac_district exists rather than a district column on ac."""
    by_number = {int(r["ac_number"]): r["districts"] for r in rows("ac.csv")}
    assert "|" in by_number[33]
    assert set(by_number[33].split("|")) == {"Giridih", "Bokaro"}


def test_the_unverified_acs_say_what_needs_checking():
    """A `verified=false` flag with no note tells the operator nothing about
    what to check."""
    for r in rows("ac.csv"):
        if r["verified"].strip().lower() == "false":
            assert r["notes"].strip(), f"AC {r['ac_number']} is unverified with no note"
            assert "confirm" in r["notes"].lower() or "unverified" in r["notes"].lower()


# --------------------------------------------------------------------------
# No invented numbers
# --------------------------------------------------------------------------


def test_every_seeded_total_is_published():
    """No invented numbers: every vote row in ac_totals.csv comes from a Form 20
    workbook (scripts/build_form20_seeds.py) and every electors row names its CEO
    Jharkhand document. Before the Form 20s of the other five ACs were found,
    this asserted that Gandey, Tundi and Silli had no totals at all, because the
    spec gave only their margins."""
    for r in rows("ac_totals.csv"):
        if r["metric"] == "electors":
            assert "CEO Jharkhand" in r["source"] or "Form 20" in r["source"], r
        elif r["election_label"] != "VS-2014":
            assert r["source"].startswith("ECI Form 20"), r


def test_every_ac_has_its_vs2024_form20_totals():
    seeded = {(int(r["ac_number"]), r["election_label"]) for r in rows("ac_totals.csv")}
    for ac in (31, 32, 33, 42, 61, 65):
        assert (ac, "VS-2024") in seeded
        assert (ac, "LS-2024") in seeded


def test_the_known_giridih_figures_are_exact():
    """These are the numbers the whole Form 20 reconciliation turns on: the AC
    margin must come out at 1.85% against them."""
    got = {
        (r["election_label"], r["party_abbr"], r["metric"]): int(r["value"])
        for r in rows("ac_totals.csv") if r["ac_number"] == "32"
    }
    assert got[("VS-2024", "JMM", "votes")] == 94042
    assert got[("VS-2024", "BJP", "votes")] == 90204
    assert got[("VS-2024", "JLKM", "votes")] == 10787
    assert got[("VS-2024", "", "nota")] == 2004
    # Electors incl. 274 service voters, as printed in the Form 20 header: the
    # denominator that matches votes polled incl. postal ballots.
    assert got[("VS-2024", "", "electors")] == 305172

    # Published margin and the share it implies, as the audit reconstructed it:
    # 3,838 / 207,598 total valid votes including NOTA = 1.849%.
    margin = got[("VS-2024", "JMM", "votes")] - got[("VS-2024", "BJP", "votes")]
    assert margin == 3838
    valid = (got[("VS-2024", "JMM", "votes")] + got[("VS-2024", "BJP", "votes")]
             + got[("VS-2024", "JLKM", "votes")] + got[("VS-2024", "", "nota")])
    # Other candidates make up the rest of the 207,598; what matters here is
    # that NOTA is inside the denominator, not outside it.
    assert valid <= 207598
    assert round(100 * margin / 207598, 2) == 1.85


def test_kanke_margin_from_the_seeded_totals_matches_the_spec():
    got = {(r["party_abbr"], r["metric"]): int(r["value"])
           for r in rows("ac_totals.csv") if r["ac_number"] == "65"}
    assert got[("INC", "votes")] - got[("BJP", "votes")] == 968


def test_dumri_margin_from_the_seeded_totals_matches_the_spec():
    got = {(r["party_abbr"], r["metric"]): int(r["value"])
           for r in rows("ac_totals.csv") if r["ac_number"] == "33"}
    assert got[("JLKM", "votes")] - got[("JMM", "votes")] == 10945


# --------------------------------------------------------------------------
# Contest pairs
# --------------------------------------------------------------------------


def test_contest_pairs_are_not_all_jmm_bjp():
    """The point of ac_contest. Hardcoding JMM/BJP is audit D5: a Dumri scenario
    reported a JMM/BJP winner for a seat JLKM holds."""
    pairs = {(r["ac_number"], r["event_label"]): (r["party_a"], r["party_b"])
             for r in rows("ac_contest.csv")}
    assert pairs[("32", "VS-2024")] == ("JMM", "BJP")
    assert pairs[("33", "VS-2024")] == ("JLKM", "JMM")
    assert pairs[("61", "VS-2024")] == ("JMM", "AJSU")
    assert pairs[("65", "VS-2024")] == ("INC", "BJP")
    assert len({p for p in pairs.values()}) > 1


def test_every_ac_has_a_contest_pair_for_the_baseline_event():
    """Without one, the signed margin ramp has no sign and the map falls back to
    half a diverging scale - audit F1."""
    baseline = [r["label"] for r in rows("election_event.csv")
                if r["is_baseline"].strip().lower() == "true"]
    assert len(baseline) == 1, "exactly one event may be the baseline"
    have = {r["ac_number"] for r in rows("ac_contest.csv") if r["event_label"] == baseline[0]}
    assert have == {str(n) for n in EXPECTED_ACS}


def test_contest_parties_differ():
    for r in rows("ac_contest.csv"):
        assert r["party_a"] != r["party_b"], r


def test_giridih_2014_contest_is_the_right_way_round():
    """BJP held Giridih in 2014, so it is party_a that year and JMM in 2019/24.
    A signed margin with the pair reversed points the ramp the wrong way."""
    pairs = {(r["ac_number"], r["event_label"]): (r["party_a"], r["party_b"])
             for r in rows("ac_contest.csv")}
    assert pairs[("32", "VS-2014")] == ("BJP", "JMM")
    assert pairs[("32", "VS-2019")] == ("JMM", "BJP")


def test_a_contest_pair_with_an_unconfirmed_runner_up_says_so():
    """Spec 1 records only that JMM won Tundi. The runner-up is a guess, and a
    guess in a seed file has to be labelled."""
    tundi = [r for r in rows("ac_contest.csv") if r["ac_number"] == "42"]
    assert tundi, "Tundi needs a contest pair for the ramp to have a sign"
    assert "unverified" in tundi[0]["source"].lower()


# --------------------------------------------------------------------------
# Party aliases: the fix for C1
# --------------------------------------------------------------------------


def test_every_party_has_at_least_one_alias_in_each_script(parties):
    """A Form 20 header is Devanagari; an ECI result page is Latin. Both have to
    resolve, which is exactly what resolve_candidates could not do - it built
    its lookup from abbr and name_en only, never name_hi."""
    by_party: dict[str, set[str]] = {}
    for r in rows("party_alias.csv"):
        by_party.setdefault(r["party_abbr"], set()).add(r["script"])
    # UNK is a placeholder for "party not recorded in source" (db/seed/form20/),
    # not a party a Form 20 header can print, so nothing should resolve to it.
    for abbr in parties - {"UNK"}:
        assert abbr in by_party, f"{abbr} has no alias at all"
        assert "hi" in by_party[abbr], f"{abbr} has no Devanagari alias"
        assert "en" in by_party[abbr], f"{abbr} has no Latin alias"


def test_the_devanagari_abbreviations_a_form20_actually_prints_are_present():
    aliases = {r["alias"] for r in rows("party_alias.csv")}
    for expected in ("झामुमो", "भाजपा", "आजसू", "नोटा", "निर्दलीय"):
        assert expected in aliases, f"{expected} missing - a Form 20 header will not resolve"


def test_aliases_are_unique():
    """The table's primary key is the alias, so a duplicate would be a silent
    last-write-wins between two parties."""
    seen: dict[str, str] = {}
    for r in rows("party_alias.csv"):
        assert r["alias"] not in seen, (
            f"alias {r['alias']!r} maps to both {seen[r['alias']]} and {r['party_abbr']}"
        )
        seen[r["alias"]] = r["party_abbr"]


def test_nota_is_a_party_row_with_aliases():
    """D1: NOTA must load as a real candidate so it enters the denominator. The
    seeded NOTA party and its FILTER bucket were dead code because the parser
    always classified NOTA as a tail value."""
    assert "NOTA" in {r["abbr"] for r in rows("parties.csv")}
    nota = {r["alias"] for r in rows("party_alias.csv") if r["party_abbr"] == "NOTA"}
    assert "नोटा" in nota
    assert "None of the Above" in nota


# --------------------------------------------------------------------------
# Alliances
# --------------------------------------------------------------------------


def test_alliances_change_between_events():
    """The reason party_alliance is per event rather than a column on party:
    AJSU was outside the NDA in 2019 and inside it in 2014 and 2024."""
    by_key = {(r["party_abbr"], r["event_label"]): r["alliance"]
              for r in rows("party_alliance.csv")}
    assert by_key[("AJSU", "VS-2019")] == "NONE"
    assert by_key[("AJSU", "VS-2024")] == "NDA"
    assert by_key[("AJSU", "VS-2014")] == "NDA"


def test_jvm_has_alliances_only_for_events_it_contested():
    """JVM merged into BJP in February 2020, so it has no 2024 row."""
    events_with_jvm = {r["event_label"] for r in rows("party_alliance.csv")
                       if r["party_abbr"] == "JVM"}
    assert "VS-2019" in events_with_jvm
    assert "VS-2024" not in events_with_jvm


def test_jlkm_appears_only_from_2024():
    events_with_jlkm = {r["event_label"] for r in rows("party_alliance.csv")
                        if r["party_abbr"] == "JLKM"}
    assert events_with_jlkm == {"VS-2024", "LS-2024"}


# --------------------------------------------------------------------------
# Referential integrity across the CSV set
# --------------------------------------------------------------------------


def test_party_aliases_reference_known_parties(parties):
    for r in rows("party_alias.csv"):
        assert r["party_abbr"] in parties, r


def test_alliances_reference_known_parties_and_events(parties, events):
    for r in rows("party_alliance.csv"):
        assert r["party_abbr"] in parties, r
        assert r["event_label"] in events, r


def test_contests_reference_known_acs_events_and_parties(acs, events, parties):
    for r in rows("ac_contest.csv"):
        assert r["ac_number"] in acs, r
        assert r["event_label"] in events, r
        assert r["party_a"] in parties and r["party_b"] in parties, r


def test_totals_reference_known_acs_events_and_parties(acs, events, parties):
    for r in rows("ac_totals.csv"):
        assert r["ac_number"] in acs, r
        assert r["election_label"] in events, r
        if r["party_abbr"]:
            assert r["party_abbr"] in parties, r


def test_blocks_and_areas_reference_known_acs(acs):
    for name in ("ac_blocks.csv", "areas_wards.csv", "areas_panchayats.csv"):
        for r in rows(name):
            assert r["ac_number"] in acs, (name, r)


def test_every_ac_has_at_least_one_block(acs):
    have = {r["ac_number"] for r in rows("ac_blocks.csv")}
    assert have == acs


def test_new_ac_blocks_are_marked_unverified():
    """Block lists for the five new ACs come from the spec, not from a PS list.
    The spec says to seed them with source='spec-unverified' until the real list
    is available. The one exception is a block split between constituencies by
    LGD village data (scripts/build_panchayats.py), which says so."""
    for r in rows("ac_blocks.csv"):
        if r["ac_number"] != "32":
            assert r["source"] in ("spec-unverified", "lgd-village-ac"), r
            if r["source"] == "lgd-village-ac":
                assert r["name_en"].endswith("(part)"), r


def test_generated_block_ids_stay_inside_smallint():
    """block_id is a SMALLINT and the loader mints it as ac_number * 100 + n."""
    counts: dict[str, int] = {}
    for r in rows("ac_blocks.csv"):
        counts[r["ac_number"]] = counts.get(r["ac_number"], 0) + 1
    for ac_number, n in counts.items():
        assert int(ac_number) * 100 + n < 32767
        assert n < 100, f"AC {ac_number} has {n} blocks; the *100 scheme allows 99"


def test_panchayats_come_from_lgd_with_a_code_and_their_evidence():
    """Panchayat names are the Local Government Directory's, never typed in: each
    row carries its LGD code, its source and why it sits in that constituency
    (db/seed/README.md, DECISIONS D-012)."""
    panchayats = rows("areas_panchayats.csv")
    assert len(panchayats) > 100
    blocks = {(r["ac_number"], r["name_en"]) for r in rows("ac_blocks.csv")}
    for r in panchayats:
        assert r["kind"] == "panchayat", r
        assert r["code"].isdigit() and r["source"].startswith("LGD"), r
        assert r["membership"], r
        assert (r["ac_number"], r["block_name_en"]) in blocks, r
    codes = [r["code"] for r in panchayats]
    assert len(codes) == len(set(codes)), "a panchayat is seeded twice"
    names = [(r["block_name_en"], r["name_en"]) for r in panchayats]
    assert len(names) == len(set(names)), "two panchayats share a name in one block"


def test_giridih_has_both_its_blocks_panchayats():
    by_block: dict[str, int] = {}
    for r in rows("areas_panchayats.csv"):
        if r["ac_number"] == "32":
            by_block[r["block_name_en"]] = by_block.get(r["block_name_en"], 0) + 1
    # LGD: 30 Giridih-block panchayats; the 2024 polling-station list has
    # stations in 18 of them, so the other 12 are in Gandey (31) - D-013.
    assert by_block == {"Giridih Block": 18, "Pirtand Block": 17}


def test_village_aliases_point_at_seeded_panchayats():
    seeded = {(r["ac_number"], r["block_name_en"], r["name_en"])
              for r in rows("areas_panchayats.csv")}
    aliases = rows("area_aliases.csv")
    assert aliases
    for r in aliases:
        assert (r["ac_number"], r["block_name_en"], r["area_name_en"]) in seeded, r
    keys = [r["alias"].casefold() for r in aliases]
    assert len(keys) == len(set(keys)), "an alias names two panchayats"


def test_area_polygons_match_seeded_areas():
    import json

    seeded = {(r["ac_number"], r["block_name_en"], r["kind"], r["name_en"])
              for r in rows("areas_panchayats.csv") + rows("areas_wards.csv")}
    data = json.loads((SEED / "geo" / "areas.json").read_text(encoding="utf-8"))
    for f in data["features"]:
        p = f["properties"]
        assert (str(p["ac_number"]), p["block_name_en"], p["kind"], p["name_en"]) in seeded, p
        assert p["source"] in data["sources"], p
        assert f["geometry"]["type"] in ("Polygon", "MultiPolygon"), p


def test_exactly_one_baseline_event():
    """0014 enforces one baseline per AC with a partial unique index; the seed
    must not try to set two."""
    baselines = [r for r in rows("election_event.csv")
                 if r["is_baseline"].strip().lower() == "true"]
    assert len(baselines) == 1
    assert baselines[0]["label"] == "VS-2024"


def test_event_labels_have_no_ac_specific_suffix():
    """The old elections.csv carried 'LS-2024 (AC seg)' because one row had to
    serve as both the event and the Giridih segment. Events are shared now and
    the segment is the per-AC election row, so the suffix would be wrong."""
    for r in rows("election_event.csv"):
        assert "(" not in r["label"], r
        assert r["label"] == f"{r['type']}-{r['year']}"


def test_current_ps_list_carries_no_personal_data():
    """The BLO list it comes from names each booth level officer and their
    mobile number. Neither belongs in the repository (scripts/build_ps_list_current.py)."""
    import re

    path = SEED / "ps_list" / "giridih_current_parts.csv"
    text = path.read_text(encoding="utf-8")
    assert not re.search(r"(?<!\d)[6-9]\d{9}(?!\d)", text), "a mobile number is in the seed"
    columns = text.splitlines()[0].lower().split(",")
    for column in columns:
        assert not column.startswith(("blo_", "mobile", "phone", "officer", "बीएलओ", "मोबाइल")), column


def test_current_ps_list_matches_only_seeded_panchayats():
    parts = rows("ps_list/giridih_current_parts.csv")
    numbers = [int(r["part_number"]) for r in parts]
    assert numbers == sorted(set(numbers)), "a part is listed twice"
    codes = {r["code"] for r in rows("areas_panchayats.csv")}
    matched = [r for r in parts if r["status"] == "matched"]
    assert matched
    for r in matched:
        assert r["panchayat_code"] in codes, r
    for r in parts:
        assert r["status"] in ("matched", "unmatched"), r
