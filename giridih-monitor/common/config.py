"""Configuration loaded from environment (see .env.example).

Deliberately dependency-free: the worker image does not install
pydantic-settings, and every container reads the same variables.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def _env_int(key: str, default: int) -> int:
    raw = _env(key)
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _env_float(key: str, default: float) -> float:
    raw = _env(key)
    try:
        return float(raw) if raw else default
    except ValueError:
        return default


def _env_bool(key: str, default: bool = False) -> bool:
    raw = _env(key).lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _env_list(key: str, default: str = "") -> list[str]:
    raw = _env(key, default)
    return [p.strip() for p in raw.split(",") if p.strip()]


# source_doc.kind values, mirroring the CHECK constraint in
# db/migrations/0008_ops.sql. Each may have its own storage backend.
DOC_KINDS = (
    "form20",
    "roll_mother",
    "roll_supplement",
    "ps_list",
    "sec_result",
    "census",
    "other",
)


def _env_storage_by_kind() -> dict[str, str]:
    """STORAGE_BACKEND_FORM20=s3, STORAGE_BACKEND_ROLL_MOTHER=local, and so on.

    One variable per kind rather than a parsed mapping string: a typo in
    'form20:s3,roll_mother:local' is easy to make and silently drops a rule,
    whereas a misspelt variable name simply does not apply and the default
    stands. Only non-empty values are returned so an unset variable does not
    mask STORAGE_BACKEND.
    """
    resolved: dict[str, str] = {}
    for kind in DOC_KINDS:
        value = _env(f"STORAGE_BACKEND_{kind.upper()}").lower()
        if value:
            resolved[kind] = value
    return resolved


@dataclass(frozen=True)
class Prices:
    """USD per million tokens. Re-check platform.claude.com/docs before budgeting."""

    haiku_in: float = 1.00
    haiku_out: float = 5.00
    sonnet_in: float = 2.00
    sonnet_out: float = 10.00
    cache_read_multiplier: float = 0.10
    cache_write_multiplier: float = 1.25
    batch_discount: float = 0.50


@dataclass(frozen=True)
class Settings:
    # Database
    database_url: str = ""
    readonly_db_url: str = ""

    # Auth
    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    jwt_ttl_minutes: int = 720
    auth_mode: str = "password"
    cors_origins: list[str] = field(default_factory=list)

    # Anthropic
    anthropic_api_key: str = ""
    model_router: str = "claude-haiku-4-5"
    model_lookup: str = "claude-haiku-4-5"
    model_analysis: str = "claude-sonnet-5"
    model_label: str = "claude-haiku-4-5"
    analysis_effort: str = "low"

    # Budgets
    monthly_cap_usd: float = 120.0
    degrade_at_pct: int = 80
    default_daily_token_budget: int = 150_000
    block_role_daily_token_budget: int = 60_000
    prices: Prices = field(default_factory=Prices)

    # Embeddings
    embed_model: str = "intfloat/multilingual-e5-small"
    embed_dim: int = 384
    embed_device: str = "cpu"

    # Paths
    raw_dir: Path = Path("raw")
    ocr_dir: Path = Path("ocr")
    backup_dir: Path = Path("backups")

    # Document storage (common/storage.py). `storage_backend` is the default;
    # `storage_backend_by_kind` overrides it per source_doc.kind, which is how
    # Form 20 goes to object storage while electoral rolls stay on this host.
    # Roll kinds are refused a remote backend regardless of what is set here -
    # the invariant lives in storage.assert_local_only, not in configuration.
    storage_backend: str = "local"
    storage_backend_by_kind: dict[str, str] = field(default_factory=dict)
    s3_endpoint: str = ""
    s3_bucket: str = ""
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_region: str = ""
    s3_prefix: str = ""

    # Ingestion
    # C13: keep the auditable original. The old default deleted the source
    # roll PDF while extract_pdf's page cache kept its full text on disk, so
    # the system destroyed the evidence and retained the personal data.
    retain_raw_rolls: bool = True
    ocr_min_confidence: int = 70
    pdf_text_min_chars: int = 40
    tesseract_langs: str = "hin+eng"
    nominatim_user_agent: str = "giridih-monitor/1.0"
    nominatim_rps: float = 1.0
    ceo_base: str = "https://ceo.jharkhand.gov.in"
    sec_base: str = "https://jharkhandsec.gov.in"

    # News
    news_user_agent: str = "giridih-monitor/1.0"
    news_timeout: int = 20
    news_max_items: int = 300

    # Ops
    log_level: str = "INFO"
    prompt_retention_days: int = 7
    backup_retention_days: int = 14
    tz: str = "Asia/Kolkata"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        database_url=_env("DATABASE_URL"),
        readonly_db_url=_env("READONLY_DB_URL"),
        jwt_secret=_env("JWT_SECRET"),
        jwt_algorithm=_env("JWT_ALGORITHM", "HS256"),
        jwt_ttl_minutes=_env_int("JWT_TTL_MINUTES", 720),
        auth_mode=_env("AUTH_MODE", "password"),
        cors_origins=_env_list("API_CORS_ORIGINS", "http://localhost:5173"),
        anthropic_api_key=_env("ANTHROPIC_API_KEY"),
        model_router=_env("MODEL_ROUTER", "claude-haiku-4-5"),
        model_lookup=_env("MODEL_LOOKUP", "claude-haiku-4-5"),
        model_analysis=_env("MODEL_ANALYSIS", "claude-sonnet-5"),
        model_label=_env("MODEL_LABEL", "claude-haiku-4-5"),
        analysis_effort=_env("ANALYSIS_EFFORT", "low"),
        monthly_cap_usd=_env_float("LLM_MONTHLY_CAP_USD", 120.0),
        degrade_at_pct=_env_int("LLM_DEGRADE_AT_PCT", 80),
        default_daily_token_budget=_env_int("DEFAULT_DAILY_TOKEN_BUDGET", 150_000),
        block_role_daily_token_budget=_env_int("BLOCK_ROLE_DAILY_TOKEN_BUDGET", 60_000),
        prices=Prices(
            haiku_in=_env_float("PRICE_HAIKU_IN", 1.00),
            haiku_out=_env_float("PRICE_HAIKU_OUT", 5.00),
            sonnet_in=_env_float("PRICE_SONNET_IN", 2.00),
            sonnet_out=_env_float("PRICE_SONNET_OUT", 10.00),
            cache_read_multiplier=_env_float("CACHE_READ_MULTIPLIER", 0.10),
            batch_discount=_env_float("BATCH_DISCOUNT", 0.50),
        ),
        embed_model=_env("EMBED_MODEL", "intfloat/multilingual-e5-small"),
        embed_dim=_env_int("EMBED_DIM", 384),
        embed_device=_env("EMBED_DEVICE", "cpu"),
        raw_dir=Path(_env("RAW_DIR", "raw")),
        ocr_dir=Path(_env("OCR_DIR", "ocr")),
        backup_dir=Path(_env("BACKUP_DIR", "backups")),
        storage_backend=_env("STORAGE_BACKEND", "local").lower(),
        storage_backend_by_kind=_env_storage_by_kind(),
        s3_endpoint=_env("S3_ENDPOINT"),
        s3_bucket=_env("S3_BUCKET"),
        s3_access_key=_env("S3_ACCESS_KEY"),
        s3_secret_key=_env("S3_SECRET_KEY"),
        s3_region=_env("S3_REGION"),
        s3_prefix=_env("S3_PREFIX"),
        retain_raw_rolls=_env_bool("RETAIN_RAW_ROLLS", True),
        ocr_min_confidence=_env_int("OCR_MIN_CONFIDENCE", 70),
        pdf_text_min_chars=_env_int("PDF_TEXT_MIN_CHARS", 40),
        tesseract_langs=_env("TESSERACT_LANGS", "hin+eng"),
        nominatim_user_agent=_env("NOMINATIM_USER_AGENT", "giridih-monitor/1.0"),
        nominatim_rps=_env_float("NOMINATIM_RPS", 1.0),
        ceo_base=_env("CEO_JHARKHAND_BASE", "https://ceo.jharkhand.gov.in"),
        sec_base=_env("SEC_JHARKHAND_BASE", "https://jharkhandsec.gov.in"),
        news_user_agent=_env("NEWS_USER_AGENT", "giridih-monitor/1.0"),
        news_timeout=_env_int("NEWS_CRAWL_TIMEOUT", 20),
        news_max_items=_env_int("NEWS_MAX_ITEMS_PER_RUN", 300),
        log_level=_env("LOG_LEVEL", "INFO"),
        prompt_retention_days=_env_int("PROMPT_RETENTION_DAYS", 7),
        backup_retention_days=_env_int("BACKUP_RETENTION_DAYS", 14),
        tz=_env("TZ", "Asia/Kolkata"),
    )
