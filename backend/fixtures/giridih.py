"""The one source of fixture truth for Giridih AC-32.

**Why this module exists.** There were two fixtures: eight hand-written booths in
`web/src/fixtures/index.ts` for the frontend, and a handful of constants in
`tests/metric_cases.py` for the metric tests. They disagreed about the
constituency's valid votes, and neither was wrong enough to fail a test (finding
N8). Two fixtures cannot be kept in step by intention; they are generated from
this one.

`scripts/generate_fixtures.py` emits `frontend/src/fixtures/generated.ts` from
here, and `tests/metric_cases.py` imports from here. A test asserts the generated
TypeScript matches what this module produces.

**What is real and what is not.** Since the real Form 20 load:

* **Real** (ECI Form 20, `db/seed/form20/`, read by `ingest.form20_tables`):
  every vote at all 367 polling stations for VS-2024 and VS-2019, each
  candidate by name, postal ballots, rejected and tendered votes, the printed
  page of each row, and the 2019 -> 2024 station mapping (PS numbering is
  stable; the evidence is in `ingest/load_form20_tables.py`).
* **Synthetic, and labelled so:** each booth's electorate (the published AC
  total of 304,898, apportioned), its map position, its ward or panchayat,
  its building name, roll additions, floating vote and volatility. No PS list
  or roll is loaded, so none of these is known for a real station. The
  fixture banner says so on every page.

The 36 wards are the real ones from `db/seed/areas_wards.csv`; the rural areas
are named "Fixture Panchayat NN" because the real panchayat list is not seeded
(finding N9), and assigning a real station to a ward is itself synthetic.
"""

from __future__ import annotations

import csv
import pathlib
import random
from dataclasses import asdict, dataclass
from functools import cache

ROOT = pathlib.Path(__file__).resolve().parents[1]
SEED_DIR = ROOT / "db" / "seed"

AC_NUMBER = 32

# ---------------------------------------------------------------------------
# The Form 20 tables
# ---------------------------------------------------------------------------

FORM20_DIR = SEED_DIR / "form20"
FORM20_FILES = {
    "VS-2024": FORM20_DIR / "giridih_vs2024_form20.xlsx",
    "VS-2019": FORM20_DIR / "giridih_vs2019_form20.xlsx",
}
PARTY_FILE = FORM20_DIR / "candidate_parties.csv"
UNRECORDED = "UNK"

# The pivot columns the booth table and the scenario engine read. Each maps to
# the candidate whose party is recorded; everyone else is `others`.
PIVOT = ("jmm", "bjp", "jlkm")


@cache
def table(label: str):
    """The parsed Form 20 for one election, refused if it does not reconcile."""
    from ingest import form20_tables

    parsed = form20_tables.read(FORM20_FILES[label])
    problems = form20_tables.problems(parsed)
    if problems:
        raise RuntimeError(f"{label} Form 20 does not reconcile: {problems[:3]}")
    return parsed


@cache
def parties(label: str) -> dict[str, str]:
    """Candidate header -> party code, from the affiliation list; UNK otherwise."""
    from ingest import form20_tables

    recorded = {}
    with PARTY_FILE.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["election"] == label:
                recorded[form20_tables.name_key(row["candidate"])] = row["party_abbr"]
    return {name: recorded.get(form20_tables.name_key(name), UNRECORDED)
            for name in table(label).candidates}


def candidates(label: str) -> list[dict]:
    """Every candidate as declared: EVM, postal and total votes, ranked."""
    from ingest import form20_tables

    t = table(label)
    party = parties(label)
    rows = [
        {
            "candidate": form20_tables.display_name(name),
            "party": party[name],
            "evm_votes": t.evm.votes[i],
            "postal_votes": t.postal.votes[i],
            "votes": t.polled.votes[i],
        }
        for i, name in enumerate(t.candidates)
    ]
    rows.sort(key=lambda r: (-r["votes"], r["candidate"]))
    return rows


def _pivot(label: str, votes: list[int]) -> dict[str, int]:
    """One row of candidate votes folded into the pivot columns."""
    party = parties(label)
    out = {key: 0 for key in (*PIVOT, "others")}
    for name, value in zip(table(label).candidates, votes, strict=True):
        key = party[name].lower()
        out[key if key in PIVOT else "others"] += value
    return out


