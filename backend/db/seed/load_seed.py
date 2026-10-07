"""Load db/seed/*.csv into the database. Idempotent - safe to re-run.

    python -m db.seed.load_seed
    python -m db.seed.load_seed --only parties,surnames

Everything constituency-specific is keyed by `ac_number`, not by a surrogate id,
because the CSVs are edited by hand and an AC number is a fact an operator can
check against a PS list header. Blocks get a legible generated id
(`ac_number * 100 + n`, so 3201 is Giridih's first block) which cannot collide
across constituencies.

**Nothing here invents a constituency fact.** The five ACs beyond Giridih are
seeded from `MULTI_AC_EXPANSION_SPEC.md` 1, which itself says every figure must
be verified against ECI/CEO Jharkhand before use, so those rows carry
`verified=false` and their blocks carry `source='spec-unverified'`. Where the
spec gives a margin but no vote totals - Gandey, Tundi, Silli - no
`result_ac_total` row is written at all. A margin is not a total and deriving one
from the other would be fabrication.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from common.db import cursor, query
from common.logging_setup import get_logger
from common.textnorm import alias_key, normalize_text

log = get_logger(__name__)
SEED_DIR = Path(__file__).resolve().parent


def _rows(name: str) -> list[dict[str, str]]:
    path = SEED_DIR / name
    if not path.exists():
        log.warning("seed file missing: %s", name)
        return []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return [r for r in csv.DictReader(fh) if any(v.strip() for v in r.values() if v)]


def _bool(v: str | None) -> bool:
    return str(v).strip().lower() in {"1", "true", "yes", "y"}


def _int(v: str | None) -> int | None:
    v = (v or "").strip()
    return int(v) if v else None


def _ac_ids() -> dict[int, int]:
    """ac_number -> ac_id."""
    return {r["ac_number"]: r["ac_id"] for r in query("SELECT ac_id, ac_number FROM ac")}


def _party_ids() -> dict[str, int]:
    return {r["abbr"]: r["party_id"] for r in query("SELECT party_id, abbr FROM party")}


# ---------------------------------------------------------------------------
# Spine
# ---------------------------------------------------------------------------


def load_acs() -> int:
    """The six constituencies, their districts and their parent PC.

    `districts` is pipe-separated because Dumri spans Giridih and Bokaro; the
    first named district is the primary one for display.
    """
    rows = _rows("ac.csv")
    loaded = 0
    with cursor() as cur:
        cur.execute("SELECT state_id FROM state WHERE name_en = 'Jharkhand'")
        state = cur.fetchone()
        if state is None:
            log.error("no Jharkhand row in `state`; apply migration 0014 first")
            return 0
        state_id = state["state_id"]

        for r in rows:
            ac_number = int(r["ac_number"])
            pc_number = _int(r.get("pc_number"))
            cur.execute(
                "SELECT pc_id FROM pc WHERE state_id = %s AND pc_number = %s",
                (state_id, pc_number),
            )
            pc = cur.fetchone()
            if pc is None and pc_number is not None:
                log.warning("ac.csv: AC %s names PC %s, which is not seeded", ac_number, pc_number)

            cur.execute(
                "INSERT INTO ac (state_id, ac_number, name_en, name_hi, reservation, pc_id, "
                "bypoll_due, vacancy_date, verified, notes) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (state_id, ac_number) DO UPDATE SET "
                "  name_en = EXCLUDED.name_en, name_hi = EXCLUDED.name_hi, "
                "  reservation = EXCLUDED.reservation, pc_id = EXCLUDED.pc_id, "
                "  bypoll_due = EXCLUDED.bypoll_due, vacancy_date = EXCLUDED.vacancy_date, "
                "  notes = EXCLUDED.notes, "
                # Never re-assert verified=false over a human's verified=true. A
                # re-seed must not silently undo a reconciliation someone did.
                "  verified = ac.verified OR EXCLUDED.verified "
                "RETURNING ac_id",
                (state_id, ac_number, r["name_en"], r["name_hi"], r["reservation"],
                 pc["pc_id"] if pc else None, r.get("bypoll_due") or None,
                 r.get("vacancy_date") or None, _bool(r.get("verified")), r.get("notes") or None),
            )
            ac_id = cur.fetchone()["ac_id"]

            names = [d.strip() for d in (r.get("districts") or "").split("|") if d.strip()]
            for index, name in enumerate(names):
                cur.execute(
                    "SELECT district_id FROM district WHERE state_id = %s AND name_en = %s",
                    (state_id, name),
                )
                district = cur.fetchone()
                if district is None:
                    log.warning("ac.csv: AC %s names district %r, which is not seeded",
                                ac_number, name)
                    continue
                cur.execute(
                    "INSERT INTO ac_district (ac_id, district_id, is_primary) VALUES (%s, %s, %s) "
                    "ON CONFLICT (ac_id, district_id) DO UPDATE SET is_primary = EXCLUDED.is_primary",
                    (ac_id, district["district_id"], index == 0),
                )
            loaded += 1
    return loaded


def load_blocks() -> int:
    rows = _rows("ac_blocks.csv")
    acs = _ac_ids()
    loaded, per_ac = 0, {}
    with cursor() as cur:
        for r in rows:
            ac_number = int(r["ac_number"])
            ac_id = acs.get(ac_number)
            if ac_id is None:
                log.warning("ac_blocks.csv: AC %s is not seeded", ac_number)
                continue
            per_ac[ac_number] = per_ac.get(ac_number, 0) + 1
            block_id = ac_number * 100 + per_ac[ac_number]
            cur.execute(
                "INSERT INTO block (block_id, ac_id, name_en, name_hi, kind) "
                "VALUES (%s, %s, %s, %s, %s) "
                "ON CONFLICT (block_id) DO UPDATE SET ac_id = EXCLUDED.ac_id, "
                "name_en = EXCLUDED.name_en, name_hi = EXCLUDED.name_hi, kind = EXCLUDED.kind",
                (block_id, ac_id, r["name_en"], r["name_hi"], r["kind"]),
            )
            loaded += 1
    return loaded


def load_areas() -> int:
    """Wards and panchayats, resolved to a block by (ac_number, block name).

    `areas_panchayats.csv` is generated from the Local Government Directory by
    scripts/build_panchayats.py - the official list, not names anyone typed. LGD
    has no Hindi panchayat names, so `name_hi` repeats the English one rather
    than carry a guessed transliteration. See db/seed/README.md.
    """
    rows = _rows("areas_wards.csv") + _rows("areas_panchayats.csv")
    acs = _ac_ids()
    loaded = 0
    with cursor() as cur:
        for r in rows:
            ac_number = int(r["ac_number"])
            ac_id = acs.get(ac_number)
            if ac_id is None:
                log.warning("areas: AC %s is not seeded", ac_number)
                continue
            cur.execute(
                "SELECT block_id FROM block WHERE ac_id = %s AND name_en = %s",
                (ac_id, r["block_name_en"]),
            )
            block = cur.fetchone()
            if block is None:
                log.warning("areas: AC %s has no block named %r", ac_number, r["block_name_en"])
                continue
            # A panchayat whose constituency changed (D-013: the 2024 polling-
            # station list moved Giridih-block panchayats between AC 31 and 32)
            # is moved, not duplicated: office holders, local results and news
            # tags hang off its area_id.
            if r.get("code"):
                cur.execute(
                    "UPDATE area SET block_id = %s, ac_id = %s, name_en = %s, name_hi = %s "
                    "WHERE kind = %s AND code = %s AND block_id <> %s "
                    "AND NOT EXISTS (SELECT 1 FROM area x WHERE x.block_id = %s "
                    "                AND x.kind = %s AND x.name_en = %s)",
                    (block["block_id"], ac_id, r["name_en"], r["name_hi"], r["kind"], r["code"],
                     block["block_id"], block["block_id"], r["kind"], r["name_en"]),
                )
            cur.execute(
                "INSERT INTO area (block_id, ac_id, kind, name_en, name_hi, code, census_code) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (block_id, kind, name_en) DO UPDATE SET name_hi = EXCLUDED.name_hi, "
                "ac_id = EXCLUDED.ac_id, code = EXCLUDED.code, "
                "census_code = COALESCE(EXCLUDED.census_code, area.census_code) "
                "RETURNING area_id",
                (block["block_id"], ac_id, r["kind"], r["name_en"], r["name_hi"],
                 r.get("code") or None, r.get("census_code") or None),
            )
            area_id = cur.fetchone()["area_id"]
            for text, script in ((r["name_en"], "en"), (r["name_hi"], "hi")):
                key = alias_key(text)
                if not key:
                    continue
                cur.execute(
                    "INSERT INTO area_alias (alias, area_id, script, source) "
                    "VALUES (%s, %s, %s, 'seed') ON CONFLICT (alias) DO NOTHING",
                    (key, area_id, script),
                )
            loaded += 1
    return loaded


def _area_id(cur, ac_id: int, block_name: str, kind: str, name: str) -> int | None:
    cur.execute(
        "SELECT a.area_id FROM area a JOIN block b ON b.block_id = a.block_id "
        "WHERE a.ac_id = %s AND b.name_en = %s AND a.kind = %s AND a.name_en = %s",
        (ac_id, block_name, kind, name),
    )
    row = cur.fetchone()
    return row["area_id"] if row else None


def load_area_aliases() -> int:
    """Other names for an area - a panchayat's villages - from area_aliases.csv
    (scripts/build_panchayats.py). A name already taken by another area keeps
    its first owner: area_alias.alias is unique, and a village name that the
    build found in two panchayats is not in the file at all."""
    acs = _ac_ids()
    loaded = 0
    with cursor() as cur:
        for r in _rows("area_aliases.csv"):
            ac_id = acs.get(int(r["ac_number"]))
            area_id = ac_id and _area_id(cur, ac_id, r["block_name_en"], r["kind"],
                                         r["area_name_en"])
            key = alias_key(r["alias"])
            if not area_id or not key:
                continue
            cur.execute(
                "INSERT INTO area_alias (alias, area_id, script, source) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT (alias) DO NOTHING",
                (key, area_id, r["script"], r["source"]),
            )
            loaded += cur.rowcount
    return loaded


def load_area_boundaries() -> int:
    """Panchayat and ward polygons from geo/areas.json (scripts/build_panchayats.py),
    with the label point as the area centroid the geocoder falls back on."""
    import json

    path = SEED_DIR / "geo" / "areas.json"
    if not path.exists():
        log.warning("seed file missing: geo/areas.json")
        return 0
    data = json.loads(path.read_text(encoding="utf-8"))
    acs = _ac_ids()
    loaded = 0
    with cursor() as cur:
        for feature in data["features"]:
            props = feature["properties"]
            ac_id = acs.get(props["ac_number"])
            area_id = ac_id and _area_id(cur, ac_id, props["block_name_en"], props["kind"],
                                         props["name_en"])
            if not area_id:
                continue
            lon, lat = props["label_point"]
            cur.execute(
                "UPDATE area SET boundary = %s::jsonb, boundary_source = %s, "
                "centroid_lon = %s, centroid_lat = %s WHERE area_id = %s",
                (json.dumps(feature["geometry"]), data["sources"][props["source"]],
                 lon, lat, area_id),
            )
            loaded += cur.rowcount
    return loaded


def load_poll_dates() -> int:
    """Poll date and phase of each contest, from poll_dates.csv. Only dates a
    source confirms are listed; a contest without one keeps NULL, and the area
    news window then says it has no date rather than use a guessed one. Kanke
    VS-2024 is absent because the sources found disagree on its phase."""
    acs = _ac_ids()
    loaded = 0
    with cursor() as cur:
        for r in _rows("poll_dates.csv"):
            ac_id = acs.get(int(r["ac_number"]))
            if ac_id is None:
                continue
            cur.execute(
                "UPDATE election e SET poll_date = %s, phase = %s FROM election_event ev "
                "WHERE ev.event_id = e.event_id AND ev.label = %s AND e.ac_id = %s",
                (r["poll_date"], int(r["phase"]) if r["phase"] else None,
                 r["election_label"], ac_id),
            )
            loaded += cur.rowcount
    return loaded


# ---------------------------------------------------------------------------
# Parties
# ---------------------------------------------------------------------------


def load_parties() -> int:
    rows = _rows("parties.csv")
    with cursor() as cur:
        for r in rows:
            cur.execute(
                "INSERT INTO party (abbr, name_en, name_hi, alliance_2024, colour) "
                "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (abbr) DO UPDATE SET "
                "name_en = EXCLUDED.name_en, name_hi = EXCLUDED.name_hi, "
                "alliance_2024 = EXCLUDED.alliance_2024, colour = EXCLUDED.colour",
                (r["abbr"], r["name_en"], r["name_hi"], r["alliance_2024"], r["colour"]),
            )
    return len(rows)


def load_party_aliases() -> int:
    """Every spelling of a party we have seen, in either script.

    This is what makes audit C1 go away. `resolve_candidates` built its lookup
    from `abbr` and `name_en` only - never `name_hi`, which is seeded for all
    thirteen parties - so a Devanagari Form 20 header could not match by
    construction, every candidate loaded with party_id NULL, and
    mv_result_booth_wide reported a 100% margin for a party that does not exist.

    Aliases are normalised through the same `alias_key` the area matcher uses,
    so a header cell gets the same treatment as the seed did.
    """
    rows = _rows("party_alias.csv")
    parties = _party_ids()
    loaded, unknown = 0, set()
    with cursor() as cur:
        for r in rows:
            pid = parties.get(r["party_abbr"].strip())
            if pid is None:
                unknown.add(r["party_abbr"])
                continue
            for value in {r["alias"].strip(), alias_key(r["alias"])}:
                if not value:
                    continue
                cur.execute(
                    "INSERT INTO party_alias (alias, party_id, script, source) "
                    "VALUES (%s, %s, %s, %s) ON CONFLICT (alias) DO UPDATE SET "
                    "party_id = EXCLUDED.party_id, script = EXCLUDED.script, "
                    "source = EXCLUDED.source",
                    (value, pid, r.get("script") or "other", r.get("source") or None),
                )
            loaded += 1
    if unknown:
        log.error("party_alias.csv references unknown parties: %s", sorted(unknown))
    return loaded


def load_party_alliances() -> int:
    """Alliance membership per event. JVM merged into BJP in 2020 and AJSU has
    been in and out of the NDA, so a single column on `party` cannot express it
    and the scenario engine needs the alliance as it stood at the event."""
    rows = _rows("party_alliance.csv")
    parties = _party_ids()
    events = {r["label"]: r["event_id"] for r in query("SELECT event_id, label FROM election_event")}
    loaded, unknown_party, unknown_event = 0, set(), set()
    with cursor() as cur:
        for r in rows:
            pid = parties.get(r["party_abbr"].strip())
            eid = events.get(r["event_label"].strip())
            if pid is None:
                unknown_party.add(r["party_abbr"])
                continue
            if eid is None:
                unknown_event.add(r["event_label"])
                continue
            cur.execute(
                "INSERT INTO party_alliance (party_id, event_id, alliance) VALUES (%s, %s, %s) "
                "ON CONFLICT (party_id, event_id) DO UPDATE SET alliance = EXCLUDED.alliance",
                (pid, eid, r["alliance"]),
            )
            loaded += 1
    if unknown_party:
        log.error("party_alliance.csv references unknown parties: %s", sorted(unknown_party))
    if unknown_event:
        log.error("party_alliance.csv references unknown events: %s", sorted(unknown_event))
    return loaded


# ---------------------------------------------------------------------------
# Elections
# ---------------------------------------------------------------------------


def load_elections() -> int:
    """Events, then one contest row per event per active AC.

    A contest row is created for every AC even where no result is loaded. The
    contest happened; whether we hold its Form 20 is a separate question, and
    the row is what lets a page say "not loaded" for a specific election rather
    than showing nothing at all.
    """
    events = _rows("election_event.csv")
    acs = _ac_ids()
    loaded = 0
    with cursor() as cur:
        for r in events:
            cur.execute(
                "INSERT INTO election_event (type, year, label, count_date, is_bypoll) "
                "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (label) DO UPDATE SET "
                "count_date = COALESCE(EXCLUDED.count_date, election_event.count_date) "
                "RETURNING event_id",
                (r["type"], int(r["year"]), r["label"], r.get("count_date") or None,
                 _bool(r.get("is_bypoll"))),
            )
            event_id = cur.fetchone()["event_id"]
            is_baseline = _bool(r.get("is_baseline"))

            for _ac_number, ac_id in sorted(acs.items()):
                cur.execute(
                    "INSERT INTO election (event_id, ac_id, type, year, label, is_baseline, notes) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (event_id, ac_id) DO UPDATE SET "
                    "  label = EXCLUDED.label, is_baseline = EXCLUDED.is_baseline, "
                    "  notes = EXCLUDED.notes",
                    (event_id, ac_id,
                     # `election.type` keeps the old CHECK, which has no 'ULB'.
                     "WARD" if r["type"] == "ULB" else r["type"],
                     int(r["year"]), r["label"], is_baseline, r.get("notes") or None),
                )
                loaded += 1
    return loaded


def load_ac_contests() -> int:
    """The two parties a signed margin is measured between, per AC per event.

    Without this the contest pair was hardcoded to ("JMM", "BJP") in
    analytics/scenario.py, so a Dumri scenario reported a JMM/BJP winner for a
    seat JLKM actually holds (audit D5).
    """
    rows = _rows("ac_contest.csv")
    acs = _ac_ids()
    parties = _party_ids()
    events = {r["label"]: r["event_id"] for r in query("SELECT event_id, label FROM election_event")}
    loaded = 0
    with cursor() as cur:
        for r in rows:
            ac_id = acs.get(int(r["ac_number"]))
            event_id = events.get(r["event_label"].strip())
            a = parties.get(r["party_a"].strip())
            b = parties.get(r["party_b"].strip())
            if None in (ac_id, event_id, a, b):
                log.warning("ac_contest.csv: cannot resolve %s / %s / %s vs %s",
                            r["ac_number"], r["event_label"], r["party_a"], r["party_b"])
                continue
            cur.execute(
                "INSERT INTO ac_contest (ac_id, event_id, party_a, party_b, source) "
                "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (ac_id, event_id) DO UPDATE SET "
                "party_a = EXCLUDED.party_a, party_b = EXCLUDED.party_b, source = EXCLUDED.source",
                (ac_id, event_id, a, b, r.get("source") or None),
            )
            loaded += 1
    return loaded


def load_ac_totals() -> int:
    """AC-level published totals: the Form 20 validation target (LLD 4.2).

    Candidates named here are created if absent so the totals have something to
    hang off; the Form 20 parser fuzzy-matches its column headers onto them.

    Rows are per (AC, election label). Where the spec gives only a margin -
    Gandey, Tundi, Silli - there is no row, deliberately: a margin is not a
    total and inventing totals to fit one would be fabrication.
    """
    rows = _rows("ac_totals.csv")
    acs = _ac_ids()
    parties = _party_ids()
    elections = {
        (r["ac_id"], r["label"]): r["election_id"]
        for r in query("SELECT election_id, ac_id, label FROM election")
    }
    loaded = 0
    with cursor() as cur:
        for r in rows:
            ac_id = acs.get(int(r["ac_number"]))
            if ac_id is None:
                log.warning("ac_totals.csv: AC %s is not seeded", r["ac_number"])
                continue
            eid = elections.get((ac_id, r["election_label"]))
            if eid is None:
                log.warning("ac_totals.csv: AC %s has no election %s",
                            r["ac_number"], r["election_label"])
                continue

            cand_id = None
            name = (r.get("candidate_name") or "").strip()
            abbr = (r.get("party_abbr") or "").strip()
            if name:
                pid = parties.get(abbr)
                # A candidate whose party became known (UNK -> BSP) keeps its row:
                # re-party it instead of inserting a second candidate of the same name.
                cur.execute(
                    "UPDATE candidate c SET party_id = %s WHERE c.election_id = %s AND c.name_en = %s "
                    "AND c.party_id IS DISTINCT FROM %s AND NOT EXISTS (SELECT 1 FROM candidate x "
                    "  WHERE x.election_id = c.election_id AND x.name_en = c.name_en "
                    "  AND x.party_id IS NOT DISTINCT FROM %s)",
                    (pid, eid, name, pid, pid),
                )
                cur.execute(
                    "INSERT INTO candidate (election_id, ac_id, name_en, party_id) "
                    "VALUES (%s, %s, %s, %s) "
                    "ON CONFLICT (election_id, name_en, party_id) DO UPDATE SET "
                    "name_en = EXCLUDED.name_en, ac_id = EXCLUDED.ac_id "
                    "RETURNING candidate_id",
                    (eid, ac_id, name, pid),
                )
                cand_id = cur.fetchone()["candidate_id"]
                # Twins left by an older load under another party: drop them
                # (and their totals) when no booth votes hang off them.
                cur.execute(
                    "DELETE FROM result_ac_total t USING candidate c WHERE t.candidate_id = c.candidate_id "
                    "AND c.election_id = %s AND c.name_en = %s AND c.candidate_id <> %s "
                    "AND NOT EXISTS (SELECT 1 FROM result_booth rb WHERE rb.candidate_id = c.candidate_id)",
                    (eid, name, cand_id),
                )
                cur.execute(
                    "DELETE FROM candidate c WHERE c.election_id = %s AND c.name_en = %s "
                    "AND c.candidate_id <> %s "
                    "AND NOT EXISTS (SELECT 1 FROM result_booth rb WHERE rb.candidate_id = c.candidate_id)",
                    (eid, name, cand_id),
                )
            elif abbr and r["metric"] == "votes":
                # Kanke's JLKM total is published without a candidate name. The
                # party total is still a real published figure, so it is kept
                # against a placeholder candidate rather than dropped.
                pid = parties.get(abbr)
                cur.execute(
                    "INSERT INTO candidate (election_id, ac_id, name_en, party_id) "
                    "VALUES (%s, %s, %s, %s) "
                    "ON CONFLICT (election_id, name_en, party_id) DO UPDATE SET "
                    "ac_id = EXCLUDED.ac_id RETURNING candidate_id",
                    (eid, ac_id, f"({abbr} candidate, name not in source)", pid),
                )
                cand_id = cur.fetchone()["candidate_id"]

            # result_ac_total is keyed by a COALESCE expression index, and
            # ON CONFLICT inference against an expression index is brittle.
            # Delete-then-insert is equivalent here and cannot mis-infer.
            cur.execute(
                "DELETE FROM result_ac_total WHERE election_id = %s AND metric = %s "
                "AND candidate_id IS NOT DISTINCT FROM %s",
                (eid, r["metric"], cand_id),
            )
            cur.execute(
                "INSERT INTO result_ac_total (election_id, ac_id, candidate_id, metric, value, source) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (eid, ac_id, cand_id, r["metric"], int(r["value"]), r.get("source") or None),
            )
            loaded += 1
    return loaded


# ---------------------------------------------------------------------------
# Unchanged sections
# ---------------------------------------------------------------------------


def load_communities() -> int:
    rows = _rows("communities.csv")
    with cursor() as cur:
        for r in rows:
            cur.execute(
                "INSERT INTO community (name_en, name_hi, category, sort_order) "
                "VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (name_en) DO UPDATE SET name_hi = EXCLUDED.name_hi, "
                "category = EXCLUDED.category, sort_order = EXCLUDED.sort_order",
                (r["name_en"], r["name_hi"], r["category"], int(r["sort_order"])),
            )
    return len(rows)


def load_surnames() -> int:
    rows = _rows("surname_dict.csv")
    comm = {r["name_en"]: r["community_id"]
            for r in query("SELECT community_id, name_en FROM community")}
    loaded, unknown = 0, set()
    with cursor() as cur:
        for r in rows:
            cid = comm.get(r["community"])
            if cid is None:
                unknown.add(r["community"])
                continue
            cur.execute(
                "INSERT INTO surname_dict (surname_hi, surname_en, community_id, weight, notes) "
                "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (surname_hi, community_id) DO UPDATE SET "
                "surname_en = EXCLUDED.surname_en, weight = EXCLUDED.weight, notes = EXCLUDED.notes",
                (normalize_text(r["surname_hi"]), r.get("surname_en") or None, cid,
                 float(r["weight"]), r.get("notes") or None),
            )
            loaded += 1
    if unknown:
        log.error("surname_dict.csv references unknown communities: %s", sorted(unknown))
    return loaded


def load_news_sources() -> int:
    rows = _rows("news_sources.csv")
    with cursor() as cur:
        for r in rows:
            cur.execute(
                "INSERT INTO news_source (name, kind, url, lang, is_active) "
                "VALUES (%s, %s, %s, %s, %s) "
                "ON CONFLICT (name) DO UPDATE SET url = EXCLUDED.url, kind = EXCLUDED.kind, "
                "lang = EXCLUDED.lang, is_active = EXCLUDED.is_active",
                (r["name"], r["kind"], r["url"], r["lang"], _bool(r.get("is_active"))),
            )
    return len(rows)


def load_knowledge_cards() -> int:
    """Markdown files in knowledge_cards/ with a small YAML-ish front matter.

    A card may name an `ac` in its front matter; cards without one are general
    guidance that applies to every constituency (the caste guardrails and data
    provenance cards), and keeping those AC-agnostic is deliberate.
    """
    folder = SEED_DIR / "knowledge_cards"
    if not folder.exists():
        return 0
    acs = _ac_ids()
    loaded = 0
    with cursor() as cur:
        for path in sorted(folder.glob("*.md")):
            meta, body = _split_front_matter(path.read_text(encoding="utf-8"))
            slug = meta.get("slug") or path.stem
            sources = [s.strip() for s in meta.get("sources", "").split("|") if s.strip()]
            ac_id = acs.get(_int(meta.get("ac")) or -1)
            cur.execute(
                "INSERT INTO knowledge_card (slug, ac_id, topic, title_hi, title_en, body_hi, "
                "body_en, sources, last_reviewed, in_prompt) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (slug) DO UPDATE SET topic = EXCLUDED.topic, ac_id = EXCLUDED.ac_id, "
                "title_hi = EXCLUDED.title_hi, title_en = EXCLUDED.title_en, "
                "body_hi = EXCLUDED.body_hi, body_en = EXCLUDED.body_en, "
                "sources = EXCLUDED.sources, last_reviewed = EXCLUDED.last_reviewed, "
                "in_prompt = EXCLUDED.in_prompt",
                (slug, ac_id, meta.get("topic", "general"), meta.get("title_hi"),
                 meta.get("title_en"), body, body, sources,
                 meta.get("last_reviewed") or None, _bool(meta.get("in_prompt", "true"))),
            )
            loaded += 1
    return loaded


def _split_front_matter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    meta: dict[str, str] = {}
    for line in parts[1].splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta, parts[2].strip()


def load_ps_current_parts() -> int:
    """BLO current-part list (AC 32 parts 276-385) into ps_current_part."""
    path = SEED_DIR / "ps_list" / "giridih_current_parts.csv"
    if not path.exists():
        log.warning("seed file missing: ps_list/giridih_current_parts.csv")
        return 0
    acs = _ac_ids()
    loaded = 0
    with cursor() as cur:
        for r in _rows("ps_list/giridih_current_parts.csv"):
            ac_number = int(r["ac_number"])
            ac_id = acs.get(ac_number)
            if ac_id is None:
                continue
            block_id = None
            block_name = (r.get("block_name_en") or "").strip()
            if block_name:
                cur.execute(
                    "SELECT block_id FROM block WHERE ac_id = %s AND name_en = %s",
                    (ac_id, block_name),
                )
                block = cur.fetchone()
                block_id = block["block_id"] if block else None
            area_id = None
            panchayat_code = (r.get("panchayat_code") or "").strip()
            if panchayat_code:
                cur.execute(
                    "SELECT area_id FROM area WHERE ac_id = %s AND code = %s AND kind = 'panchayat'",
                    (ac_id, panchayat_code),
                )
                row = cur.fetchone()
                area_id = row["area_id"] if row else None
            part_number = int(r["part_number"])
            score = float(r["score"]) if (r.get("score") or "").strip() else None
            cur.execute(
                "INSERT INTO ps_current_part (ac_id, part_number, building_hi, village_hi, "
                "block_id, village_lgd, village_code, area_id, match_score, match_status, "
                "note, source) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (ac_id, part_number) DO UPDATE SET "
                "building_hi = EXCLUDED.building_hi, village_hi = EXCLUDED.village_hi, "
                "block_id = EXCLUDED.block_id, village_lgd = EXCLUDED.village_lgd, "
                "village_code = EXCLUDED.village_code, area_id = EXCLUDED.area_id, "
                "match_score = EXCLUDED.match_score, match_status = EXCLUDED.match_status, "
                "note = EXCLUDED.note, source = EXCLUDED.source",
                (
                    ac_id,
                    part_number,
                    r.get("building_hi") or None,
                    r.get("village_hi") or None,
                    block_id,
                    r.get("village_lgd") or None,
                    r.get("village_code") or None,
                    area_id,
                    score,
                    r.get("status") or None,
                    r.get("note") or None,
                    r.get("source") or "BLO list",
                ),
            )
            loaded += 1
    return loaded


def load_gp_officials() -> int:
    """GP portal JSON dumps into gp_official (roles the elected-office loader skips)."""
    import json

    repo_root = SEED_DIR.parents[2]  # giridih-monitor/ (not backend/)
    index_path = repo_root / "data_giridih" / "raw_gp" / "gp_index.json"
    if not index_path.exists():
        log.warning("data_giridih/raw_gp/gp_index.json missing")
        return 0
    index = json.loads(index_path.read_text(encoding="utf-8"))
    loaded = 0
    portal_source = "grampanchayat.jharkhand.gov.in"
    with cursor() as cur:
        for entries in index.values():
            for entry in entries:
                rel = (entry.get("file") or "").replace("../data_giridih/", "").lstrip("/")
                gp_path = (repo_root / "data_giridih" / rel).resolve()
                if not gp_path.is_file():
                    log.warning("gp file missing: %s", gp_path)
                    continue
                data = json.loads(gp_path.read_text(encoding="utf-8"))
                gp_id = str(data.get("gp_id") or entry.get("gp_id") or "")
                cur.execute(
                    "SELECT area_id FROM area WHERE kind = 'panchayat' AND code = %s",
                    (gp_id,),
                )
                area = cur.fetchone()
                if area is None:
                    log.warning("no area for gp_id %s (%s)", gp_id, gp_path.name)
                    continue
                area_id = area["area_id"]
                cur.execute(
                    "DELETE FROM gp_official WHERE area_id = %s AND source = %s",
                    (area_id, portal_source),
                )
                for member in data.get("members") or []:
                    name = (member.get("name") or "").strip()
                    if not name:
                        continue
                    portal_role = member.get("portal_role") or member.get("office") or "official"
                    office = member.get("office") or portal_role
                    cur.execute(
                        "INSERT INTO gp_official (area_id, gp_lgd_code, portal_role, office, "
                        "name, source) VALUES (%s, %s, %s, %s, %s, %s)",
                        (area_id, gp_id, portal_role, office, name, portal_source),
                    )
                    loaded += 1
    return loaded


def load_boundaries() -> int:
    """AC and block outlines from geo/boundaries.json (scripts/build_boundaries.py).

    Only seeded blocks receive a shape: a source block the seed does not list
    stays in the file, and on the map, but has no row here to attach to and
    is not created - adding a block to a constituency is a seed decision, not
    something a boundary file should do as a side effect.
    """
    import json

    path = SEED_DIR / "geo" / "boundaries.json"
    if not path.exists():
        log.warning("seed file missing: geo/boundaries.json")
        return 0
    data = json.loads(path.read_text(encoding="utf-8"))
    attribution = {k: v["attribution"] for k, v in data["sources"].items()}
    acs = _ac_ids()
    loaded = 0
    with cursor() as cur:
        for feature in data["features"]:
            props = feature["properties"]
            ac_id = acs.get(props["ac_number"])
            if ac_id is None:
                continue
            values = (json.dumps(feature["geometry"]), props["bbox"],
                      attribution[props["source"]])
            if props["layer"] == "ac":
                cur.execute(
                    "UPDATE ac SET boundary = %s::jsonb, boundary_bbox = %s, "
                    "boundary_source = %s WHERE ac_id = %s",
                    (*values, ac_id),
                )
                loaded += cur.rowcount
            elif props["layer"] == "block" and props["seeded"]:
                cur.execute(
                    "UPDATE block SET boundary = %s::jsonb, boundary_bbox = %s, "
                    "boundary_source = %s WHERE ac_id = %s AND name_en = %s",
                    (*values, ac_id, props["name_en"]),
                )
                loaded += cur.rowcount
    for warning in data.get("warnings", []):
        log.warning("boundaries AC-%s: %s", warning["ac_number"], warning["message"])
    return loaded


LOADERS = {
    "acs": load_acs,
    "blocks": load_blocks,
    "areas": load_areas,
    "parties": load_parties,
    "party_aliases": load_party_aliases,
    "communities": load_communities,
    "elections": load_elections,
    "party_alliances": load_party_alliances,
    "ac_contests": load_ac_contests,
    "surnames": load_surnames,
    "ac_totals": load_ac_totals,
    "news_sources": load_news_sources,
    "cards": load_knowledge_cards,
    "boundaries": load_boundaries,
    "area_aliases": load_area_aliases,
    "poll_dates": load_poll_dates,
    "area_boundaries": load_area_boundaries,
    "ps_current_parts": load_ps_current_parts,
    "gp_officials": load_gp_officials,
}

# Order matters: ACs before anything scoped to one, parties before aliases and
# alliances, events before alliances and contests, elections before ac_totals.
ORDER = ["acs", "blocks", "areas", "area_aliases", "parties", "party_aliases", "communities",
         "elections", "poll_dates", "party_alliances", "ac_contests", "surnames", "ac_totals",
         "news_sources", "cards", "boundaries", "area_boundaries", "ps_current_parts",
         "gp_officials"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Load seed data (idempotent)")
    ap.add_argument("--only", help="comma-separated subset: " + ",".join(ORDER))
    args = ap.parse_args(argv)

    wanted = [s.strip() for s in args.only.split(",")] if args.only else ORDER
    for name in wanted:
        fn = LOADERS.get(name)
        if fn is None:
            log.error("unknown seed section %r", name)
            return 2
        n = fn()
        log.info("seed %-16s %4d row(s)", name, n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
