"""Environment loading and the placeholder-secret check.

Every secret comes from the environment (optionally a git-ignored `.env` file). `load_settings`
refuses to return settings while any secret is missing, a placeholder or too weak, so the app
cannot start half-configured.
"""
from __future__ import annotations

import os
import re
import secrets
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Mapping

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CORE_SEED = Path(__file__).resolve().parent / "core_adapter" / "seed.json"
def _default_media_dir() -> Path:
    """Outside the repository (preflight insists): %LOCALAPPDATA%\\pulse\\media or ~/.local/share/pulse/media."""
    base = os.environ.get("LOCALAPPDATA") if os.name == "nt" else None
    return (Path(base) if base else Path.home() / ".local" / "share") / "pulse" / "media"


DEFAULT_MEDIA_DIR = _default_media_dir()
DEMO_PGDATA = REPO_ROOT / ".demo-pgdata"       # the demo's own embedded PostgreSQL (scripts/demo.py)
DEMO_MEDIA_DIR = REPO_ROOT / ".demo-media"

LINK_TOKEN_TTL_HOURS = 24

_PLACEHOLDER_MARKERS = ("change-me", "changeme", "change_me", "replace", "placeholder", "your-", "your_", "<", ">")
_BOT_TOKEN_RE = re.compile(r"^\d{5,}:[A-Za-z0-9_-]{30,}$")
_WEBHOOK_SECRET_RE = re.compile(r"^[A-Za-z0-9_-]{32,256}$")
_BOT_USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")
MIN_JWT_SECRET_LEN = 32


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Retention:
    """Retention and cleanup defaults; per AC they can be overridden (pulse/settings_ac.py).
    None = keep for ever. Bot messages can only be deleted within 48 hours of sending (Bot API)."""

    media_retention_days: int | None = None
    void_retention_days: int | None = None      # stored only: what happens to voided entries is undecided
    chat_cleanup_hours: int = 24


def load_retention(env: Mapping[str, str]) -> "Retention":
    values: dict = {}
    for attr, var in (("media_retention_days", "MEDIA_RETENTION_DAYS"), ("void_retention_days", "VOID_RETENTION_DAYS"),
                      ("chat_cleanup_hours", "CHAT_CLEANUP_HOURS")):
        raw = env.get(var, "").strip()
        if raw:
            if not raw.isdigit() or int(raw) <= 0:
                raise ConfigError(f"{var} must be a positive whole number")
            values[attr] = int(raw)
    if values.get("chat_cleanup_hours", 24) >= 48:
        raise ConfigError("CHAT_CLEANUP_HOURS must be under 48: Telegram lets bots delete messages only within 48 hours")
    return Retention(**values)


@dataclass(frozen=True)
class LedgerLimits:
    """Ledger thresholds (LLD §4.5 defaults). Env-overridable; per-AC settings come later."""

    large_entry: Decimal = Decimal("25000")          # flag `large` at or above
    backdated_days: int = 7                          # flag `backdated` when older than this
    undo_minutes: int = 10                           # creator may undo within this
    confirm_amount_above: Decimal = Decimal("1000000")  # bot asks to confirm above ₹10,00,000
    expense_session_minutes: int = 15                # expense, advance and return draft expiry
    tail_threshold: Decimal = Decimal("2000")        # tail analysis: entries at or below
    pin_limit: Decimal = Decimal("50000")            # bot asks for the PIN at or above (LLD §4.5, §11)
    ack_remind_hours: int = 24                       # remind an unacknowledged advance or return
    ack_flag_hours: int = 48                         # then flag it ack_overdue
    followup_days: int = 14                          # impact follow-up for ASSIST and WORKS (LLD §7)


@dataclass(frozen=True)
class ReportLimits:
    """Ground-report intake (LLD §4.5, §5.3)."""

    join_window_seconds: int = 180          # media within 3 minutes of the last item joins the draft
    autofile_minutes: int = 10              # a draft untouched this long files itself
    add_more_minutes: int = 10              # "add more" accepts items this long after filing
    max_photos: int = 10
    max_voice_seconds: int = 300            # total voice and audio per report
    # Telegram cloud Bot API: getFile downloads up to 20 MB. Not re-checked against the live docs from
    # this machine (proxy); confirm and override with TG_MAX_DOWNLOAD_BYTES if it changes.
    max_download_bytes: int = 20 * 1024 * 1024


_REPORT_ENV = {
    "join_window_seconds": "REPORT_JOIN_WINDOW_SECONDS",
    "autofile_minutes": "REPORT_AUTOFILE_MINUTES",
    "add_more_minutes": "REPORT_ADD_MORE_MINUTES",
    "max_photos": "REPORT_MAX_PHOTOS",
    "max_voice_seconds": "REPORT_MAX_VOICE_SECONDS",
    "max_download_bytes": "TG_MAX_DOWNLOAD_BYTES",
}