def _declared(label: str) -> dict[str, int]:
    t = table(label)
    out = _pivot(label, t.polled.votes)
    out["nota"] = t.polled.nota
    return out


ELECTORS = 304_898           # published (secondary) - no roll is loaded
ELECTORS_2019 = 264_814

PARTY_TOTALS: dict[str, int] = _declared("VS-2024")
VALID_VOTES = sum(PARTY_TOTALS.values())             # NOTA included, METRICS.md
REJECTED = table("VS-2024").polled.rejected          # all postal; 0 at every booth
POSTAL_VALID = table("VS-2024").postal.valid + table("VS-2024").postal.nota
EVM_VALID = table("VS-2024").evm.valid + table("VS-2024").evm.nota

_ranked_2024 = candidates("VS-2024")
WINNER = _ranked_2024[0]["party"]
RUNNER_UP = _ranked_2024[1]["party"]
WINNER_CANDIDATE = _ranked_2024[0]["candidate"]
RUNNER_CANDIDATE = _ranked_2024[1]["candidate"]
MARGIN_VOTES = _ranked_2024[0]["votes"] - _ranked_2024[1]["votes"]

# The contest pair for Giridih, from ac_contest. Used for the signed margin.
CONTEST_PARTY_A = "JMM"
CONTEST_PARTY_B = "BJP"

# The 2019 crosswalk the loader writes: PS n -> the same booth, 'exact', 0.95,
# unreviewed (ingest/load_form20_tables.py records the evidence).
CROSSWALK_CONFIDENCE = 0.95


# ---------------------------------------------------------------------------
# Booth shape
# ---------------------------------------------------------------------------

BOOTH_COUNT = len(table("VS-2024").booths)     # 367 polling stations

# A polling station in Jharkhand is capped near 1,500 electors and rarely sits
# below 800, which is the band the brief asks for. The eight-booth fixture had
# booths averaging 38,000 - an entire assembly segment each - so every
# per-booth figure on every screen was implausible by a factor of forty, and
# marker sizes scaled by electorate were meaningless.
MIN_ELECTORS = 800
MAX_ELECTORS = 1_500

# Fixed, so the fixture is identical on every machine and in every run. Change
# it and every booth changes, which the generated-file test will notice.
SEED = 20_241_123


@dataclass(frozen=True)
class Area:
    area_en: str
    area_hi: str
    kind: str
    block_en: str
    block_hi: str
    real: bool


@dataclass(frozen=True)
class Booth:
    booth_uid: str
    ps_numbers: str
    area_en: str
    area_hi: str
    block_en: str
    building: str
    lat: float | None
    lon: float | None
    electors: int
    jmm: int
    bjp: int
    jlkm: int
    others: int
    nota: int
    rejected: int
    tendered: int
    source_page: int
    crosswalk_confidence: float | None
    crosswalk_reviewed: bool
    lineage_kind: str | None
    additions: int
    floating_pct: float | None
    margin_stddev: float | None

    @property
    def valid_votes(self) -> int:
        return self.jmm + self.bjp + self.jlkm + self.others + self.nota

    @property
    def votes_polled(self) -> int:
        return self.valid_votes + self.rejected


# ---------------------------------------------------------------------------
# Areas
# ---------------------------------------------------------------------------

BLOCKS = [
    ("Giridih Municipal Corporation", "गिरिडीह नगर निगम", "ulb"),
    ("Giridih Block", "गिरिडीह प्रखंड", "rural"),
    ("Pirtand Block", "पीरटांड़ प्रखंड", "rural"),
]

RURAL_AREAS_PER_BLOCK = 12


def real_wards() -> list[tuple[str, str]]:
    """The 36 municipal wards, read from the seed rather than copied.

    Read at import time on purpose: if the seed's ward list changes, this
    fixture changes with it and the generated-file test fails until it is
    regenerated. A copied list would silently disagree.
    """
    path = SEED_DIR / "areas_wards.csv"
    wards = []
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if int(row["ac_number"]) == AC_NUMBER:
                wards.append((row["name_en"], row["name_hi"]))
    if not wards:
        raise RuntimeError(
            f"{path} has no wards for AC {AC_NUMBER}; the fixture cannot place "
            "urban booths"
        )
    return wards


