"""Idempotent reference-data seeding: expense buckets (LLD §4.1), report categories (LLD §4.2) and
starter keywords for both.

Upserts by bucket `code` and by (keyword, bucket). Never deletes: ledger rows reference buckets,
and keywords added later by an admin must survive a re-seed. `statutory_head` and `active` are
not overwritten, so the election agent's mapping and an admin's deactivation are kept.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import psycopg

from pulse.common.text import normalise, script_of
from pulse.config import REPO_ROOT

SEEDS_DIR = REPO_ROOT / "db" / "seeds"


def _upsert_bucket(conn: psycopg.Connection, *, code: str, name_en: str, name_hi: str, parent_id: int | None,
                   sensitive: bool, ask_reach: bool, sort: int) -> int:
    row = conn.execute(
        """INSERT INTO field.exp_bucket (code, name_en, name_hi, parent_id, sensitive, ask_reach, sort)
           VALUES (%s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (code) DO UPDATE SET name_en = EXCLUDED.name_en, name_hi = EXCLUDED.name_hi,
               parent_id = EXCLUDED.parent_id, sensitive = EXCLUDED.sensitive,
               ask_reach = EXCLUDED.ask_reach, sort = EXCLUDED.sort
           RETURNING bucket_id""",
        (code, name_en, name_hi, parent_id, sensitive, ask_reach, sort),
    ).fetchone()
    return row["bucket_id"]


def seed_buckets(conn: psycopg.Connection, path: Path = SEEDS_DIR / "exp_buckets.json") -> dict[str, int]:
    data = json.loads(path.read_text(encoding="utf-8"))
    ids: dict[str, int] = {}
    for i, b in enumerate(data["buckets"], start=1):
        top = _upsert_bucket(conn, code=b["code"], name_en=b["name_en"], name_hi=b["name_hi"], parent_id=None,
                             sensitive=b["sensitive"], ask_reach=b["ask_reach"], sort=i * 10)
        ids[b["code"]] = top
        for j, (sub_code, name_en, name_hi) in enumerate(b["subheads"], start=1):
            code = f"{b['code']}.{sub_code}"
            # sub-heads carry the parent's flags too, but flags are always read from the top level
            ids[code] = _upsert_bucket(conn, code=code, name_en=name_en, name_hi=name_hi, parent_id=top,
                                       sensitive=b["sensitive"], ask_reach=b["ask_reach"], sort=j)
    return ids


def seed_keywords(conn: psycopg.Connection, bucket_ids: dict[str, int],
                  path: Path = SEEDS_DIR / "exp_keywords.json") -> int:
    data = json.loads(path.read_text(encoding="utf-8"))
    n = 0
    for code, entries in data["keywords"].items():
        for entry in entries:
            weight = Decimal(str(entry[2])) if len(entry) > 2 else Decimal("1")
            for spelling in entry[:2]:
                kw = normalise(spelling)
                if not kw:
                    continue
                conn.execute(
                    """INSERT INTO field.exp_keyword (keyword, script, bucket_id, weight) VALUES (%s, %s, %s, %s)
                       ON CONFLICT (keyword, bucket_id) DO UPDATE SET weight = EXCLUDED.weight, script = EXCLUDED.script""",
                    (kw, script_of(spelling), bucket_ids[code], weight),
                )
                n += 1
    return n


def seed_report_categories(conn: psycopg.Connection, path: Path = SEEDS_DIR / "report_categories.json") -> dict[str, int]:
    data = json.loads(path.read_text(encoding="utf-8"))
    ids: dict[str, int] = {}
    for i, c in enumerate(data["categories"], start=1):
        row = conn.execute(
            """INSERT INTO field.report_category (code, name_hi, name_en, default_urgency, restricted, sort)
               VALUES (%s, %s, %s, %s, %s, %s)
               ON CONFLICT (code) DO UPDATE SET name_hi = EXCLUDED.name_hi, name_en = EXCLUDED.name_en,
                   default_urgency = EXCLUDED.default_urgency, restricted = EXCLUDED.restricted, sort = EXCLUDED.sort
               RETURNING category_id""",
            (c["code"], c["name_hi"], c["name_en"], c["default_urgency"], c["restricted"], i * 10),
        ).fetchone()
        ids[c["code"]] = row["category_id"]
    return ids


def seed_report_keywords(conn: psycopg.Connection, category_ids: dict[str, int],
                         path: Path = SEEDS_DIR / "report_keywords.json") -> int:
    data = json.loads(path.read_text(encoding="utf-8"))
    n = 0
    for code, entries in data["keywords"].items():
        for entry in entries:
            weight = Decimal(str(entry[2])) if len(entry) > 2 else Decimal("1")
            hint = entry[3] if len(entry) > 3 else None
            for spelling in entry[:2]:
                kw = normalise(spelling)
                if not kw:
                    continue
                conn.execute(
                    """INSERT INTO field.report_keyword (keyword, script, category_id, urgency_hint, weight)
                       VALUES (%s, %s, %s, %s, %s)
                       ON CONFLICT (keyword, category_id) DO UPDATE
                       SET weight = EXCLUDED.weight, script = EXCLUDED.script, urgency_hint = EXCLUDED.urgency_hint""",
                    (kw, script_of(spelling), category_ids[code], hint, weight),
                )
                n += 1
    return n


def seed_alert_rules(conn: psycopg.Connection, path: Path = SEEDS_DIR / "alert_rules.json") -> int:
    """Upsert by code. `active` is not overwritten, so a rule switched off by an admin stays off."""
    from psycopg.types.json import Jsonb

    data = json.loads(path.read_text(encoding="utf-8"))
    for r in data["rules"]:
        conn.execute(
            """INSERT INTO field.alert_rule (code, trigger, condition, priority, recipients, fallback, after_minutes,
                   at_local_time, include_media, description)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (code) DO UPDATE SET trigger = EXCLUDED.trigger, condition = EXCLUDED.condition,
                   priority = EXCLUDED.priority, recipients = EXCLUDED.recipients, fallback = EXCLUDED.fallback,
                   after_minutes = EXCLUDED.after_minutes, at_local_time = EXCLUDED.at_local_time,
                   include_media = EXCLUDED.include_media, description = EXCLUDED.description""",
            (r["code"], r["trigger"], Jsonb(r["condition"]), r["priority"], r["recipients"], r.get("fallback", []),
             r.get("after_minutes"), r.get("at_local_time"), r["include_media"], r.get("description")),
        )
    return len(data["rules"])


def apply_seeds(conn: psycopg.Connection) -> None:
    with conn.transaction():
        seed_keywords(conn, seed_buckets(conn))
        seed_report_keywords(conn, seed_report_categories(conn))
        seed_alert_rules(conn)
