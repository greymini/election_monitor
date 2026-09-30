"""Load db/seed/*.csv into the database. Idempotent - safe to re-run.

    python -m db.seed.load_seed
    python -m db.seed.load_seed --only parties,surnames
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


def load_blocks() -> int:
    rows = _rows("blocks.csv")
    with cursor() as cur:
        for r in rows:
            cur.execute(
                "INSERT INTO block (block_id, name_en, name_hi, kind) VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (block_id) DO UPDATE SET name_en = EXCLUDED.name_en, "
                "name_hi = EXCLUDED.name_hi, kind = EXCLUDED.kind",
                (int(r["block_id"]), r["name_en"], r["name_hi"], r["kind"]),
            )
    return len(rows)


def load_areas() -> int:
    rows = _rows("areas_wards.csv") + _rows("areas_panchayats.csv")
    with cursor() as cur:
        for r in rows:
            cur.execute(
                "INSERT INTO area (block_id, kind, name_en, name_hi, code, census_code) "
                "VALUES (%s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (block_id, kind, name_en) DO UPDATE SET name_hi = EXCLUDED.name_hi, "
                "code = EXCLUDED.code, census_code = COALESCE(EXCLUDED.census_code, area.census_code) "
                "RETURNING area_id",
                (int(r["block_id"]), r["kind"], r["name_en"], r["name_hi"],
                 r.get("code") or None, r.get("census_code") or None),
            )
            area_id = cur.fetchone()["area_id"]
            for text, script in ((r["name_en"], "en"), (r["name_hi"], "hi")):
                key = alias_key(text)
                if not key:
                    continue
                cur.execute(
                    "INSERT INTO area_alias (alias, area_id, script, source) VALUES (%s, %s, %s, 'seed') "
                    "ON CONFLICT (alias) DO NOTHING",
                    (key, area_id, script),
                )
    return len(rows)


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


def load_communities() -> int:
    rows = _rows("communities.csv")
    with cursor() as cur:
        for r in rows:
            cur.execute(
                "INSERT INTO community (name_en, name_hi, category, sort_order) VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (name_en) DO UPDATE SET name_hi = EXCLUDED.name_hi, "
                "category = EXCLUDED.category, sort_order = EXCLUDED.sort_order",
                (r["name_en"], r["name_hi"], r["category"], int(r["sort_order"])),
            )
    return len(rows)


def load_elections() -> int:
    rows = _rows("elections.csv")
    with cursor() as cur:
        for r in rows:
            cur.execute(
                "INSERT INTO election (type, year, poll_date, label, is_baseline, notes) "
                "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (label) DO UPDATE SET "
                "poll_date = COALESCE(EXCLUDED.poll_date, election.poll_date), "
                "is_baseline = EXCLUDED.is_baseline, notes = EXCLUDED.notes",
                (r["type"], int(r["year"]), r.get("poll_date") or None, r["label"],
                 _bool(r.get("is_baseline")), r.get("notes") or None),
            )
    return len(rows)


def load_surnames() -> int:
    rows = _rows("surname_dict.csv")
    comm = {r["name_en"]: r["community_id"] for r in query("SELECT community_id, name_en FROM community")}
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


def load_ac_totals() -> int:
    """AC-level published totals used as the Form 20 validation target (LLD 4.2).

    Candidates named here are created if absent so the totals have something to
    hang off; the Form 20 parser will match its column headers onto them.
    """
    rows = _rows("ac_totals.csv")
    elections = {r["label"]: r["election_id"] for r in query("SELECT election_id, label FROM election")}
    parties = {r["abbr"]: r["party_id"] for r in query("SELECT party_id, abbr FROM party")}
    loaded = 0
    with cursor() as cur:
        for r in rows:
            eid = elections.get(r["election_label"])
            if eid is None:
                log.warning("ac_totals.csv: unknown election %s", r["election_label"])
                continue
            cand_id = None
            name = (r.get("candidate_name") or "").strip()
            if name:
                pid = parties.get((r.get("party_abbr") or "").strip())
                cur.execute(
                    "INSERT INTO candidate (election_id, name_en, party_id) VALUES (%s, %s, %s) "
                    "ON CONFLICT (election_id, name_en, party_id) DO UPDATE SET name_en = EXCLUDED.name_en "
                    "RETURNING candidate_id",
                    (eid, name, pid),
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
                "INSERT INTO result_ac_total (election_id, candidate_id, metric, value, source) "
                "VALUES (%s, %s, %s, %s, %s)",
                (eid, cand_id, r["metric"], int(r["value"]), r.get("source") or None),
            )
            loaded += 1
    return loaded


def load_news_sources() -> int:
    rows = _rows("news_sources.csv")
    with cursor() as cur:
        for r in rows:
            cur.execute(
                "INSERT INTO news_source (name, kind, url, lang, is_active) VALUES (%s, %s, %s, %s, %s) "
                "ON CONFLICT (name) DO UPDATE SET url = EXCLUDED.url, kind = EXCLUDED.kind, "
                "lang = EXCLUDED.lang, is_active = EXCLUDED.is_active",
                (r["name"], r["kind"], r["url"], r["lang"], _bool(r.get("is_active"))),
            )
    return len(rows)


def load_knowledge_cards() -> int:
    """Markdown files in knowledge_cards/ with a small YAML-ish front matter."""
    folder = SEED_DIR / "knowledge_cards"
    if not folder.exists():
        return 0
    loaded = 0
    with cursor() as cur:
        for path in sorted(folder.glob("*.md")):
            meta, body = _split_front_matter(path.read_text(encoding="utf-8"))
            slug = meta.get("slug") or path.stem
            sources = [s.strip() for s in meta.get("sources", "").split("|") if s.strip()]
            cur.execute(
                "INSERT INTO knowledge_card (slug, topic, title_hi, title_en, body_hi, body_en, "
                "sources, last_reviewed, in_prompt) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (slug) DO UPDATE SET topic = EXCLUDED.topic, title_hi = EXCLUDED.title_hi, "
                "title_en = EXCLUDED.title_en, body_hi = EXCLUDED.body_hi, body_en = EXCLUDED.body_en, "
                "sources = EXCLUDED.sources, last_reviewed = EXCLUDED.last_reviewed, "
                "in_prompt = EXCLUDED.in_prompt",
                (slug, meta.get("topic", "general"), meta.get("title_hi"), meta.get("title_en"),
                 body, body, sources, meta.get("last_reviewed") or None,
                 _bool(meta.get("in_prompt", "true"))),
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


LOADERS = {
    "blocks": load_blocks,
    "areas": load_areas,
    "parties": load_parties,
    "communities": load_communities,
    "elections": load_elections,
    "surnames": load_surnames,
    "ac_totals": load_ac_totals,
    "news_sources": load_news_sources,
    "cards": load_knowledge_cards,
}

# Order matters: communities before surnames, elections before ac_totals.
ORDER = ["blocks", "areas", "parties", "communities", "elections",
         "surnames", "ac_totals", "news_sources", "cards"]


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
        log.info("seed %-14s %4d row(s)", name, n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