def areas() -> list[Area]:
    out: list[Area] = []
    ulb_en, ulb_hi, _ = BLOCKS[0]
    for name_en, name_hi in real_wards():
        out.append(Area(name_en, name_hi, "ward", ulb_en, ulb_hi, real=True))

    # Synthetic, and named so nobody can mistake them for Jharkhand places.
    # The block's first word is carried into the name, because "Fixture
    # Panchayat 01" existed in both rural blocks otherwise - two different
    # areas with the same name, which broke the "booths are spread across the
    # structure" count and would have merged two areas in any UI that grouped
    # by name.
    for block_en, block_hi, kind in BLOCKS[1:]:
        if kind != "rural":
            continue
        tag = block_en.split()[0]
        for index in range(1, RURAL_AREAS_PER_BLOCK + 1):
            out.append(Area(
                f"Fixture Panchayat {tag} {index:02d}",
                f"नमूना पंचायत {tag} {index:02d}",
                "panchayat",
                block_en,
                block_hi,
                real=False,
            ))
    return out


# ---------------------------------------------------------------------------
# Exact integer allocation
# ---------------------------------------------------------------------------


def _allocate(total: int, weights: list[float]) -> list[int]:
    """Split `total` across `weights` so the parts sum to exactly `total`.

    Largest remainder. Floor every share, then hand the leftover out one at a
    time to the largest fractional parts. This is what makes every column of the
    fixture reconcile to its published total exactly rather than to within a few
    votes - and "within a few votes" is precisely the kind of discrepancy that
    makes a reader stop trusting a table.
    """
    if total == 0:
        return [0] * len(weights)
    mass = sum(weights)
    if mass <= 0:
        raise ValueError("weights must be positive")
    exact = [total * w / mass for w in weights]
    parts = [int(x) for x in exact]
    remainder = total - sum(parts)
    order = sorted(range(len(weights)), key=lambda i: exact[i] - parts[i], reverse=True)
    for i in order[:remainder]:
        parts[i] += 1
    return parts


def _clamped_electors(rng: random.Random, valid: list[int]) -> list[int]:
    """Electors per booth: inside the band, summing to the published total.

    Derived from each booth's own vote count and a turnout drawn per booth, then
    corrected to hit the AC total exactly. The correction is spread over the
    booths that have room for it, so no booth is pushed outside 800-1,500 to
    make the arithmetic work.
    """
    target = ELECTORS
    raw = []
    for votes in valid:
        turnout = rng.uniform(0.58, 0.79)
        raw.append(max(MIN_ELECTORS, min(MAX_ELECTORS, round(votes / turnout))))

    # Spread the difference one vote at a time over booths with headroom.
    for _ in range(1_000_000):
        diff = target - sum(raw)
        if diff == 0:
            break
        step = 1 if diff > 0 else -1
        moved = False
        order = list(range(len(raw)))
        rng.shuffle(order)
        for i in order:
            nxt = raw[i] + step
            if MIN_ELECTORS <= nxt <= MAX_ELECTORS and nxt >= valid[i]:
                raw[i] = nxt
                moved = True
                if target - sum(raw) == 0:
                    break
        if not moved:
            raise RuntimeError(
                f"cannot reach {target} electors within "
                f"[{MIN_ELECTORS}, {MAX_ELECTORS}] across {len(raw)} booths"
            )
    if sum(raw) != target:
        raise RuntimeError(f"electors sum to {sum(raw)}, expected {target}")
    return raw


# ---------------------------------------------------------------------------
# The booths
# ---------------------------------------------------------------------------

# Roughly where Giridih town sits. These are not real booth locations.
CENTRE_LAT, CENTRE_LON = 24.1854, 86.3094

# Synthetic ward and panchayat polygons, cut from the real CD block shapes by
# scripts/build_boundaries.py. Every booth is placed inside its own area's
# polygon, so a booth always sits in the ward or panchayat it is labelled with
# and inside the block outline the map draws around it.
#
# This replaced two earlier layouts. The first was a uniform jitter of +/-0.3
# degrees around the town centre that ignored the area entirely: booths of
# "Ward 1" landed 20-40 km out in the countryside, and a block filter selected
# markers from all over the map. The second, a grid per block, kept each area
# together but was invented geography: it put Giridih Block's booths north of
# the constituency's real outline. The polygons are still synthetic inside each
# block, and real geocodes replace all of this once a PS list is loaded.
AREA_POLYGONS = ROOT / "fixtures" / "geo" / "giridih_areas.json"


