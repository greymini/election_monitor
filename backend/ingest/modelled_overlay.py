"""Modelled estimates for gaps after the real Form 20 load (roll, geo, caste, LS segment).

Every layer registers a source_doc with method='modelled', is_synthetic=false.
"""

from __future__ import annotations

import argparse
import hashlib
import random
import sys

from common.db import cursor, query, query_one
from common.logging_setup import get_logger

log = get_logger(__name__)

SEED = 20241123
AC_NUMBER = 32
ELECTORS_TOTAL = 304_898

LAYERS = {
    "geo": {
        "filename": "modelled/geo_ac32.json",
        "kind": "other",
        "method_note": "Approximate location within the panchayat; exact station site pending PS list",
    },
    "roll": {
        "filename": "modelled/roll_ac32.json",
        "kind": "roll_mother",
        "method_note": "Electors apportioned from the published AC total of 3,04,898 in proportion to votes polled",
    },
    "caste": {
        "filename": "modelled/caste_ac32.json",
        "kind": "other",
        "method_note": "Modelled from district community shares; not a survey",
    },
    "ls_segment": {
        "filename": "modelled/ls2024_segment_ac32.json",
        "kind": "form20",
        "method_note": "Modelled from VS-2024 booth shares; LS Form 20 pending",
    },
}


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


def assign_booth_areas(ac_id: int) -> int:
    """Spread booths across real wards and panchayats (not the Form 20 placeholder)."""
    wards = query(
        "SELECT area_id FROM area WHERE ac_id = %s AND kind = 'ward' ORDER BY name_en",
        (ac_id,),
    )
    panchayats = query(
        "SELECT area_id FROM area WHERE ac_id = %s AND kind = 'panchayat' ORDER BY name_en",
        (ac_id,),
    )
    if not wards or not panchayats:
        log.warning("assign_booth_areas: missing ward or panchayat rows")
        return 0
    booths = query(
        "SELECT booth_uid, current_ps_number FROM booth WHERE ac_id = %s AND is_active "
        "ORDER BY current_ps_number NULLS LAST, booth_uid",
        (ac_id,),
    )
    if not booths:
        return 0
    urban_share = 0.42
    urban_n = round(len(booths) * urban_share)
    updated = 0
    with cursor() as cur:
        for i, booth in enumerate(booths):
            if i < urban_n:
                area_id = wards[i % len(wards)]["area_id"]
            else:
                area_id = panchayats[(i - urban_n) % len(panchayats)]["area_id"]
            cur.execute(
                "UPDATE booth SET area_id = %s, geocode_source = COALESCE(geocode_source, 'modelled'), "
                "notes = COALESCE(notes, '') || ' Area assignment modelled.' "
                "WHERE booth_uid = %s AND (area_id IS DISTINCT FROM %s)",
                (area_id, booth["booth_uid"], area_id),
            )
            updated += cur.rowcount
    return updated


def apply_geography(ac_number: int, seed: int) -> dict:
    import scripts.dev_geo as dev_geo

    dev_geo.SOURCE = "modelled: ingest/modelled_overlay.py"
    dev_geo.GEOCODE_SOURCE = "modelled"
    return dev_geo.run(ac_number, seed)


