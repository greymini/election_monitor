"""The one source of fixture truth for Giridih AC-32.

**Why this module exists.** There were two fixtures: eight hand-written booths in
`web/src/fixtures/index.ts` for the frontend, and a handful of constants in
`tests/metric_cases.py` for the metric tests. They disagreed about the
constituency's valid votes - 207,598 against 207,459 - and neither was wrong
enough to fail a test, because both round the published margin to 1.85%. That was
finding N8. Two fixtures cannot be kept in step by intention; they have to be
generated from one thing, which is this.

`scripts/generate_fixtures.py` emits `web/src/fixtures/generated.ts` from here,
and `tests/metric_cases.py` imports from here. A test asserts the generated
TypeScript matches what this module produces, so editing one side and forgetting
the other fails rather than drifts - the same arrangement as
`analytics/metric_sql.py` and the metrics migration.

**What is real and what is not.** The AC totals below are the published Giridih
2024 figures. Everything per-booth is *invented*, deterministically, to add up to
them. A booth-level number from this module is not evidence of anything: no Form
20 has been parsed. The generated banner says so on every page, and every booth
name is unmistakably synthetic for the same reason.

**On area names.** The 36 wards are the real ones, read from
`db/seed/areas_wards.csv` so they cannot drift from the seed. The rural areas are
*not* real: `db/seed/areas_panchayats.csv` is header-only for all six ACs, so
this system does not know the real panchayat names for any of them (finding N9).
Rather than invent Jharkhand place names - which the master prompt forbids, and
which would be indistinguishable from real data once loaded - the rural areas are
named "Fixture Panchayat NN". When the real list is seeded, replace them here.
"""

from __future__ import annotations

import csv
import pathlib
import random
from dataclasses import asdict, dataclass

ROOT = pathlib.Path(__file__).resolve().parents[1]
SEED_DIR = ROOT / "db" / "seed"

AC_NUMBER = 32

# ---------------------------------------------------------------------------
# The published constituency totals. The only figures here that are real.
#
# N8 is resolved by there being one of each. Where the two old fixtures
# disagreed, `valid_votes` takes the frontend's 207,598: it was labelled as
# published, whereas 207,459 was a value I derived backwards from the margin
# percentage to make it come out at exactly 1.85. Neither is verified against a
# document, and both round the margin to the published 1.85% - so this is a
# choice of which unverified number to use consistently, not a determination of
# which is correct. It must be checked against Form 20 when one exists.
# ---------------------------------------------------------------------------

ELECTORS = 304_898
VALID_VOTES = 207_598

# Rejected votes: Form 20 omits the column when it is zero, and no document has
# been read, so zero is the honest placeholder and votes_polled == valid_votes.
REJECTED = 0

PARTY_TOTALS: dict[str, int] = {
    "jmm": 94_042,
    "bjp": 90_204,
    "jlkm": 10_787,
    "nota": 2_004,
}
# Everything not one of the four named lines, so the columns sum to valid_votes
# exactly rather than approximately.
PARTY_TOTALS["others"] = VALID_VOTES - sum(PARTY_TOTALS.values())

WINNER = "JMM"
RUNNER_UP = "BJP"
MARGIN_VOTES = PARTY_TOTALS["jmm"] - PARTY_TOTALS["bjp"]

# The contest pair for Giridih, from ac_contest. Used for the signed margin.
CONTEST_PARTY_A = "JMM"
CONTEST_PARTY_B = "BJP"

# ---------------------------------------------------------------------------
# Booth shape
# ---------------------------------------------------------------------------

BOOTH_COUNT = 305

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

# Roughly where Giridih town sits, so markers land on the constituency rather
# than in the sea. Jitter is applied per booth; these are not real locations.
CENTRE_LAT, CENTRE_LON = 24.1854, 86.3094