def area_polygons() -> dict[str, dict]:
    """area_en -> GeoJSON geometry, from the built fixture file."""
    import json

    data = json.loads(AREA_POLYGONS.read_text(encoding="utf-8"))
    out = {
        f["properties"]["name_en"]: f["geometry"]
        for f in data["features"] if f["properties"]["layer"] == "area"
    }
    missing = [a.area_en for a in areas() if a.area_en not in out]
    if missing:
        raise RuntimeError(
            f"{AREA_POLYGONS.name} has no polygon for {missing[:3]}; "
            "run scripts/build_boundaries.py"
        )
    return out


def _polygons(geometry: dict) -> list[list[list[list[float]]]]:
    return geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]


def point_in_geometry(lon: float, lat: float, geometry: dict) -> bool:
    """Even-odd ray cast over every ring, so holes are handled. Pure Python, so
    the fixture and its tests need no geometry library."""
    for polygon in _polygons(geometry):
        inside = False
        for ring in polygon:
            for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1], strict=True):
                if (y1 > lat) != (y2 > lat) and lon < x1 + (lat - y1) * (x2 - x1) / (y2 - y1):
                    inside = not inside
        if inside:
            return True
    return False


def _place(rng: random.Random, uid: str, geometry: dict) -> tuple[float, float]:
    """A booth's (lat, lon), inside its area's polygon.

    Draws exactly two numbers from `rng`, as the original jitter did, so every
    other value drawn later in the booth loop - additions, floating vote,
    volatility - is unchanged by the move. The position itself comes from a
    generator seeded by the booth id, so rejection sampling can take as many
    tries as it needs without disturbing anything else.
    """
    rng.random()
    rng.random()
    local = random.Random(f"{SEED}:{uid}")
    rings = [ring for polygon in _polygons(geometry) for ring in polygon]
    xs = [x for ring in rings for x, _ in ring]
    ys = [y for ring in rings for _, y in ring]
    for _ in range(10_000):
        lon = local.uniform(min(xs), max(xs))
        lat = local.uniform(min(ys), max(ys))
        if point_in_geometry(lon, lat, geometry):
            return round(lat, 5), round(lon, 5)
    raise RuntimeError(f"could not place {uid} inside its area")


def booths() -> list[Booth]:
    rng = random.Random(SEED)
    area_list = areas()
    shapes = area_polygons()

    # Urban booths are denser per area than rural ones, which is the one
    # structural thing about the distribution worth being true.
    assignments: list[Area] = []
    urban = [a for a in area_list if a.kind == "ward"]
    rural = [a for a in area_list if a.kind == "panchayat"]
    urban_share = 0.42
    urban_count = round(BOOTH_COUNT * urban_share)
    for i in range(urban_count):
        assignments.append(urban[i % len(urban)])
    for i in range(BOOTH_COUNT - urban_count):
        assignments.append(rural[i % len(rural)])

    # The votes are the Form 20's, station by station, in PS order.
    stations = sorted(table("VS-2024").booths, key=lambda b: b.ps_number)
    columns = {key: [] for key in (*PIVOT, "others", "nota")}
    for st in stations:
        row = _pivot("VS-2024", st.votes)
        for key in (*PIVOT, "others"):
            columns[key].append(row[key])
        columns["nota"].append(st.nota)
    valid = [st.valid_incl_nota for st in stations]
    electors = _clamped_electors(rng, valid)

    out: list[Booth] = []
    for i in range(BOOTH_COUNT):
        area = assignments[i]
        uid = f"{AC_NUMBER}-B{i + 1:04d}"

        # Deliberate gaps in the synthetic layers only, so every "not loaded"
        # path has a booth to render. The results and the crosswalk are real
        # and have no gaps.
        ungeocoded = i % 47 == 3
        no_roll_link = i % 53 == 11
        point = None if ungeocoded else _place(rng, uid, shapes[area.area_en])

        out.append(Booth(
            booth_uid=uid,
            ps_numbers=str(stations[i].ps_number),
            area_en=area.area_en,
            area_hi=area.area_hi,
            block_en=area.block_en,
            building=(
                f"{'Primary School' if i % 3 else 'Middle School'} "
                f"{area.area_en} {i + 1}"
            ),
            lat=point[0] if point else None,
            lon=point[1] if point else None,
            electors=electors[i],
            jmm=columns["jmm"][i],
            bjp=columns["bjp"][i],
            jlkm=columns["jlkm"][i],
            others=columns["others"][i],
            nota=columns["nota"][i],
            rejected=stations[i].rejected,
            tendered=stations[i].tendered,
            source_page=stations[i].page,
            # The 2024 row is the anchor; its crosswalk is the loader's 1.0.
            crosswalk_confidence=1.0,
            crosswalk_reviewed=True,
            lineage_kind=None,
            additions=0 if no_roll_link else round(electors[i] * rng.uniform(0.02, 0.09)),
            floating_pct=None if i % 7 == 0 else round(rng.uniform(4.0, 18.0), 2),
            margin_stddev=None if i % 11 == 0 else round(rng.uniform(1.5, 12.0), 2),
        ))
    return out


