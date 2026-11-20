"""Per-AC settings: field.ac_setting overrides the environment defaults (LLD §4.5 "configurable per AC").

`effective(conn, ac_id, defaults)` is the one way to read them. The bot, the API and the worker all
call it for the AC they are acting in, so an override applies everywhere at once.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

import psycopg
from psycopg.types.json import Jsonb

from pulse.config import LedgerLimits, ReportLimits, Retention

# key → (which group, attribute, parser, check, description)
_Money = Decimal


def _positive_money(v: Decimal) -> bool:
    return v > 0


def _positive_int(v: int) -> bool:
    return v > 0


def _under_48(v: int) -> bool:
    return 0 < v < 48


KEYS: dict[str, tuple[str, str, Callable[[str], Any], Callable[[Any], bool], str]] = {
    "large_entry": ("ledger", "large_entry", Decimal, _positive_money, "flag entries at or above (₹)"),
    "tail_threshold": ("ledger", "tail_threshold", Decimal, _positive_money, "tail analysis: entries at or below (₹)"),
    "pin_limit": ("ledger", "pin_limit", Decimal, _positive_money, "bot asks for the PIN at or above (₹)"),
    "autofile_minutes": ("reports", "autofile_minutes", int, _positive_int, "report draft files itself after"),
    "ack_remind_hours": ("ledger", "ack_remind_hours", int, _positive_int, "remind an unacknowledged advance after"),
    "ack_flag_hours": ("ledger", "ack_flag_hours", int, _positive_int, "flag an unacknowledged advance after"),
    "media_retention_days": ("retention", "media_retention_days", int, _positive_int, "delete media files after"),
    "void_retention_days": ("retention", "void_retention_days", int, _positive_int,
                            "voided entries: stored, not acted on (decision open)"),
    "chat_cleanup_hours": ("retention", "chat_cleanup_hours", int, _under_48,
                           "delete the bot's money cards after (under 48: Bot API limit)"),
    "followup_days": ("ledger", "followup_days", int, _positive_int,
                      "ask whether ASSIST / WORKS work was done after (days)"),
}


class SettingError(ValueError):
    pass


@dataclass(frozen=True)
class Defaults:
    ledger: LedgerLimits = field(default_factory=LedgerLimits)
    reports: ReportLimits = field(default_factory=ReportLimits)
    retention: Retention = field(default_factory=Retention)


@dataclass(frozen=True)
class Effective:
    ac_id: str
    ledger: LedgerLimits
    reports: ReportLimits
    retention: Retention
    sources: dict[str, str]          # key → 'default' | 'override'

    def value(self, key: str) -> Any:
        group, attr = KEYS[key][0], KEYS[key][1]
        return getattr(getattr(self, group), attr)


def parse(key: str, raw: Any) -> Any:
    if key not in KEYS:
        raise SettingError(f"unknown setting {key!r}")
    _, _, conv, ok, _ = KEYS[key]
    try:
        value = conv(str(raw).strip())
    except (ValueError, InvalidOperation):
        raise SettingError(f"{key}: not a valid number") from None
    if not ok(value):
        raise SettingError(f"{key}: out of range")
    return value


def effective(conn: psycopg.Connection, ac_id: str, defaults: Defaults | None = None) -> Effective:
    defaults = defaults or Defaults()
    groups = {"ledger": defaults.ledger, "reports": defaults.reports, "retention": defaults.retention}
    overrides: dict[str, dict[str, Any]] = {"ledger": {}, "reports": {}, "retention": {}}
    sources = {k: "default" for k in KEYS}
    for row in conn.execute("SELECT key, value FROM field.ac_setting WHERE ac_id = %s", (ac_id,)):
        group, attr = KEYS[row["key"]][0], KEYS[row["key"]][1]
        overrides[group][attr] = parse(row["key"], row["value"])
        sources[row["key"]] = "override"
    return Effective(ac_id=ac_id, sources=sources,
                     **{g: replace(groups[g], **overrides[g]) for g in groups})


def defaults_from(settings) -> Defaults:
    return Defaults(ledger=settings.ledger, reports=settings.reports, retention=settings.retention)


def change(conn: psycopg.Connection, ac_id: str, changes: dict[str, Any], *, actor_user_id: str,
           defaults: Defaults | None = None) -> Effective:
    """Apply {key: value or None}; None returns the key to its default. One audit row per key."""
    before = effective(conn, ac_id, defaults)
    for key, raw in changes.items():
        if key not in KEYS:
            raise SettingError(f"unknown setting {key!r}")
        if raw is None:
            conn.execute("DELETE FROM field.ac_setting WHERE ac_id = %s AND key = %s", (ac_id, key))
            new_value = None
        else:
            new_value = parse(key, raw)
            conn.execute(
                """INSERT INTO field.ac_setting (ac_id, key, value, updated_by) VALUES (%s, %s, %s, %s)
                   ON CONFLICT (ac_id, key) DO UPDATE SET value = EXCLUDED.value, updated_at = now(),
                       updated_by = EXCLUDED.updated_by""",
                (ac_id, key, str(new_value), actor_user_id),
            )
        audit(conn, ac_id, "setting_changed", actor_user_id=actor_user_id,
              data={"key": key, "old": _j(before.value(key)), "new": _j(new_value), "reset_to_default": raw is None})
    after = effective(conn, ac_id, defaults)
    if "media_retention_days" in changes:
        days = after.retention.media_retention_days
        conn.execute(
            """UPDATE field.media_asset SET delete_after = CASE WHEN %(d)s::int IS NULL THEN NULL
                                                             ELSE created_at + make_interval(days => %(d)s::int) END
               WHERE ac_id = %(ac)s AND status <> 'purged'""",
            {"d": days, "ac": ac_id},
        )
    return after


def _j(v: Any) -> Any:
    return str(v) if isinstance(v, Decimal) else v


def audit(conn: psycopg.Connection, ac_id: str, action: str, *, actor_user_id: str | None = None,
          actor_member_id: Any = None, target_member_id: Any = None, data: dict | None = None) -> None:
    conn.execute(
        """INSERT INTO field.admin_audit (ac_id, actor_user_id, actor_member_id, action, target_member_id, data)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        (ac_id, actor_user_id, actor_member_id, action, target_member_id, Jsonb(data) if data is not None else None),
    )


def all_ac_ids(conn: psycopg.Connection) -> list[str]:
    return [r["ac_id"] for r in conn.execute("SELECT DISTINCT ac_id FROM field.member ORDER BY 1")]
