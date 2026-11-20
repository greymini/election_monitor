"""Ground reports (LLD §3.4, §5.3): with pulse/reports/lifecycle.py (status changes after filing),
the only code that writes field.report, report_media and report_event. Every step writes a report_event row. A report is never refused: limits refuse a
single item, PII in notes is masked, and an untouched draft files itself.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from pulse.common import pii
from pulse.common.clock import utcnow
from pulse.config import ReportLimits
from pulse.core_adapter import CoreAdapter
from pulse.reports import classify

NOTE_MAX = 10_000                 # beyond this, further text is kept in the event log only


class ReportError(Exception):
    pass


class NotFound(ReportError):
    pass


class PiiRejected(ReportError):
    """A typed field (title, note) holds a phone, Aadhaar or voter-id number: refused, not masked,
    because the person can simply type it again without the number. Report notes and transcripts
    are masked instead (refusing them would lose the report)."""

    def __init__(self, field: str, kinds: list[str]):
        super().__init__(f"{field} contains an identifier ({', '.join(kinds)}); remove it and try again")
        self.field, self.kinds = field, kinds


def screen_typed(field: str, text: str | None) -> None:
    from pulse.common import pii

    kinds = pii.screen(text)
    if kinds:
        raise PiiRejected(field, kinds)


class LimitReached(ReportError):
    """An item would exceed a per-report limit. kind: photos | voice."""

    def __init__(self, kind: str):
        super().__init__(kind)
        self.kind = kind


def event(conn: psycopg.Connection, report_id: UUID, kind: str, *, actor: UUID | None, now: datetime,
          note: str | None = None, data: dict | None = None) -> None:
    conn.execute(
        "INSERT INTO field.report_event (report_id, at, actor_member_id, kind, note, data) VALUES (%s, %s, %s, %s, %s, %s)",
        (report_id, now, actor, kind, note, Jsonb(data) if data is not None else None),
    )


def get(conn: psycopg.Connection, report_id: UUID, *, lock: bool = False) -> dict[str, Any] | None:
    return conn.execute("SELECT * FROM field.report WHERE report_id = %s" + (" FOR UPDATE" if lock else ""),
                        (report_id,)).fetchone()


def _touch(conn: psycopg.Connection, report_id: UUID, now: datetime) -> None:
    conn.execute("UPDATE field.report SET last_activity_at = %s WHERE report_id = %s", (now, report_id))


# ---- location ---------------------------------------------------------------------------------

def resolve_area(conn: psycopg.Connection, core: CoreAdapter, member: dict[str, Any]) -> tuple[str | None, str | None, str] | None:
    """(block_id, area_id, source) from the member's only scope ('area_default') or the area they used
    last on a report or an expense ('last_used'); None means ask."""
    scopes = conn.execute("SELECT block_id, area_id FROM field.member_scope WHERE member_id = %s",
                          (member["member_id"],)).fetchall()
    if len(scopes) == 1:
        s = scopes[0]
        block = s["block_id"]
        if s["area_id"] and block is None:
            area = next((a for a in core.list_areas(member["ac_id"]) if a.area_id == s["area_id"]), None)
            block = area.block_id if area else None
        return block, s["area_id"], "area_default"
    last = conn.execute(
        """SELECT block_id, area_id FROM (
               SELECT block_id, area_id, created_at FROM field.report
               WHERE reporter_member_id = %(m)s AND (block_id IS NOT NULL OR area_id IS NOT NULL)
               UNION ALL
               SELECT block_id, area_id, created_at FROM field.money_txn
               WHERE from_member_id = %(m)s AND kind = 'expense' AND status <> 'void'
                 AND (block_id IS NOT NULL OR area_id IS NOT NULL)) x
           ORDER BY created_at DESC LIMIT 1""",
        {"m": member["member_id"]},
    ).fetchone()
    if last:
        return last["block_id"], last["area_id"], "last_used"
    return None


def set_area(conn: psycopg.Connection, core: CoreAdapter, report_id: UUID, block_id: str | None, area_id: str | None,
             source: str, *, actor: UUID | None, now: datetime) -> dict[str, Any]:
    report = get(conn, report_id, lock=True)
    if area_id is not None:
        area = next((a for a in core.list_areas(report["ac_id"]) if a.area_id == area_id), None)
        if area is None or (block_id is not None and area.block_id != block_id):
            raise ReportError("unknown area")
    # a shared GPS location stays the location source; the area is recorded alongside it
    loc_source = report["loc_source"] if report["loc_source"] == "shared" else source
    row = conn.execute(
        """UPDATE field.report SET block_id = %s, area_id = %s, loc_source = %s, last_activity_at = %s
           WHERE report_id = %s RETURNING *""",
        (block_id, area_id, loc_source, now, report_id),
    ).fetchone()
    event(conn, report_id, "area_set", actor=actor, now=now, data={"block_id": block_id, "area_id": area_id,
                                                                    "source": source})
    return row


def set_location(conn: psycopg.Connection, report_id: UUID, lat: float, lon: float, *, actor: UUID | None,
                 now: datetime) -> dict[str, Any]:
    """A location the member shared: it wins over any area default (LLD §5.3)."""
    row = conn.execute(
        """UPDATE field.report SET lat = round(%s::numeric, 6), lon = round(%s::numeric, 6), loc_source = 'shared',
               last_activity_at = %s WHERE report_id = %s RETURNING *""",
        (str(lat), str(lon), now, report_id),
    ).fetchone()
    event(conn, report_id, "location_added", actor=actor, now=now)
    return row


# ---- drafts and items -------------------------------------------------------------------------

def open_draft(conn: psycopg.Connection, core: CoreAdapter, member: dict[str, Any], *, now: datetime | None = None,
               created_via: str = "telegram") -> dict[str, Any]:
    now = now or utcnow()
    row = conn.execute(
        """INSERT INTO field.report (ac_id, reporter_member_id, created_via, created_at, last_activity_at)
           VALUES (%s, %s, %s, %s, %s) RETURNING *""",
        (member["ac_id"], member["member_id"], created_via, now, now),
    ).fetchone()
    event(conn, row["report_id"], "draft_opened", actor=member["member_id"], now=now)
    area = resolve_area(conn, core, member)
    if area is not None:
        row = set_area(conn, core, row["report_id"], area[0], area[1], area[2], actor=None, now=now)
    return row


def counts(conn: psycopg.Connection, report_id: UUID) -> dict[str, int]:
    row = conn.execute(
        """SELECT count(*) FILTER (WHERE a.kind = 'photo') AS photo,
                  count(*) FILTER (WHERE a.kind = 'voice') AS voice,
                  count(*) FILTER (WHERE a.kind = 'video') AS video,
                  count(*) FILTER (WHERE a.kind = 'document') AS document,
                  COALESCE(SUM(a.duration_s) FILTER (WHERE a.kind = 'voice'), 0) AS voice_seconds
           FROM field.report_media m JOIN field.media_asset a ON a.asset_id = m.asset_id
           WHERE m.report_id = %s""",
        (report_id,),
    ).fetchone()
    return dict(row)


def check_room(conn: psycopg.Connection, report_id: UUID, kind: str, duration_s: int | None,
               limits: ReportLimits) -> None:
    """Raise LimitReached before an item is accepted; the item is then refused, never the report."""
    c = counts(conn, report_id)
    if kind == "photo" and c["photo"] >= limits.max_photos:
        raise LimitReached("photos")
    if kind == "voice" and c["voice_seconds"] + (duration_s or 0) > limits.max_voice_seconds:
        raise LimitReached("voice")


def add_media(conn: psycopg.Connection, report_id: UUID, asset_id: UUID, *, actor: UUID, now: datetime,
              after_filing: bool = False, role: str = "report") -> int:
    seq = conn.execute("SELECT COALESCE(max(seq), 0) + 1 AS n FROM field.report_media WHERE report_id = %s",
                       (report_id,)).fetchone()["n"]
    conn.execute("INSERT INTO field.report_media (report_id, asset_id, seq, role) VALUES (%s, %s, %s, %s)",
                 (report_id, asset_id, seq, role))
    _touch(conn, report_id, now)
    kind = "closing_photo" if role == "closing" else ("more_added" if after_filing else "item_added")
    event(conn, report_id, kind, actor=actor, now=now, data={"asset_id": str(asset_id), "seq": seq})
    return seq


def mask_note(text: str | None) -> str | None:
    text = (text or "").strip()
    return pii.mask(text) if text else None


def add_note(conn: psycopg.Connection, report_id: UUID, text: str, *, actor: UUID, now: datetime) -> bool:
    """Append typed text or a caption, PII masked. Returns True if something was masked."""
    text = (text or "").strip()
    if not text:
        return False
    masked = pii.mask(text)
    report = get(conn, report_id, lock=True)
    current = report["note_text"] or ""
    joined = (current + "\n" + masked) if current else masked
    if len(joined) <= NOTE_MAX:
        conn.execute("UPDATE field.report SET note_text = %s, last_activity_at = %s WHERE report_id = %s",
                     (joined, now, report_id))
    else:
        _touch(conn, report_id, now)
    event(conn, report_id, "note_added", actor=actor, now=now, note=masked if len(joined) > NOTE_MAX else None,
          data={"masked": masked != text, "chars": len(masked)})
    from pulse.reports import title

    title.refresh(conn, report_id)
    return masked != text


def set_occurred_at(conn: psycopg.Connection, report_id: UUID, occurred_at: datetime, *, actor: UUID,
                    now: datetime) -> None:
    conn.execute("UPDATE field.report SET occurred_at = %s WHERE report_id = %s", (occurred_at, report_id))
    event(conn, report_id, "occurred_at_set", actor=actor, now=now, data={"occurred_at": occurred_at.isoformat()})


def mark_escalated(conn: psycopg.Connection, report_id: UUID, level: int, *, final: bool, now: datetime,
                   data: dict | None = None) -> None:
    """Record an escalation round; the last round also marks the report escalated (for the digest)."""
    conn.execute("UPDATE field.report SET escalation_level = %s, escalated_at = %s WHERE report_id = %s",
                 (level, now if final else None, report_id))
    event(conn, report_id, "escalated", actor=None, now=now, data={"level": level, **(data or {})})


def refuse_item(conn: psycopg.Connection, report_id: UUID, reason: str, *, actor: UUID, now: datetime) -> None:
    event(conn, report_id, "item_refused", actor=actor, now=now, data={"reason": reason})


# ---- category, urgency, filing ----------------------------------------------------------------

def categories(conn: psycopg.Connection) -> list[dict[str, Any]]:
    return conn.execute("SELECT * FROM field.report_category WHERE active ORDER BY sort, category_id").fetchall()


def set_category(conn: psycopg.Connection, report_id: UUID, category_id: int, *, actor: UUID, now: datetime,
                 suggested_id: int | None = None, except_alert: int | None = None) -> dict[str, Any]:
    cat = conn.execute("SELECT * FROM field.report_category WHERE category_id = %s AND active", (category_id,)).fetchone()
    if cat is None:
        raise ReportError("unknown category")
    row = conn.execute(
        """UPDATE field.report SET category_id = %s, restricted = %s, last_activity_at = %s
           WHERE report_id = %s RETURNING *""",
        (category_id, cat["restricted"], now, report_id),
    ).fetchone()
    event(conn, report_id, "category_set", actor=actor, now=now,
          data={"category_id": category_id, "suggested_id": suggested_id,
                "took_suggestion": suggested_id is not None and suggested_id == category_id})
    if row["status"] != "draft":                  # a filed report: look for duplicates, redraw its cards
        from pulse.notify import outbox
        from pulse.reports import duplicates

        duplicates.suggest(conn, report_id)
        outbox.refresh_report_cards(conn, report_id, except_alert=except_alert)
    return row


def set_urgency(conn: psycopg.Connection, report_id: UUID, urgency: str, *, source: str, actor: UUID | None,
                now: datetime) -> dict[str, Any]:
    if urgency not in ("normal", "urgent"):
        raise ReportError("urgency must be normal or urgent")
    row = conn.execute("UPDATE field.report SET urgency = %s, last_activity_at = %s WHERE report_id = %s RETURNING *",
                       (urgency, now, report_id)).fetchone()
    event(conn, report_id, "urgency_set", actor=actor, now=now, data={"urgency": urgency, "source": source})
    return row


def _next_report_no(conn: psycopg.Connection, core: CoreAdapter, ac_id: str) -> str:
    ac = next((a for a in core.list_acs() if a.ac_id == ac_id), None)
    if ac is None:
        raise ReportError("unknown AC")
    n = conn.execute(
        """INSERT INTO field.report_counter (ac_id, last_no) VALUES (%s, 1)
           ON CONFLICT (ac_id) DO UPDATE SET last_no = field.report_counter.last_no + 1 RETURNING last_no""",
        (ac_id,),
    ).fetchone()["last_no"]
    return f"{ac.ac_number}-R-{n:06d}"


def file(conn: psycopg.Connection, core: CoreAdapter, report_id: UUID, *, how: str, actor: UUID | None,
         now: datetime) -> dict[str, Any]:
    """draft → new, with its reference number. how: user | urgent | auto | superseded | web. Idempotent.
    Filing looks for possible duplicates and queues the alerts (pulse/notify/alerts.py)."""
    report = get(conn, report_id, lock=True)
    if report["status"] != "draft":
        return report
    row = conn.execute(
        """UPDATE field.report SET status = 'new', report_no = %s, filed_at = %s, last_activity_at = %s
           WHERE report_id = %s RETURNING *""",
        (_next_report_no(conn, core, report["ac_id"]), now, now, report_id),
    ).fetchone()
    event(conn, report_id, "filed", actor=actor, now=now, data={"how": how, "report_no": row["report_no"]})
    from pulse.reports import title

    title.refresh(conn, report_id)
    row = get(conn, report_id)
    from pulse.notify import alerts
    from pulse.reports import duplicates

    duplicates.suggest(conn, report_id)
    alerts.on_filed(conn, row, how)
    return row


def autofile(conn: psycopg.Connection, core: CoreAdapter, report_id: UUID, *, now: datetime,
             how: str = "auto") -> dict[str, Any]:
    """File a draft nobody finished: category stays NULL ("to be sorted"); urgency from keyword hints in
    the notes (machine output, recorded as source 'rule'), unless the reporter already chose."""
    report = get(conn, report_id, lock=True)
    if report["status"] != "draft":
        return report
    chose = conn.execute("SELECT 1 FROM field.report_event WHERE report_id = %s AND kind = 'urgency_set'",
                         (report_id,)).fetchone()
    text = "\n".join(t for t in (report["note_text"], report["transcript_hi"]) if t)
    if not chose and classify.urgency_hint(conn, text):
        set_urgency(conn, report_id, "urgent", source="rule", actor=None, now=now)
    return file(conn, core, report_id, how=how, actor=None, now=now)


def drafts_due(conn: psycopg.Connection, now: datetime, limits: ReportLimits) -> list[dict[str, Any]]:
    """Drafts untouched for autofile_minutes. Locked with SKIP LOCKED so two workers share the work."""
    return conn.execute(
        """SELECT * FROM field.report WHERE status = 'draft' AND last_activity_at <= %s
           ORDER BY last_activity_at FOR UPDATE SKIP LOCKED""",
        (now - timedelta(minutes=limits.autofile_minutes),),
    ).fetchall()


def recent_for_member(conn: psycopg.Connection, member_id: UUID, n: int = 5) -> list[dict[str, Any]]:
    return conn.execute(
        """SELECT r.report_id, r.report_no, r.status, r.created_at, r.urgency, c.name_hi AS category_name_hi
           FROM field.report r LEFT JOIN field.report_category c ON c.category_id = r.category_id
           WHERE r.reporter_member_id = %s ORDER BY r.created_at DESC LIMIT %s""",
        (member_id, n),
    ).fetchall()


@dataclass(frozen=True)
class Summary:
    counts: dict[str, int]
    report: dict[str, Any]
    category: dict[str, Any] | None


def summary(conn: psycopg.Connection, report_id: UUID) -> Summary:
    report = get(conn, report_id)
    cat = (conn.execute("SELECT * FROM field.report_category WHERE category_id = %s", (report["category_id"],)).fetchone()
           if report["category_id"] else None)
    return Summary(counts=counts(conn, report_id), report=report, category=cat)