# ---------------------------------------------------------------------------
# What the tests and the generator consume
# ---------------------------------------------------------------------------


def ac_totals() -> dict[str, int | float | str]:
    """The declared AC-level figures (EVM + postal), as the API computes them."""
    t = table("VS-2024")
    return {
        "electors": ELECTORS,
        "electors_source": "published",
        "valid_votes": VALID_VOTES,
        "votes_polled": VALID_VOTES + REJECTED,
        "rejected": REJECTED,
        "evm_votes": EVM_VALID,
        "postal_votes": POSTAL_VALID,
        "tendered": t.polled.tendered,
        "nota": PARTY_TOTALS["nota"],
        "jmm": PARTY_TOTALS["jmm"],
        "bjp": PARTY_TOTALS["bjp"],
        "jlkm": PARTY_TOTALS["jlkm"],
        "others": PARTY_TOTALS["others"],
        "winner_party": WINNER,
        "runner_party": RUNNER_UP,
        "winner_candidate": WINNER_CANDIDATE,
        "runner_candidate": RUNNER_CANDIDATE,
        "contestants": len(t.candidates),
        "margin_votes": MARGIN_VOTES,
        "margin_pct": round(100.0 * MARGIN_VOTES / VALID_VOTES, 2),
        "turnout_pct": round(100.0 * (VALID_VOTES + REJECTED) / ELECTORS, 2),
        "jmm_share_pct": round(100.0 * PARTY_TOTALS["jmm"] / VALID_VOTES, 2),
        "bjp_share_pct": round(100.0 * PARTY_TOTALS["bjp"] / VALID_VOTES, 2),
        "nota_share_pct": round(100.0 * PARTY_TOTALS["nota"] / VALID_VOTES, 2),
        "source_doc": FORM20_FILES["VS-2024"].name,
        "sha256": t.sha256,
        "pages": t.pages,
    }


def evm_totals(label: str = "VS-2024") -> dict[str, int]:
    """Booth-level column totals: the 'Total EVM Votes' row, pivoted."""
    t = table(label)
    out = _pivot(label, t.evm.votes)
    out["nota"] = t.evm.nota
    return out


def booth_votes(label: str) -> dict[str, list[int]]:
    """Per booth_uid: votes per candidate in Form 20 column order, then NOTA."""
    return {f"{AC_NUMBER}-B{b.ps_number:04d}": [*b.votes, b.nota]
            for b in table(label).booths}


def candidate_columns(label: str) -> list[dict]:
    """The Form 20 column order, for reading booth_votes()."""
    from ingest import form20_tables

    party = parties(label)
    return [{"candidate": form20_tables.display_name(n), "party": party[n]}
            for n in table(label).candidates]


def as_dicts() -> list[dict]:
    return [asdict(b) for b in booths()]


