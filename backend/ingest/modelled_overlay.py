"""Modelled estimates for what no public source gives booth by booth: electors and community mix.

    python -m ingest.modelled_overlay --ac 32

Run after the real loads: Form 20 (VS-2019, VS-2024, LS-2019, LS-2024), the
2024 polling-station list (`ingest.load_ps_list`) and the seed. Every layer is
registered in `source_doc` with `method = 'modelled'`; nothing here is test
data, and nothing is random. What each layer is fitted to:

**Roll (`modelled/roll_ac32.json`).** Electors per booth are not published
without voter names (roll PDFs), so they are estimated - but every total is
official. For each roll (VS-2019, LS-2024, VS-2024):

* the AC's general electors, male / female / third gender and 18-19 year olds
  from CEO Jharkhand (`ROLLS` below, with sources);
* a booth's electors = its votes polled / the turnout of its setting (urban or
  rural, from the polling-station list), averaged over the two elections on
  that roll, shrunk a quarter of the way to the setting's mean (ECI sizes
  booths to similar rolls), never below the votes it polled, then scaled to
  the official total exactly. The two settings' turnouts are the ones that
  give urban and rural booths the same average roll - ECI's own sizing rule.
* women: the official AC total, spread by the Census 2011 sex ratio of the
  booth's village (or the town's);
* 18-19: the official AC total, spread evenly (no booth figure exists);
  20-29 and 60+ from ECI's LS-2024 state age profile (18-29 = 28.38%,
  60+ = 13.40%); 30-59 is published only as one band, so 30-39, 40-49 and
  50-59 are left empty rather than split by guesswork;
* roll change VS-2019 -> VS-2024 per booth: the net change; gross additions are
  at least the booth's 18-19 year olds (all of them joined after 2019), and
  deletions are what makes the net come out. Gross figures are not published.

**Community (`modelled/caste_ac32.json`).** No caste count exists below the
state. Each booth's mix of five groups - Muslim, ST, SC, Kurmi (Mahato), and
upper caste together with other OBC (the two vote too alike to separate) - is
the mix that best explains the booth's real VS-2024 and LS-2024 vote shares
(INDIA / NDA / JLKM / others) under the published vote-by-community figures
(`db/seed/modelled/vote_by_community.csv`: Lokniti-CSDS and Axis My India,
2024), while keeping SC and ST close to the Census 2011 figures of the
booth's own village. The Muslim share is then scaled, unit by unit, to the
Census 2011 religion figures (Giridih town 30.3%, Giridih block 22.4%,
Pirtand 8.3%). Estimates, with a confidence, never a count of anyone.

**Geography** is no longer modelled here: `ingest.load_ps_list` names and
places every booth from the real register. The `geo` source row stays, with a
note saying how approximate the points are.

LS-2024 booth results are real (CEO Jharkhand Form 20), so the old modelled
LS segment and its placeholder candidates are removed.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import sys
from pathlib import Path

from common.db import cursor, query, query_one
from common.logging_setup import get_logger

log = get_logger(__name__)

AC_NUMBER = 32
SEED = Path(__file__).resolve().parents[1] / "db" / "seed"
PS_LIST = SEED / "ps_list" / "giridih_ps2024.csv"
PCA = SEED / "census" / "village_pca_giridih_pirtand.csv"
AFFINITY = SEED / "modelled" / "vote_by_community.csv"
METHOD_VERSION = "modelled-ecological-1"

LAYERS = {
    "geo": {
        "filename": "modelled/geo_ac32.json",
        "kind": "other",
        "method_note": ("Booth names and areas from the CEO Jharkhand 2024 polling-station list; "
                        "points are approximate (village or ward level), not the building"),
    },
    "roll": {
        "filename": "modelled/roll_ac32.json",
        "kind": "roll_mother",
        "method_note": ("Booth electors estimated from votes polled; AC totals, gender and 18-19 "
                        "voters are the official CEO Jharkhand figures"),
    },
    "caste": {
        "filename": "modelled/caste_ac32.json",
        "kind": "other",
        "method_note": ("Community mix fitted to each booth's real 2024 votes with Lokniti-CSDS / "
                        "Axis vote-by-community shares and Census 2011 SC/ST/religion; not a survey"),
    },
}

# (revision label, date, elections on this roll, general electors, male, female,
#  third gender, 18-19, source). General electors exclude service voters, who
# vote by post and belong to no booth.
ROLLS = [
    ("modelled-vs2019", "2019-11-01", ("VS-2019", "LS-2019"), 264_574, 138_597, 125_968, 9, None,
     "CEO Jharkhand statistical report VS-2019 (general electors)"),
    ("modelled-ls2024", "2024-05-15", ("LS-2024", "VS-2024"), 307_399, None, None, None, 10_486,
     "CEO Jharkhand LS-2024 electorate, phase VI: 3,07,676 incl. 277 service; 18-19: 10,486"),
    ("modelled-vs2024", "2024-10-29", ("VS-2024", "LS-2024"), 304_898, 153_764, 151_133, 1, 13_881,
     "CEO Jharkhand VS-2024 elector count, 2nd phase (Elector_count_2nd_Phase.pdf)"),
]
LINKS = {"VS-2019": "modelled-vs2019", "LS-2019": "modelled-vs2019",
         "LS-2024": "modelled-ls2024", "VS-2024": "modelled-vs2024"}
STATE_18_29, STATE_60_PLUS = 0.2838, 0.1340       # ECI Atlas LS-2024, Jharkhand
SHRINK = 0.25
RIDGE = 1.0            # pull of a booth's community mix towards its unit's average
MAX_ELECTORS = 1500

BLOCS = {"JMM": "INDIA", "INC": "INDIA", "RJD": "INDIA", "CPIML": "INDIA",
         "BJP": "NDA", "AJSU": "NDA", "JLKM": "JLKM"}
# Jairam Mahato founded JLKM but stood as an independent in LS-2024: the party
# was not registered in time.
CANDIDATE_BLOCS = {("LS-2024", "JAIRAM KUMAR MAHATO"): "JLKM"}
BLOC_ORDER = ("INDIA", "NDA", "JLKM", "Others")

# Census 2011 religion: Muslim share of each unit (PCA by religion, RL-2000).
MUSLIM_SHARE = {"town": 34_716 / 114_533, "Giridih": 57_728 / 258_037, "Pirtand": 9_123 / 109_515}
# Census 2011 SC / ST of Giridih Nagar Parishad, for town booths with no village of their own.
TOWN_SC_ST = (9_510 / 114_533, 1_280 / 114_533)
TOWN_SEX_RATIO = 910


def _rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _register_doc(layer: str, ac_id: int) -> int:
    meta = LAYERS[layer]
    digest = hashlib.sha256(meta["filename"].encode()).hexdigest()
    row = query_one(
        "INSERT INTO source_doc (filename, kind, sha256, bytes, ac_id, parse_status, is_synthetic, "
        "method, method_note, storage_backend, storage_key) "
        "VALUES (%s, %s, %s, 0, %s, 'loaded', false, 'modelled', %s, 'local', %s) "
        "ON CONFLICT (sha256) DO UPDATE SET method = EXCLUDED.method, "
        "method_note = EXCLUDED.method_note, is_synthetic = false, ac_id = EXCLUDED.ac_id "
        "RETURNING doc_id",
        (meta["filename"], meta["kind"], digest, ac_id, meta["method_note"], meta["filename"]),
    )
    return row["doc_id"]


def _ac_id(ac_number: int) -> int:
    row = query_one("SELECT ac_id FROM ac WHERE ac_number = %s", (ac_number,))
    if row is None:
        raise SystemExit(f"AC {ac_number} is not seeded")
    return row["ac_id"]


def apportion(total: int, weights: list[float], floors: list[int] | None = None,
              caps: list[int] | None = None) -> list[int]:
    """Integers summing to `total`, proportional to `weights`, within floors and caps.

    Largest remainder, after iteratively fixing the entries a floor or cap binds.
    """
    n = len(weights)
    floors = floors or [0] * n
    caps = caps or [10**12] * n
    fixed: dict[int, int] = {}
    for _ in range(n + 1):
        free = [i for i in range(n) if i not in fixed]
        left = total - sum(fixed.values())
        wsum = sum(weights[i] for i in free) or 1.0
        share = {i: left * weights[i] / wsum for i in free}
        bound = {i: floors[i] for i in free if share[i] < floors[i]}
        bound |= {i: caps[i] for i in free if share[i] > caps[i]}
        if not bound:
            break
        fixed |= bound
    base = {i: int(share[i]) for i in free}
    rest = left - sum(base.values())
    for i in sorted(free, key=lambda i: share[i] - base[i], reverse=True)[:max(0, rest)]:
        base[i] += 1
    return [fixed.get(i, base.get(i, 0)) for i in range(n)]


# --------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------

def booths(ac_id: int) -> list[dict]:
    """Active booths with their 2024 station number and what the PS list says about them."""
    ps = {int(r["ps_number"]): r for r in _rows(PS_LIST)} if PS_LIST.exists() else {}
    pca = {r["village_code_2011"]: r for r in _rows(PCA)} if PCA.exists() else {}
    out = []
    for b in query("SELECT booth_uid, current_ps_number FROM booth WHERE ac_id = %s AND is_active "
                   "ORDER BY current_ps_number NULLS LAST, booth_uid", (ac_id,)):
        r = ps.get(b["current_ps_number"] or -1, {})
        village = pca.get(r.get("village_census_code", ""))
        setting = r.get("setting") or "rural"
        unit = ("town" if setting == "urban" and not village
                else "Pirtand" if r.get("block_name_en") == "Pirtand Block" else "Giridih")
        if village and int(village["population"]):
            pop = int(village["population"])
            sc, st, sex_ratio = int(village["sc"]) / pop, int(village["st"]) / pop, int(village["sex_ratio"])
        elif unit == "town":
            (sc, st), sex_ratio = TOWN_SC_ST, TOWN_SEX_RATIO
        else:
            sc = st = sex_ratio = None
        out.append({"booth_uid": b["booth_uid"], "ps": b["current_ps_number"], "setting": setting,
                    "unit": unit, "sc": sc, "st": st, "sex_ratio": sex_ratio,
                    "located": float(r.get("locate_conf") or 0)})
    _fill_from_neighbours(out)
    return out


def _fill_from_neighbours(rows: list[dict]) -> None:
    """A booth without a village of its own takes the mean of its unit's booths that have one."""
    for unit in {r["unit"] for r in rows}:
        known = [r for r in rows if r["unit"] == unit and r["sc"] is not None]
        if not known:
            continue
        mean = {k: sum(r[k] for r in known) / len(known) for k in ("sc", "st", "sex_ratio")}
        for r in rows:
            if r["unit"] == unit and r["sc"] is None:
                r.update(sc=mean["sc"], st=mean["st"], sex_ratio=mean["sex_ratio"])