def booths() -> list[Booth]:
    rng = random.Random(SEED)
    area_list = areas()

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

    # Each booth gets a lean, and each party is then allocated across booths in
    # proportion to how much that booth favours it. Allocating per party means
    # every party column sums to its published total by construction.
    leans = [rng.uniform(-1.0, 1.0) for _ in range(BOOTH_COUNT)]
    sizes = [rng.uniform(0.75, 1.3) for _ in range(BOOTH_COUNT)]

    def weights(pull: float) -> list[float]:
        return [
            max(0.05, size * (1.0 + pull * lean))
            for size, lean in zip(sizes, leans, strict=True)
        ]

    columns = {
        "jmm": _allocate(PARTY_TOTALS["jmm"], weights(+0.55)),
        "bjp": _allocate(PARTY_TOTALS["bjp"], weights(-0.55)),
        "jlkm": _allocate(PARTY_TOTALS["jlkm"], weights(+0.20)),
        "others": _allocate(PARTY_TOTALS["others"], weights(-0.10)),
        "nota": _allocate(PARTY_TOTALS["nota"], weights(0.0)),
    }

    valid = [
        columns["jmm"][i] + columns["bjp"][i] + columns["jlkm"][i]
        + columns["others"][i] + columns["nota"][i]
        for i in range(BOOTH_COUNT)
    ]
    electors = _clamped_electors(rng, valid)

    out: list[Booth] = []
    for i in range(BOOTH_COUNT):
        area = assignments[i]
        uid = f"{AC_NUMBER}-B{i + 1:04d}"

        # A handful of deliberate data gaps, so every "not loaded" path on every
        # screen has something to render. Chosen by index so they are stable.
        ungeocoded = i % 47 == 3
        weak_crosswalk = i % 31 == 5
        split_booth = i % 61 == 7
        no_roll_link = i % 53 == 11

        out.append(Booth(
            booth_uid=uid,
            ps_numbers=str(i + 1),
            area_en=area.area_en,
            area_hi=area.area_hi,
            block_en=area.block_en,
            building=(
                f"{'Primary School' if i % 3 else 'Middle School'} "
                f"{area.area_en} {i + 1}"
            ),
            lat=None if ungeocoded else round(CENTRE_LAT + rng.uniform(-0.28, 0.28), 5),
            lon=None if ungeocoded else round(CENTRE_LON + rng.uniform(-0.30, 0.30), 5),
            electors=electors[i],
            jmm=columns["jmm"][i],
            bjp=columns["bjp"][i],
            jlkm=columns["jlkm"][i],
            others=columns["others"][i],
            nota=columns["nota"][i],
            rejected=0,
            source_page=3 + i // 40,
            crosswalk_confidence=0.71 if weak_crosswalk else 1.0,
            crosswalk_reviewed=not weak_crosswalk,
            lineage_kind="split" if split_booth else None,
            additions=0 if no_roll_link else round(electors[i] * rng.uniform(0.02, 0.09)),
            floating_pct=None if i % 7 == 0 else round(rng.uniform(4.0, 18.0), 2),
            margin_stddev=None if i % 11 == 0 else round(rng.uniform(1.5, 12.0), 2),
        ))
    return out


# ---------------------------------------------------------------------------
# What the tests and the generator consume
# ---------------------------------------------------------------------------


def ac_totals() -> dict[str, int | float | str]:
    """The AC-level figures, with the percentages the API would compute."""
    return {
        "electors": ELECTORS,
        "valid_votes": VALID_VOTES,
        "votes_polled": VALID_VOTES + REJECTED,
        "rejected": REJECTED,
        "nota": PARTY_TOTALS["nota"],
        "jmm": PARTY_TOTALS["jmm"],
        "bjp": PARTY_TOTALS["bjp"],
        "jlkm": PARTY_TOTALS["jlkm"],
        "others": PARTY_TOTALS["others"],
        "winner_party": WINNER,
        "runner_party": RUNNER_UP,
        "margin_votes": MARGIN_VOTES,
        "margin_pct": round(100.0 * MARGIN_VOTES / VALID_VOTES, 2),
        "turnout_pct": round(100.0 * (VALID_VOTES + REJECTED) / ELECTORS, 2),
        "jmm_share_pct": round(100.0 * PARTY_TOTALS["jmm"] / VALID_VOTES, 2),
        "bjp_share_pct": round(100.0 * PARTY_TOTALS["bjp"] / VALID_VOTES, 2),
        "nota_share_pct": round(100.0 * PARTY_TOTALS["nota"] / VALID_VOTES, 2),
    }


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
    for key, expected in (
        ("jmm", PARTY_TOTALS["jmm"]), ("bjp", PARTY_TOTALS["bjp"]),
        ("jlkm", PARTY_TOTALS["jlkm"]), ("others", PARTY_TOTALS["others"]),
        ("nota", PARTY_TOTALS["nota"]), ("electors", ELECTORS),
    ):
        if sums[key] != expected:
            problems.append(f"{key}: {sums[key]} != {expected}")

    valid_sum = sum(b.valid_votes for b in rows)
    if valid_sum != VALID_VOTES:
        problems.append(f"valid_votes: {valid_sum} != {VALID_VOTES}")

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
        "weak_crosswalk": sum(1 for b in rows if not b.crosswalk_reviewed),
        "split": sum(1 for b in rows if b.lineage_kind),
        "totals": totals,
        "problems": problems,
    }