def check() -> dict[str, object]:
    """Self-check, so a generator run reports what it produced.

    Every one of these is also a test; having them here means a bad edit is
    caught the moment the generator runs rather than at the end of a suite.
    """
    rows = booths()
    totals = ac_totals()
    sums = {
        key: sum(getattr(b, key) for b in rows)
        for key in ("jmm", "bjp", "jlkm", "others", "nota", "electors")
    }
    problems = []
    evm = evm_totals()
    for key, expected in (
        ("jmm", evm["jmm"]), ("bjp", evm["bjp"]),
        ("jlkm", evm["jlkm"]), ("others", evm["others"]),
        ("nota", evm["nota"]), ("electors", ELECTORS),
    ):
        if sums[key] != expected:
            problems.append(f"{key}: {sums[key]} != {expected}")

    valid_sum = sum(b.valid_votes for b in rows)
    if valid_sum != EVM_VALID:
        problems.append(f"valid_votes: {valid_sum} != EVM {EVM_VALID}")
    if EVM_VALID + POSTAL_VALID != VALID_VOTES:
        problems.append("EVM + postal != declared valid votes")

    out_of_band = [
        b.booth_uid for b in rows
        if not MIN_ELECTORS <= b.electors <= MAX_ELECTORS
    ]
    if out_of_band:
        problems.append(f"electors outside the band: {out_of_band[:5]}")

    over_turnout = [b.booth_uid for b in rows if b.valid_votes > b.electors]
    if over_turnout:
        problems.append(f"more votes than electors: {over_turnout[:5]}")

    return {
        "booths": len(rows),
        "areas": len(areas()),
        "electors_min": min(b.electors for b in rows),
        "electors_max": max(b.electors for b in rows),
        "electors_mean": round(sums["electors"] / len(rows), 1),
        "ungeocoded": sum(1 for b in rows if b.lat is None),
        "with_2019": len(booths_2019()),
        "totals": totals,
        "problems": problems,
    }


# ---------------------------------------------------------------------------
# The prior assembly election, for swing
# ---------------------------------------------------------------------------
#
# Real: the VS-2019 Form 20, station by station, mapped to the 2024 booth with
# the same PS number - the mapping the loader writes. JVM fielded no candidate
# in Giridih in 2019 per the loaded sources, so 2019 has no JVM column; every
# candidate without a recorded party is in `others`.

PARTY_TOTALS_2019: dict[str, int] = _declared("VS-2019")
VALID_VOTES_2019 = sum(PARTY_TOTALS_2019.values())


def booths_2019() -> dict[str, dict[str, int]]:
    """Prior-election EVM votes per booth, keyed by the 2024 booth_uid."""
    out = {}
    for st in table("VS-2019").booths:
        row = _pivot("VS-2019", st.votes)
        out[f"{AC_NUMBER}-B{st.ps_number:04d}"] = {
            "jmm": row["jmm"], "bjp": row["bjp"],
            "others": row["jlkm"] + row["others"], "nota": st.nota,
            "source_page": st.page,
        }
    return out


def ac_totals_2019() -> dict[str, int | float | str]:
    t = table("VS-2019")
    ranked = candidates("VS-2019")
    rejected = t.polled.rejected
    return {
        "electors": ELECTORS_2019,
        "electors_source": "published",
        "valid_votes": VALID_VOTES_2019,
        "votes_polled": VALID_VOTES_2019 + rejected,
        "rejected": rejected,
        "evm_votes": t.evm.valid + t.evm.nota,
        "postal_votes": t.postal.valid + t.postal.nota,
        "jmm": PARTY_TOTALS_2019["jmm"],
        "bjp": PARTY_TOTALS_2019["bjp"],
        "nota": PARTY_TOTALS_2019["nota"],
        "others": PARTY_TOTALS_2019["others"] + PARTY_TOTALS_2019["jlkm"],
        "winner_candidate": ranked[0]["candidate"],
        "runner_candidate": ranked[1]["candidate"],
        "contestants": len(t.candidates),
        "margin_votes": ranked[0]["votes"] - ranked[1]["votes"],
        "margin_pct": round(100.0 * (ranked[0]["votes"] - ranked[1]["votes"])
                            / VALID_VOTES_2019, 2),
        "turnout_pct": round(100.0 * (VALID_VOTES_2019 + rejected) / ELECTORS_2019, 2),
        "source_doc": FORM20_FILES["VS-2019"].name,
        "sha256": t.sha256,
        "pages": t.pages,
    }


# ---------------------------------------------------------------------------
# Derived per-booth metrics, computed by the product's own metric code
# ---------------------------------------------------------------------------
#
# The map tooltip and the booth card were reported showing different values for
# the same booth. They read the same fixture field, so they could only differ if
# something computed that field twice - and the fixture response builder did
# exactly that, deriving margin and turnout inline for each endpoint.
#
# These are computed once, here, and emitted into the generated file, so both
# endpoints read a stored number. And they are computed by `analytics.metrics` -
# the same functions the API and the views use - so the fixture cannot quietly
# disagree with the product about what a margin is. A fixture that computes its
# own arithmetic is a second implementation of every formula.