def load_report_limits(env: Mapping[str, str]) -> ReportLimits:
    values = {}
    for attr, var in _REPORT_ENV.items():
        raw = env.get(var, "").strip()
        if raw:
            if not raw.isdigit() or int(raw) <= 0:
                raise ConfigError(f"{var} must be a positive whole number")
            values[attr] = int(raw)
    return ReportLimits(**values)


_LIMIT_ENV = {
    "large_entry": ("LEDGER_LARGE_ENTRY", Decimal),
    "backdated_days": ("LEDGER_BACKDATED_DAYS", int),
    "undo_minutes": ("LEDGER_UNDO_MINUTES", int),
    "confirm_amount_above": ("LEDGER_CONFIRM_AMOUNT_ABOVE", Decimal),
    "expense_session_minutes": ("EXPENSE_SESSION_MINUTES", int),
    "tail_threshold": ("LEDGER_TAIL_THRESHOLD", Decimal),
    "pin_limit": ("LEDGER_PIN_LIMIT", Decimal),
    "ack_remind_hours": ("LEDGER_ACK_REMIND_HOURS", int),
    "ack_flag_hours": ("LEDGER_ACK_FLAG_HOURS", int),
    "followup_days": ("FOLLOWUP_DAYS", int),
}


def load_ledger_limits(env: Mapping[str, str]) -> LedgerLimits:
    values = {}
    for attr, (var, conv) in _LIMIT_ENV.items():
        raw = env.get(var, "").strip()
        if not raw:
            continue
        try:
            value = conv(raw)
        except (ValueError, InvalidOperation):
            raise ConfigError(f"{var} must be a number") from None
        if value <= 0:
            raise ConfigError(f"{var} must be greater than zero")
        values[attr] = value
    return LedgerLimits(**values)


@dataclass(frozen=True)
class Settings:
    database_url: str
    telegram_bot_token: str
    telegram_bot_username: str
    telegram_webhook_secret: str
    jwt_secret: str
    core_adapter: str = "stub"
    core_seed_path: Path = DEFAULT_CORE_SEED
    core_database_url: str = ""                  # CORE_DATABASE_URL, used only when CORE_ADAPTER=core
    core_jwt_secret: str = ""                    # CORE_JWT_SECRET: the core's token secret, not JWT_SECRET
    link_token_ttl_hours: int = LINK_TOKEN_TTL_HOURS
    ledger: LedgerLimits = field(default_factory=LedgerLimits)
    reports: ReportLimits = field(default_factory=ReportLimits)
    media_dir: Path = DEFAULT_MEDIA_DIR
    retention: "Retention" = field(default_factory=lambda: Retention())
    bot_mode: str = "polling"                    # polling | webhook (checked by scripts/preflight.py)
    webhook_url: str | None = None
    asr_engine: str = "none"                     # pulse/asr/<name>.py; none = reports stay audio-only
    map_tile_url: str = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"   # MAP_TILE_URL, same as the core's map
    map_attribution: str = "© OpenStreetMap contributors"                  # MAP_ATTRIBUTION
    demo_mode: bool = False                      # dev-only browser demo (pulse/demo); see load_demo_settings
    demo_pgdata: Path = DEMO_PGDATA              # demo mode refuses any database not stored here

    def __repr__(self) -> str:  # never print secrets
        return f"Settings(core_adapter={self.core_adapter!r}, bot={self.telegram_bot_username!r})"


def _is_placeholder(value: str) -> bool:
    v = value.strip().lower()
    return not v or any(m in v for m in _PLACEHOLDER_MARKERS)