def polled(ac_id: int, label: str) -> dict[str, int]:
    """Votes polled (valid incl. NOTA + rejected) per booth for one election."""
    rows = query(
        "SELECT x.booth_uid, m.total_valid + COALESCE(m.rejected, 0) AS polled "
        "FROM booth_crosswalk x JOIN election e ON e.election_id = x.election_id "
        "JOIN result_booth_meta m ON m.election_id = x.election_id AND m.ps_number = x.ps_number "
        "WHERE x.ac_id = %s AND e.label = %s", (ac_id, label))
    return {r["booth_uid"]: int(r["polled"] or 0) for r in rows}


def bloc_shares(ac_id: int, label: str) -> dict[str, list[float]]:
    """Per booth: share of valid votes (NOTA included) for INDIA, NDA, JLKM, others."""
    rows = query(
        "SELECT x.booth_uid, c.name_en, p.abbr, SUM(rb.votes)::INT AS votes "
        "FROM booth_crosswalk x JOIN election e ON e.election_id = x.election_id "
        "JOIN result_booth rb ON rb.election_id = x.election_id AND rb.ps_number = x.ps_number "
        "JOIN candidate c ON c.candidate_id = rb.candidate_id "
        "LEFT JOIN party p ON p.party_id = c.party_id "
        "WHERE x.ac_id = %s AND e.label = %s GROUP BY 1, 2, 3", (ac_id, label))
    out: dict[str, list[float]] = {}
    for r in rows:
        bloc = (CANDIDATE_BLOCS.get((label, (r["name_en"] or "").upper()))
                or BLOCS.get(r["abbr"] or "", "Others"))
        out.setdefault(r["booth_uid"], [0.0] * 4)[BLOC_ORDER.index(bloc)] += r["votes"]
    for uid, v in out.items():
        total = sum(v) or 1.0
        out[uid] = [x / total for x in v]
    return out


