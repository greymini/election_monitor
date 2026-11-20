"""Custom labels and keywords (LLD §4.1, §8 label_review).

A person who picks the OTHER bucket ("other" in the bot) types a short label; field.exp_custom_label counts how often each
(normalised) label is used per AC. Labels used three or more times are listed for promotion. Promoting
one creates an AC-specific sub-head under the chosen top-level bucket and adds the label as a keyword
of that bucket, so the classifier suggests the bucket next time. Entries already saved under OTHER
stay where they are (the ledger is append-only); they can be re-classified one by one.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

import psycopg

from pulse import settings_ac
from pulse.common.clock import utcnow
from pulse.common.text import normalise, script_of
from pulse.ledger.errors import Invalid, NotFound

REVIEW_MIN_COUNT = 3
NAME_MAX = 60


def pending(conn: psycopg.Connection, ac_id: str, min_count: int = REVIEW_MIN_COUNT) -> list[dict[str, Any]]:
    return conn.execute(
        """SELECT ac_id, label_norm, count, last_seen, review_listed_at FROM field.exp_custom_label
           WHERE ac_id = %s AND promoted_bucket_id IS NULL AND count >= %s ORDER BY count DESC, label_norm""",
        (ac_id, min_count)).fetchall()


def review(conn: psycopg.Connection, now: datetime | None = None) -> dict[str, int]:
    """The weekly label_review job: mark every label due for promotion as listed (the Buckets screen
    shows them); returns how many labels each AC has waiting."""
    now = now or utcnow()
    rows = conn.execute(
        """UPDATE field.exp_custom_label SET review_listed_at = %s
           WHERE promoted_bucket_id IS NULL AND count >= %s RETURNING ac_id""",
        (now, REVIEW_MIN_COUNT)).fetchall()
    out: dict[str, int] = {}
    for r in rows:
        out[r["ac_id"]] = out.get(r["ac_id"], 0) + 1
    return out


def _clean_name(value: str, field: str) -> str:
    value = (value or "").strip()
    if not value or len(value) > NAME_MAX:
        raise Invalid(f"{field} must be 1-{NAME_MAX} characters")
    return value


def promote(conn: psycopg.Connection, ac_id: str, label_norm: str, *, parent_bucket_id: int, name_hi: str,
            name_en: str, actor_user_id: str, actor_member_id: UUID) -> dict[str, Any]:
    label = conn.execute("SELECT * FROM field.exp_custom_label WHERE ac_id = %s AND label_norm = %s FOR UPDATE",
                         (ac_id, label_norm)).fetchone()
    if label is None:
        raise NotFound("label not found")
    if label["promoted_bucket_id"] is not None:
        raise Invalid("label already promoted")
    parent = conn.execute("SELECT * FROM field.exp_bucket WHERE bucket_id = %s AND active", (parent_bucket_id,)).fetchone()
    if parent is None or parent["parent_id"] is not None or (parent["ac_id"] not in (None, ac_id)):
        raise Invalid("choose an active top-level bucket")
    if parent["code"] == "OTHER":
        raise Invalid("a label cannot become a sub-head of OTHER")
    name_hi, name_en = _clean_name(name_hi, "name_hi"), _clean_name(name_en, "name_en")
    n = conn.execute("SELECT count(*) AS n FROM field.exp_bucket WHERE ac_id = %s", (ac_id,)).fetchone()["n"] + 1
    code = f"{parent['code']}.{ac_id.upper().replace('-', '')}_{n}"
    sort = conn.execute("SELECT COALESCE(max(sort), 0) + 1 AS s FROM field.exp_bucket WHERE parent_id = %s",
                        (parent_bucket_id,)).fetchone()["s"]
    bucket = conn.execute(
        """INSERT INTO field.exp_bucket (ac_id, parent_id, code, name_hi, name_en, sensitive, ask_reach, sort)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING *""",
        (ac_id, parent_bucket_id, code, name_hi, name_en, parent["sensitive"], parent["ask_reach"], sort)).fetchone()
    # the classifier scores top-level buckets, so the keyword points at the parent
    conn.execute("""INSERT INTO field.exp_keyword (keyword, script, bucket_id) VALUES (%s, %s, %s)
                    ON CONFLICT DO NOTHING""", (label_norm, script_of(label_norm), parent_bucket_id))
    conn.execute("UPDATE field.exp_custom_label SET promoted_bucket_id = %s WHERE ac_id = %s AND label_norm = %s",
                 (bucket["bucket_id"], ac_id, label_norm))
    settings_ac.audit(conn, ac_id, "label_promoted", actor_user_id=actor_user_id, actor_member_id=actor_member_id,
                      data={"label": label_norm, "bucket_id": bucket["bucket_id"], "code": code,
                            "parent_code": parent["code"]})
    return bucket


def keywords(conn: psycopg.Connection, ac_id: str) -> list[dict[str, Any]]:
    """Keywords of the buckets this AC sees (shared presets and its own)."""
    return conn.execute(
        """SELECT k.keyword, k.script, k.weight, b.bucket_id, b.code, b.name_hi, b.name_en
           FROM field.exp_keyword k JOIN field.exp_bucket b ON b.bucket_id = k.bucket_id
           WHERE b.active AND (b.ac_id IS NULL OR b.ac_id = %s) ORDER BY b.sort, b.code, k.keyword""",
        (ac_id,)).fetchall()


def add_keyword(conn: psycopg.Connection, ac_id: str, bucket_id: int, keyword: str, *, actor_user_id: str,
                actor_member_id: UUID) -> dict[str, Any]:
    b = conn.execute("SELECT * FROM field.exp_bucket WHERE bucket_id = %s AND active", (bucket_id,)).fetchone()
    if b is None or b["parent_id"] is not None or b["ac_id"] not in (None, ac_id):
        raise Invalid("keywords belong to an active top-level bucket")
    norm = normalise(keyword)
    if not norm or len(norm) > NAME_MAX:
        raise Invalid(f"keyword must be 1-{NAME_MAX} characters")
    row = conn.execute("""INSERT INTO field.exp_keyword (keyword, script, bucket_id) VALUES (%s, %s, %s)
                          ON CONFLICT DO NOTHING RETURNING *""", (norm, script_of(norm), bucket_id)).fetchone()
    if row is None:
        raise Invalid("that keyword is already on this bucket")
    settings_ac.audit(conn, ac_id, "keyword_added", actor_user_id=actor_user_id, actor_member_id=actor_member_id,
                      data={"keyword": norm, "bucket_id": bucket_id})
    return row


def remove_keyword(conn: psycopg.Connection, ac_id: str, bucket_id: int, keyword: str, *, actor_user_id: str,
                   actor_member_id: UUID) -> None:
    norm = normalise(keyword)
    gone = conn.execute("DELETE FROM field.exp_keyword WHERE bucket_id = %s AND keyword = %s RETURNING keyword",
                        (bucket_id, norm)).fetchone()
    if gone is None:
        raise NotFound("keyword not found")
    settings_ac.audit(conn, ac_id, "keyword_removed", actor_user_id=actor_user_id, actor_member_id=actor_member_id,
                      data={"keyword": norm, "bucket_id": bucket_id})
