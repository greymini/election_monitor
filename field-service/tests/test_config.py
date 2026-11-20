import pytest

from pulse.config import ConfigError, load_database_url, load_settings

GOOD = {
    "DATABASE_URL": "postgresql://postgres:@127.0.0.1:5432/pulse",
    "TELEGRAM_BOT_TOKEN": "1234567:TEST-ONLY-bot-token-not-real-abcdefgh",
    "TELEGRAM_BOT_USERNAME": "pulse_test_bot",
    "TELEGRAM_WEBHOOK_SECRET": "test-only-webhook-secret-0123456789",
    "JWT_SECRET": "test-only-jwt-secret-0123456789abcdef",
    "CORE_ADAPTER": "stub",
}


def test_good_settings_load():
    s = load_settings(GOOD)
    assert s.telegram_bot_username == "pulse_test_bot"
    assert "TEST-ONLY" not in repr(s)  # secrets never in repr


def test_example_file_is_refused():
    # the shipped .env.example must not be startable as-is
    from pulse.config import REPO_ROOT

    example = {}
    for line in (REPO_ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            example[k] = v
    with pytest.raises(ConfigError):
        load_settings(example)


@pytest.mark.parametrize(
    "key,value",
    [
        ("TELEGRAM_BOT_TOKEN", "change-me"),
        ("TELEGRAM_BOT_TOKEN", ""),
        ("TELEGRAM_BOT_TOKEN", "not-a-token"),
        ("TELEGRAM_WEBHOOK_SECRET", "short"),
        ("TELEGRAM_WEBHOOK_SECRET", "replace-with-a-long-random-secret-value"),
        ("JWT_SECRET", "changeme"),
        ("JWT_SECRET", "too-short"),
        ("DATABASE_URL", "postgresql://change-me"),
        ("TELEGRAM_BOT_USERNAME", "<your_bot>"),
        ("CORE_ADAPTER", "real"),
    ],
)
def test_placeholder_or_weak_values_are_refused(key, value):
    with pytest.raises(ConfigError) as e:
        load_settings({**GOOD, key: value})
    assert key in str(e.value)
    assert value not in str(e.value) or not value  # the value itself is never echoed


def test_missing_values_are_refused():
    with pytest.raises(ConfigError):
        load_settings({})


def test_database_url_only():
    assert load_database_url({"DATABASE_URL": GOOD["DATABASE_URL"]}) == GOOD["DATABASE_URL"]
    with pytest.raises(ConfigError):
        load_database_url({"DATABASE_URL": "postgresql://change-me"})


def test_ledger_limits_defaults_and_overrides():
    from decimal import Decimal

    s = load_settings(GOOD)
    assert s.ledger.large_entry == Decimal("25000") and s.ledger.undo_minutes == 10
    s = load_settings({**GOOD, "LEDGER_LARGE_ENTRY": "30000", "EXPENSE_SESSION_MINUTES": "20"})
    assert s.ledger.large_entry == Decimal("30000") and s.ledger.expense_session_minutes == 20
    for bad in ("abc", "0", "-5"):
        with pytest.raises(ConfigError):
            load_settings({**GOOD, "LEDGER_UNDO_MINUTES": bad})
