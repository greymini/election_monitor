"""Times are stored in UTC; calendar dates and display use Asia/Kolkata.

The demo (pulse/demo, dev-only) can move this clock forward to show timed behaviour (auto-file,
escalation, reminders, follow-ups) without waiting. The offset stays zero unless `allow_offset()`
was called, and only the demo calls it, after checking the database is its own embedded one.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

_offset = timedelta(0)
_offset_allowed = False


def utcnow() -> datetime:
    return datetime.now(timezone.utc) + _offset


def today_ist(now: datetime | None = None) -> date:
    return (now or utcnow()).astimezone(IST).date()


def allow_offset() -> None:
    """Demo only: permit `advance`."""
    global _offset_allowed
    _offset_allowed = True


def advance(delta: timedelta) -> timedelta:
    """Move the clock forward by delta (demo only). Returns the total offset."""
    global _offset
    if not _offset_allowed:
        raise RuntimeError("the clock can only be moved in demo mode")
    if delta < timedelta(0):
        raise ValueError("the clock only moves forward")
    _offset += delta
    return _offset


def offset() -> timedelta:
    return _offset


def reset_offset() -> None:
    """Back to real time and offsets refused again (tests, demo shutdown)."""
    global _offset, _offset_allowed
    _offset, _offset_allowed = timedelta(0), False