# --------------------------------------------------------------------------
# Roll
# --------------------------------------------------------------------------

def estimate_electors(rows: list[dict], polls: list[dict[str, int]], total: int) -> list[int]:
    """Electors per booth for one roll; see the module docstring."""
    n = len(rows)
    settings = {r["setting"] for r in rows}
    count = {s: sum(1 for r in rows if r["setting"] == s) for s in settings}
    estimates = []
    for poll in polls:
        # Turnout per setting such that both settings get the same mean roll.
        turnout = {s: sum(poll.get(r["booth_uid"], 0) for r in rows if r["setting"] == s)
                   / (total * count[s] / n) for s in settings}
        estimates.append([poll.get(r["booth_uid"], 0) / turnout[r["setting"]] for r in rows])
    base = [sum(e[i] for e in estimates) / len(estimates) for i in range(n)]
    mean = {s: sum(base[i] for i in range(n) if rows[i]["setting"] == s) / count[s] for s in settings}
    shrunk = [(1 - SHRINK) * base[i] + SHRINK * mean[rows[i]["setting"]] for i in range(n)]
    floors = [max(p.get(r["booth_uid"], 0) for p in polls) for r in rows]
    caps = [max(MAX_ELECTORS, f) for f in floors]
    return apportion(total, shrunk, floors, caps)