# ---------------------------------------------------------------------------
# The prior assembly election, for swing
# ---------------------------------------------------------------------------
#
# Unverified, like everything per-booth here. The AC-level numbers are the ones
# the previous fixture carried for VS-2019 (JMM 80,871 over BJP 64,987, a margin
# of 15,884, on 264,814 electors); JVM, NOTA and others are invented to make the
# column sum, because the old fixture recorded no valid-vote total for that year
# at all. JVM is present because it is the party D9 is about: it merged into BJP
# in 2020, so a 2019-to-2024 swing table that omitted it showed BJP's gain with
# no corresponding loss anywhere.

ELECTORS_2019 = 264_814
VALID_VOTES_2019 = 168_000
PARTY_TOTALS_2019: dict[str, int] = {
    "jmm": 80_871,
    "bjp": 64_987,
    "jvm": 14_000,
    "nota": 2_100,
}
PARTY_TOTALS_2019["others"] = VALID_VOTES_2019 - sum(PARTY_TOTALS_2019.values())


def booths_2019() -> dict[str, dict[str, int]]:
    """Prior-election votes per booth, keyed by the 2024 booth_uid.

    Not every booth gets a row. Three groups are left out on purpose, because
    each drives a different NULL rule that the screens must render honestly:
    a booth with no prior row at all has no swing (D2), a split booth cannot be
    compared station to station, and a weakly crosswalked booth is not admissible
    until reviewed.
    """
    rng = random.Random(SEED + 1)
    rows = booths()
    eligible = [b for b in rows if b.lineage_kind is None and int(b.ps_numbers) % 9 != 4]

    sizes = [rng.uniform(0.75, 1.3) for _ in eligible]
    leans = [rng.uniform(-1.0, 1.0) for _ in eligible]

    def weights(pull: float) -> list[float]:
        return [
            max(0.05, size * (1.0 + pull * lean))
            for size, lean in zip(sizes, leans, strict=True)
        ]

    columns = {
        "jmm": _allocate(PARTY_TOTALS_2019["jmm"], weights(+0.50)),
        "bjp": _allocate(PARTY_TOTALS_2019["bjp"], weights(-0.50)),
        "jvm": _allocate(PARTY_TOTALS_2019["jvm"], weights(+0.15)),
        "nota": _allocate(PARTY_TOTALS_2019["nota"], weights(0.0)),
    }
    return {
        booth.booth_uid: {
            "jmm": columns["jmm"][i],
            "bjp": columns["bjp"][i],
            "jvm": columns["jvm"][i],
            "nota": columns["nota"][i],
        }
        for i, booth in enumerate(eligible)
    }


def ac_totals_2019() -> dict[str, int | float]:
    return {
        "electors": ELECTORS_2019,
        "valid_votes": VALID_VOTES_2019,
        "jmm": PARTY_TOTALS_2019["jmm"],
        "bjp": PARTY_TOTALS_2019["bjp"],
        "jvm": PARTY_TOTALS_2019["jvm"],
        "nota": PARTY_TOTALS_2019["nota"],
        "others": PARTY_TOTALS_2019["others"],
        "margin_votes": PARTY_TOTALS_2019["jmm"] - PARTY_TOTALS_2019["bjp"],
        "turnout_pct": round(100.0 * VALID_VOTES_2019 / ELECTORS_2019, 2),
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
            confidence=booth.crosswalk_confidence or 0.0,
            reviewed=booth.crosswalk_reviewed,
        )
        if booth.crosswalk_confidence is not None
        else None
    )

    share_now = metrics.share_pct(booth.jmm, valid)
    prev_valid = (
        prev["jmm"] + prev["bjp"] + prev["jvm"] + prev["nota"] if prev else None
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