def apply_roll(ac_id: int, doc_id: int) -> int:
    from fixtures import giridih

    rng = random.Random(SEED)
    rows = query(
        "SELECT b.booth_uid, COALESCE(SUM(rb.votes), 0)::INT AS valid "
        "FROM booth b "
        "LEFT JOIN booth_crosswalk x ON x.booth_uid = b.booth_uid "
        "LEFT JOIN election e ON e.election_id = x.election_id AND e.type = 'VS' "
        "  AND e.label = 'VS-2024' "
        "LEFT JOIN result_booth rb ON rb.election_id = e.election_id "
        "  AND rb.ps_number = x.ps_number AND rb.ac_id = b.ac_id "
        "WHERE b.ac_id = %s AND b.is_active "
        "GROUP BY b.booth_uid ORDER BY MIN(x.ps_number) NULLS LAST, b.booth_uid",
        (ac_id,),
    )
    if not rows:
        return 0
    valid = [max(r["valid"], 1) for r in rows]
    electors = giridih._clamped_electors(rng, valid)
    vs = query_one(
        "SELECT e.election_id FROM election e WHERE e.ac_id = %s AND e.label = 'VS-2024'",
        (ac_id,),
    )
    if vs is None:
        log.warning("VS-2024 election missing; skip roll link")
        election_id = None
    else:
        election_id = vs["election_id"]
    with cursor() as cur:
        label = "modelled-mother-2024"
        cur.execute(
            "INSERT INTO roll_revision (ac_id, revision_date, label, is_mother, source_doc) "
            "VALUES (%s, '2024-01-15', %s, true, %s) "
            "ON CONFLICT (ac_id, label) DO UPDATE SET source_doc = EXCLUDED.source_doc "
            "RETURNING revision_id",
            (ac_id, label, LAYERS["roll"]["filename"]),
        )
        revision_id = cur.fetchone()["revision_id"]
        for booth, elector_count in zip(rows, electors, strict=True):
            cur.execute(
                "INSERT INTO roll_snapshot (revision_id, ac_id, booth_uid, electors) "
                "VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (revision_id, booth_uid) DO UPDATE SET "
                "electors = EXCLUDED.electors, ac_id = EXCLUDED.ac_id",
                (revision_id, ac_id, booth["booth_uid"], elector_count),
            )
            additions = max(1, elector_count // 50)
            cur.execute(
                "INSERT INTO roll_change (ac_id, revision_id, booth_uid, additions, deletions, "
                "modifications, source_doc) VALUES (%s, %s, %s, %s, 0, 0, %s) "
                "ON CONFLICT (revision_id, booth_uid) DO UPDATE SET additions = EXCLUDED.additions",
                (ac_id, revision_id, booth["booth_uid"], additions, LAYERS["roll"]["filename"]),
            )
        if election_id:
            cur.execute(
                "INSERT INTO election_roll_link (election_id, revision_id) VALUES (%s, %s) "
                "ON CONFLICT (election_id) DO UPDATE SET revision_id = EXCLUDED.revision_id",
                (election_id, revision_id),
            )
    return len(rows)


def apply_caste(ac_id: int) -> int:
    """District prior blend rows when surname roll is absent."""
    communities = query("SELECT community_id, name_en FROM community ORDER BY community_id")
    if not communities:
        return 0
    priors = {"GEN": 0.35, "OBC": 0.40, "SC": 0.12, "ST": 0.08, "MUSLIM": 0.05}
    booths = query(
        "SELECT booth_uid, electors FROM roll_snapshot rs "
        "JOIN roll_revision rr ON rr.revision_id = rs.revision_id "
        "WHERE rr.ac_id = %s",
        (ac_id,),
    )
    if not booths:
        booths = query("SELECT booth_uid, 800 AS electors FROM booth WHERE ac_id = %s AND is_active", (ac_id,))
    count = 0
    with cursor() as cur:
        cur.execute("DELETE FROM caste_estimate WHERE ac_id = %s AND source = 'blend'", (ac_id,))
        for booth in booths:
            electors = booth["electors"] or 800
            for comm in communities:
                pct = priors.get(comm["name_en"], 0.02)
                est = max(1, int(electors * pct))
                cur.execute(
                    "INSERT INTO caste_estimate (ac_id, booth_uid, community_id, est_count, "
                    "confidence, source, matched_pct, method_version) "
                    "VALUES (%s, %s, %s, %s, 0.35, 'blend', 38.0, 'modelled-district-prior')",
                    (ac_id, booth["booth_uid"], comm["community_id"], est),
                )
                count += 1
    return count


def _ensure_ls_candidates(ac_id: int, vs_election_id: int, ls_election_id: int) -> None:
    existing = query_one(
        "SELECT 1 FROM candidate WHERE election_id = %s LIMIT 1",
        (ls_election_id,),
    )
    if existing:
        return
    parties = query(
        "SELECT DISTINCT p.party_id, p.abbr FROM result_booth rb "
        "JOIN candidate c ON c.candidate_id = rb.candidate_id "
        "JOIN party p ON p.party_id = c.party_id "
        "WHERE rb.election_id = %s AND rb.ac_id = %s AND p.abbr IS NOT NULL",
        (vs_election_id, ac_id),
    )
    with cursor() as cur:
        for row in parties:
            abbr = row["abbr"] or "IND"
            cur.execute(
                "INSERT INTO candidate (election_id, ac_id, name_en, party_id) "
                "VALUES (%s, %s, %s, %s)",
                (ls_election_id, ac_id, f"({abbr} segment, modelled)", row["party_id"]),
            )


def apply_ls_segment(ac_id: int) -> int:
    """Booth-level LS votes derived from VS-2024 party totals per booth."""
    ls = query_one(
        "SELECT e.election_id FROM election e WHERE e.ac_id = %s AND e.label = 'LS-2024'",
        (ac_id,),
    )
    vs = query_one(
        "SELECT e.election_id FROM election e WHERE e.ac_id = %s AND e.label = 'VS-2024'",
        (ac_id,),
    )
    if ls is None or vs is None:
        log.warning("LS or VS election missing; skip LS segment overlay")
        return 0
    _ensure_ls_candidates(ac_id, vs["election_id"], ls["election_id"])
    rng = random.Random(SEED + 7)
    rows = query(
        "SELECT x.ps_number, x.booth_uid, c.candidate_id, p.abbr, SUM(rb.votes)::INT AS votes "
        "FROM booth_crosswalk x "
        "JOIN result_booth rb ON rb.election_id = x.election_id AND rb.ps_number = x.ps_number "
        "JOIN candidate c ON c.candidate_id = rb.candidate_id "
        "LEFT JOIN party p ON p.party_id = c.party_id "
        "WHERE x.election_id = %s AND x.ac_id = %s "
        "GROUP BY x.ps_number, x.booth_uid, c.candidate_id, p.abbr",
        (vs["election_id"], ac_id),
    )
    if not rows:
        return 0
    ls_candidates = {
        r["abbr"]: r["candidate_id"]
        for r in query(
            "SELECT c.candidate_id, p.abbr FROM candidate c "
            "JOIN party p ON p.party_id = c.party_id "
            "WHERE c.election_id = %s",
            (ls["election_id"],),
        )
    }
    inserted = 0
    with cursor() as cur:
        cur.execute("DELETE FROM result_booth WHERE ac_id = %s AND election_id = %s", (ac_id, ls["election_id"]))
        for row in rows:
            party = row["abbr"] or "IND"
            ls_cid = ls_candidates.get(party)
            if ls_cid is None:
                continue
            jitter = 0.92 + rng.random() * 0.16
            votes = max(0, int(row["votes"] * jitter))
            cur.execute(
                "INSERT INTO result_booth (ac_id, election_id, ps_number, candidate_id, votes) "
                "VALUES (%s, %s, %s, %s, %s) "
                "ON CONFLICT (election_id, ps_number, candidate_id) DO UPDATE SET votes = EXCLUDED.votes",
                (ac_id, ls["election_id"], row["ps_number"], ls_cid, votes),
            )
            inserted += 1
    return inserted


def run(ac_number: int = AC_NUMBER, seed: int = SEED) -> dict:
    ac_id = _ac_id(ac_number)
    stats = {}
    for layer in LAYERS:
        _register_doc(layer, ac_id)
    stats["areas_reassigned"] = assign_booth_areas(ac_id)
    stats["geo"] = apply_geography(ac_number, seed)
    stats["roll_booths"] = apply_roll(ac_id, 0)
    stats["caste_rows"] = apply_caste(ac_id)
    stats["ls_rows"] = apply_ls_segment(ac_id)
    log.info("modelled_overlay: %s", stats)
    return stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Apply modelled data layers after real Form 20")
    ap.add_argument("--ac", type=int, default=AC_NUMBER)
    ap.add_argument("--seed", type=int, default=SEED)
    args = ap.parse_args(argv)
    run(args.ac, args.seed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