def apply_roll(ac_id: int) -> dict:
    rows = booths(ac_id)
    stats = {}
    with cursor() as cur:
        cur.execute("DELETE FROM roll_revision WHERE ac_id = %s AND label LIKE 'modelled-%%'", (ac_id,))
    revision_ids, electors_by_label = {}, {}
    for label, date, elections, total, _male, female, other, age_18_19, source in ROLLS:
        polls = [p for p in (polled(ac_id, e) for e in elections) if p]
        if not polls:
            log.warning("roll %s: no votes loaded for %s; skipped", label, elections)
            continue
        electors = estimate_electors(rows, polls, total)
        electors_by_label[label] = electors
        women = None
        if female is not None:
            weights = [e * (r["sex_ratio"] / (1000 + r["sex_ratio"])) for e, r in zip(electors, rows, strict=True)]
            women = apportion(female, weights, caps=electors)
        young = apportion(age_18_19, electors, caps=electors) if age_18_19 else None
        with cursor() as cur:
            cur.execute(
                "INSERT INTO roll_revision (ac_id, revision_date, label, is_mother, source_doc) "
                "VALUES (%s, %s, %s, true, %s) RETURNING revision_id",
                (ac_id, date, label, f"{LAYERS['roll']['filename']} - totals: {source}"))
            rid = cur.fetchone()["revision_id"]
            revision_ids[label] = rid
            snapshot_rows = []
            for i, r in enumerate(rows):
                e = electors[i]
                f = women[i] if women else None
                third = 0 if other is not None else None
                m = e - f - (third or 0) if f is not None else None
                y = young[i] if young else None
                band_20_29 = (round(e * STATE_18_29) - y) if y is not None and label == "modelled-vs2024" else None
                band_60 = round(e * STATE_60_PLUS) if label == "modelled-vs2024" else None
                snapshot_rows.append((rid, ac_id, r["booth_uid"], e, m, f, third, y,
                                      max(0, band_20_29) if band_20_29 is not None else None, band_60))
            cur.executemany(
                "INSERT INTO roll_snapshot (revision_id, ac_id, booth_uid, electors, male, female, "
                "other, age_18_19, age_20_29, age_60p) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                snapshot_rows)
            if other:
                # The AC's third-gender electors: on the booth with most electors, as one count.
                i = max(range(len(rows)), key=lambda k: electors[k])
                cur.execute("UPDATE roll_snapshot SET other = %s, male = male - %s "
                            "WHERE revision_id = %s AND booth_uid = %s",
                            (other, other, rid, rows[i]["booth_uid"]))
        stats[label] = sum(electors)

    # Change VS-2019 -> VS-2024, on the 2024 revision.
    if "modelled-vs2019" in electors_by_label and "modelled-vs2024" in electors_by_label:
        then, now = electors_by_label["modelled-vs2019"], electors_by_label["modelled-vs2024"]
        young = apportion(13_881, now, caps=now)
        with cursor() as cur:
            for i, r in enumerate(rows):
                net = now[i] - then[i]
                additions = max(net, young[i])
                cur.execute(
                    "INSERT INTO roll_change (ac_id, revision_id, booth_uid, additions, deletions, "
                    "modifications, add_18_19, source_doc) VALUES (%s, %s, %s, %s, %s, 0, %s, %s)",
                    (ac_id, revision_ids["modelled-vs2024"], r["booth_uid"], additions,
                     additions - net, young[i],
                     f"{LAYERS['roll']['filename']} - net change VS-2019 to VS-2024; gross additions "
                     f"at least the 18-19 year olds"))
    with cursor() as cur:
        for election, label in LINKS.items():
            if label not in revision_ids:
                continue
            cur.execute(
                "INSERT INTO election_roll_link (election_id, revision_id) "
                "SELECT e.election_id, %s FROM election e WHERE e.ac_id = %s AND e.label = %s "
                "ON CONFLICT (election_id) DO UPDATE SET revision_id = EXCLUDED.revision_id",
                (revision_ids[label], ac_id, election))
    return stats


# --------------------------------------------------------------------------
# Community
# --------------------------------------------------------------------------

def _affinity() -> tuple[list[str], list[str], list[list[float]]]:
    rows = _rows(AFFINITY)
    groups = [r["group"] for r in rows]
    names = [r["community_en"] for r in rows]
    matrix = []
    for r in rows:
        v = [float(r[b]) for b in BLOC_ORDER]
        matrix.append([x / sum(v) for x in v])
    return groups, names, matrix


def fit_mix(shares: list[list[float]], prior: dict[str, float | None], groups: list[str],
            matrix: list[list[float]], centre: list[float] | None = None) -> list[float]:
    """Group shares that best reproduce the booth's bloc shares, near the census priors.

    `centre` (the unit's average mix from a first pass) pulls every group gently
    towards its unit, so one booth's votes alone cannot push a group to 0 or 100%.
    """
    import numpy as np
    from scipy.optimize import lsq_linear

    a_rows, b_rows = [], []
    for s in shares:
        for k in range(len(BLOC_ORDER)):
            a_rows.append([matrix[g][k] for g in range(len(groups))])
            b_rows.append(s[k])
    weights = {"sc": 3.0, "st": 3.0, "muslim": 0.5}
    for g, name in enumerate(groups):
        if prior.get(name) is not None and name in weights:
            row = [0.0] * len(groups)
            row[g] = weights[name]
            a_rows.append(row)
            b_rows.append(weights[name] * prior[name])
    if centre is not None:
        for g in range(len(groups)):
            row = [0.0] * len(groups)
            row[g] = RIDGE
            a_rows.append(row)
            b_rows.append(RIDGE * centre[g])
    a_rows.append([10.0] * len(groups))
    b_rows.append(10.0)
    fit = lsq_linear(np.array(a_rows), np.array(b_rows), bounds=(0.0, 1.0))
    x = np.clip(fit.x, 0, None)
    return list(x / x.sum())


def rake_muslim(rows: list[dict], mixes: list[list[float]], electors: list[int], groups: list[str]) -> None:
    """Scale the Muslim share unit by unit to Census 2011, taking it from the non-census groups."""
    m = groups.index("muslim")
    free = [groups.index(g) for g in ("kurmi", "other_hindu")]
    for unit, target in MUSLIM_SHARE.items():
        idx = [i for i, r in enumerate(rows) if r["unit"] == unit]
        if not idx:
            continue
        for _ in range(20):
            weight = sum(electors[i] for i in idx) or 1
            current = sum(mixes[i][m] * electors[i] for i in idx) / weight
            if current <= 0 or abs(current - target) < 1e-4:
                break
            factor = target / current
            for i in idx:
                new = min(mixes[i][m] * factor, mixes[i][m] + sum(mixes[i][j] for j in free))
                delta = new - mixes[i][m]
                pool = sum(mixes[i][j] for j in free)
                for j in free:
                    mixes[i][j] -= delta * (mixes[i][j] / pool) if pool else 0
                mixes[i][m] = new


def apply_caste(ac_id: int) -> int:
    groups, names, matrix = _affinity()
    community = {r["name_en"]: r["community_id"] for r in query("SELECT community_id, name_en FROM community")}
    missing = [n for n in names if n not in community]
    if missing:
        log.warning("communities not seeded: %s; run db.seed.load_seed --only communities", missing)
        return 0
    rows = booths(ac_id)
    vs, ls = bloc_shares(ac_id, "VS-2024"), bloc_shares(ac_id, "LS-2024")
    snap = {r["booth_uid"]: r["electors"] for r in query(
        "SELECT s.booth_uid, s.electors FROM roll_snapshot s JOIN roll_revision r "
        "ON r.revision_id = s.revision_id WHERE r.ac_id = %s AND r.label = 'modelled-vs2024'", (ac_id,))}
    electors = [snap.get(r["booth_uid"], 0) for r in rows]
    def fit_all(centres: dict[str, list[float]] | None) -> list[list[float]]:
        out = []
        for r in rows:
            shares = [s[r["booth_uid"]] for s in (vs, ls) if r["booth_uid"] in s]
            prior = {"sc": r["sc"], "st": r["st"], "muslim": MUSLIM_SHARE[r["unit"]]}
            centre = centres.get(r["unit"]) if centres else None
            out.append(fit_mix(shares, prior, groups, matrix, centre) if shares else
                       (centre or [prior.get(g) or 0.0 for g in groups]))
        return out

    first = fit_all(None)
    centres = {}
    for unit in {r["unit"] for r in rows}:
        idx = [i for i, r in enumerate(rows) if r["unit"] == unit]
        weight = sum(electors[i] for i in idx) or 1
        centres[unit] = [sum(first[i][g] * electors[i] for i in idx) / weight for g in range(len(groups))]
    mixes = fit_all(centres)
    rake_muslim(rows, mixes, electors, groups)
    confidence = {"sc": 0.55, "st": 0.55, "muslim": 0.4, "kurmi": 0.3, "other_hindu": 0.3}
    count = 0
    with cursor() as cur:
        cur.execute("DELETE FROM caste_estimate WHERE ac_id = %s AND source = 'blend'", (ac_id,))
        values = []
        for r, mix, e in zip(rows, mixes, electors, strict=True):
            located = 0.0 if r["located"] >= 0.45 else 0.1
            for g, name, share in zip(groups, names, mix, strict=True):
                values.append((ac_id, r["booth_uid"], community[name], round(e * share) if e else None,
                               round(100 * share, 2), round(confidence[g] - located, 2), METHOD_VERSION))
        cur.executemany(
            "INSERT INTO caste_estimate (ac_id, booth_uid, community_id, est_count, est_pct, "
            "confidence, source, method_version) VALUES (%s, %s, %s, %s, %s, %s, 'blend', %s)",
            values)
        count = len(values)
    return count


# --------------------------------------------------------------------------
# Clean-up of the earlier modelled layers
# --------------------------------------------------------------------------

def drop_modelled_ls_segment(ac_id: int) -> int:
    """LS-2024 booth results are real now; remove the modelled segment's leftovers."""
    with cursor() as cur:
        cur.execute(
            "DELETE FROM result_booth rb USING candidate c WHERE rb.candidate_id = c.candidate_id "
            "AND c.ac_id = %s AND c.name_en LIKE '%%segment, modelled)'", (ac_id,))
        removed = cur.rowcount
        cur.execute("DELETE FROM candidate WHERE ac_id = %s AND name_en LIKE '%%segment, modelled)'", (ac_id,))
        cur.execute("DELETE FROM source_doc WHERE ac_id = %s AND filename = 'modelled/ls2024_segment_ac32.json'",
                    (ac_id,))
    return removed


def run(ac_number: int = AC_NUMBER) -> dict:
    ac_id = _ac_id(ac_number)
    for layer in LAYERS:
        _register_doc(layer, ac_id)
    stats = {"ls_modelled_rows_removed": drop_modelled_ls_segment(ac_id)}
    stats["roll"] = apply_roll(ac_id)
    stats["caste_rows"] = apply_caste(ac_id)
    log.info("modelled_overlay: %s", stats)
    return stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Modelled layers fitted to the real loads")
    ap.add_argument("--ac", type=int, default=AC_NUMBER)
    ap.add_argument("--cleanup-only", action="store_true",
                    help="only remove the old modelled LS segment (before loading the real LS Form 20)")
    args = ap.parse_args(argv)
    if args.cleanup_only:
        print({"ls_modelled_rows_removed": drop_modelled_ls_segment(_ac_id(args.ac))})
        return 0
    print(run(args.ac))
    return 0


if __name__ == "__main__":
    sys.exit(main())