def check_settings(s: Settings) -> None:
    """Raise ConfigError listing every bad value. Messages name the variable, never its value."""
    problems: list[str] = []
    if _is_placeholder(s.database_url) or not s.database_url.startswith(("postgresql://", "postgres://")):
        problems.append("DATABASE_URL must be a real postgresql:// URL")
    if _is_placeholder(s.telegram_bot_token) or not _BOT_TOKEN_RE.match(s.telegram_bot_token):
        problems.append("TELEGRAM_BOT_TOKEN must be a real bot token from @BotFather")
    if _is_placeholder(s.telegram_bot_username) or not _BOT_USERNAME_RE.match(s.telegram_bot_username):
        problems.append("TELEGRAM_BOT_USERNAME must be the bot's username without @")
    if _is_placeholder(s.telegram_webhook_secret) or not _WEBHOOK_SECRET_RE.match(s.telegram_webhook_secret):
        problems.append("TELEGRAM_WEBHOOK_SECRET must be 32-256 characters of A-Z a-z 0-9 _ -")
    if _is_placeholder(s.jwt_secret) or len(s.jwt_secret) < MIN_JWT_SECRET_LEN:
        problems.append(f"JWT_SECRET must be at least {MIN_JWT_SECRET_LEN} characters and not a placeholder")
    if s.bot_mode not in ("polling", "webhook"):
        problems.append("BOT_MODE must be polling or webhook")
    if s.bot_mode == "webhook" and not (s.webhook_url or "").startswith("https://"):
        problems.append("WEBHOOK_URL must be an https:// URL when BOT_MODE is webhook")
    if not re.fullmatch(r"[a-z][a-z0-9_]*", s.asr_engine):
        problems.append("ASR_ENGINE must be the name of a module in pulse/asr (none, fake, ...)")
    if s.core_adapter not in ("stub", "core"):
        problems.append("CORE_ADAPTER must be 'stub' or 'core'")
    if s.core_adapter == "core":
        if _is_placeholder(s.core_database_url) or not s.core_database_url.startswith(("postgresql://", "postgres://")):
            problems.append("CORE_DATABASE_URL must be a real postgresql:// URL when CORE_ADAPTER is core")
        if _is_placeholder(s.core_jwt_secret) or len(s.core_jwt_secret) < MIN_JWT_SECRET_LEN:
            problems.append(f"CORE_JWT_SECRET must be at least {MIN_JWT_SECRET_LEN} characters and not a placeholder")
        elif s.core_jwt_secret == s.jwt_secret:
            problems.append("CORE_JWT_SECRET must differ from JWT_SECRET")
    if not s.map_tile_url.startswith(("https://", "http://")) or "{z}" not in s.map_tile_url:
        problems.append("MAP_TILE_URL must be an http(s) tile URL with {z}, {x} and {y}")
    if s.demo_mode and s.bot_mode != "polling":
        problems.append("DEMO_MODE cannot be combined with BOT_MODE=webhook")
    if problems:
        raise ConfigError("Refusing to start:\n  - " + "\n  - ".join(problems))


def _env(env: Mapping[str, str] | None) -> Mapping[str, str]:
    if env is None:
        load_dotenv(REPO_ROOT / ".env", override=False)
        return os.environ
    return env


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    if (os.environ if env is None else env).get("DEMO_MODE", "").strip() == "1":
        return load_demo_settings(os.environ if env is None else env)
    e = _env(env)
    s = Settings(
        database_url=e.get("DATABASE_URL", ""),
        telegram_bot_token=e.get("TELEGRAM_BOT_TOKEN", ""),
        telegram_bot_username=e.get("TELEGRAM_BOT_USERNAME", ""),
        telegram_webhook_secret=e.get("TELEGRAM_WEBHOOK_SECRET", ""),
        jwt_secret=e.get("JWT_SECRET", ""),
        core_adapter=e.get("CORE_ADAPTER", "stub"),
        core_database_url=e.get("CORE_DATABASE_URL", "").strip(),
        core_jwt_secret=e.get("CORE_JWT_SECRET", "").strip(),
        ledger=load_ledger_limits(e),
        reports=load_report_limits(e),
        media_dir=Path(e.get("MEDIA_DIR") or DEFAULT_MEDIA_DIR),
        retention=load_retention(e),
        bot_mode=e.get("BOT_MODE", "polling").strip() or "polling",
        webhook_url=e.get("WEBHOOK_URL", "").strip() or None,
        asr_engine=e.get("ASR_ENGINE", "").strip() or "none",
        **_map_settings(e),
    )
    check_settings(s)
    return s


def _map_settings(e: Mapping[str, str]) -> dict[str, str]:
    out = {}
    if e.get("MAP_TILE_URL", "").strip():
        out["map_tile_url"] = e["MAP_TILE_URL"].strip()
    if e.get("MAP_ATTRIBUTION", "").strip():
        out["map_attribution"] = e["MAP_ATTRIBUTION"].strip()
    return out


def load_demo_settings(env: Mapping[str, str]) -> Settings:
    """DEMO_MODE=1 (set by scripts/demo.py): no .env and no Telegram. Every secret is generated here,
    held in memory and gone when the process exits. Only DATABASE_URL is read, and the app refuses to
    start unless that database lives in DEMO_PGDATA (pulse/demo.assert_embedded)."""
    s = Settings(
        database_url=env.get("DATABASE_URL", ""),
        telegram_bot_token=f"{secrets.randbelow(9 * 10**9) + 10**9}:{secrets.token_urlsafe(30)}",
        telegram_bot_username="pulse_demo_bot",
        telegram_webhook_secret=secrets.token_urlsafe(32),
        jwt_secret=secrets.token_urlsafe(48),
        media_dir=DEMO_MEDIA_DIR,
        asr_engine="fake",
        demo_mode=True,
        **_map_settings(env),
    )
    check_settings(s)
    return s


def load_database_url(env: Mapping[str, str] | None = None) -> str:
    """For tools that only need the database (migration runner)."""
    url = _env(env).get("DATABASE_URL", "")
    if _is_placeholder(url) or not url.startswith(("postgresql://", "postgres://")):
        raise ConfigError("DATABASE_URL must be a real postgresql:// URL")
    return url