def derived(booth: Booth, prev: dict[str, int] | None) -> dict[str, object]:
    from analytics import metrics

    candidates = {
        "JMM": booth.jmm,
        "BJP": booth.bjp,
        "JLKM": booth.jlkm,
        "OTH": booth.others,
        metrics.NOTA: booth.nota,
    }
    ranking = metrics.rank_candidates(candidates)
    valid = metrics.valid_votes(candidates)
    polled = metrics.votes_polled(valid, booth.rejected)

    link = (
        metrics.CrosswalkLink(
            # Swing is measured across the 2019 link, so its confidence is
            # the one that governs: 0.95, unreviewed (still admissible).
            confidence=CROSSWALK_CONFIDENCE,
            reviewed=False,
        )
        if booth.crosswalk_confidence is not None
        else None
    )

    share_now = metrics.share_pct(booth.jmm, valid)
    prev_valid = (
        prev["jmm"] + prev["bjp"] + prev["others"] + prev["nota"] if prev else None
    )
    share_prev = metrics.share_pct(prev["jmm"], prev_valid) if prev else None

    return {
        "valid_votes": valid,
        "votes_polled": polled,
        "turnout_pct": metrics.turnout_pct(polled, booth.electors),
        "winner_party": ranking.winner,
        "runner_party": ranking.runner_up,
        "margin_votes": metrics.margin_votes(ranking),
        "margin_pct": metrics.margin_pct(ranking, valid),
        "signed_margin_pct": metrics.signed_margin_pct(
            ranking, valid, CONTEST_PARTY_A, CONTEST_PARTY_B
        ),
        "jmm_share_pct": share_now,
        "jmm_swing_pct": metrics.swing_pct(
            share_now, share_prev, link, booth.lineage_kind
        ),
        "new_voter_pct": (
            None if prev is None
            else metrics.new_voter_pct(booth.additions or None, booth.electors)
        ),
    }


def booths_with_metrics() -> list[dict]:
    """Every booth, with its derived metrics folded in."""
    prev_rows = booths_2019()
    out = []
    for booth in booths():
        row = asdict(booth)
        row.update(derived(booth, prev_rows.get(booth.booth_uid)))
        out.append(row)
    return out


# ---------------------------------------------------------------------------
# Booth priority, via the real percentile ranks
# ---------------------------------------------------------------------------
#
# `priority_score` was missing from the fixture entirely, so the Overview's
# "highest-priority booths" panel filtered every booth out and rendered an empty
# list. Computed here with `analytics.metrics.percentile_ranks` and
# `priority_score` - the same functions `mv_booth_priority` calls - so the
# fixture exercises the real weighting rather than an invented number, including
# the renormalisation when an input is NULL.

def booths_with_priority() -> list[dict]:
    """Every booth with its derived metrics and its priority score."""
    from analytics import metrics

    rows = booths_with_metrics()

    # Percentile ranks are computed **within one AC**, which is what the view
    # does and why the fixture must too: a rank over a pooled set would be a
    # different number.
    closeness_input = [r["margin_pct"] for r in rows]
    margin_ranks = metrics.percentile_ranks(closeness_input)
    # Closeness is 1 - the margin percentile, so the tightest booth scores 1.
    closeness = [None if r is None else 1.0 - r for r in margin_ranks]

    new_voter = metrics.percentile_ranks([r["new_voter_pct"] for r in rows])
    floating = metrics.percentile_ranks([r["floating_pct"] for r in rows])
    volatility = metrics.percentile_ranks([r["margin_stddev"] for r in rows])

    out = []
    for i, row in enumerate(rows):
        score = metrics.priority_score(metrics.PriorityInputs(
            closeness=closeness[i],
            new_voter_pct=new_voter[i],
            floating_pct=floating[i],
            volatility=volatility[i],
        ))
        enriched = dict(row)
        enriched["priority_score"] = score.score
        enriched["priority_inputs_used"] = score.inputs_used
        enriched["priority_weight_used"] = score.weight_used
        out.append(enriched)
    return out
