"""Report titles: the first sentence of the note or transcript, editable by a person.

Source order: the typed note (a person wrote it), then the transcript (a human correction before the
machine text). A derived title is marked title_source 'auto' and is recomputed whenever that text
changes; a title a person set ('person') is never overwritten. Clearing it returns to 'auto'.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from uuid import UUID

import psycopg

TITLE_MAX = 80
# sentence ends: . ! ? newline and the Devanagari danda (U+0964) and double danda (U+0965)
_SENTENCE_END = re.compile("[.!?" + chr(10) + chr(0x0964) + chr(0x0965) + "]")


def first_sentence(text: str | None, limit: int = TITLE_MAX) -> str | None:
    text = " ".join((text or "").split(" "))
    for part in _SENTENCE_END.split(text):
        part = " ".join(part.split())
        if len(part) >= 3:
            return part if len(part) <= limit else part[: limit - 1].rstrip() + "…"
    return None


def derive(report: dict[str, Any]) -> str | None:
    for text in (report.get("note_text"), report.get("transcript_human"), report.get("transcript_hi")):
        title = first_sentence(text)
        if title:
            return title
    return None


def refresh(conn: psycopg.Connection, report_id: UUID) -> None:
    """Recompute an automatic title from the current text; a person's title stays."""
    r = conn.execute("SELECT * FROM field.report WHERE report_id = %s", (report_id,)).fetchone()
    if r is None or r["title_source"] == "person":
        return
    title = derive(r)
    conn.execute("UPDATE field.report SET title = %s, title_source = %s WHERE report_id = %s",
                 (title, "auto" if title else None, report_id))


def set_by_person(conn: psycopg.Connection, report_id: UUID, title: str | None, *, actor: UUID,
                  now: datetime) -> dict[str, Any]:
    from pulse.reports import service as reports

    clean = " ".join((title or "").split())[:TITLE_MAX] or None
    reports.screen_typed("title", clean)
    if clean is None:
        conn.execute("UPDATE field.report SET title = NULL, title_source = NULL WHERE report_id = %s", (report_id,))
        refresh(conn, report_id)
    else:
        conn.execute("UPDATE field.report SET title = %s, title_source = 'person' WHERE report_id = %s",
                     (clean, report_id))
    reports.event(conn, report_id, "title_set", actor=actor, now=now, data={"cleared": clean is None})
    return reports.get(conn, report_id)
