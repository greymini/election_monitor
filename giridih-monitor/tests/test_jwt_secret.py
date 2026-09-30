"""Audit E1: an unset JWT_SECRET is an unauthenticated admin login.

The app logged `JWT_SECRET is not set - every authenticated request will fail`
and started anyway. That message was wrong. `make_token` does refuse to mint
without a secret, but HS256 *verification* with an empty key is valid HMAC, so
`jwt.decode(token, "", algorithms=["HS256"])` succeeds. Anyone could sign
`{"sub": "1", "role": "admin"}` offline with an empty key and be admitted;
`current_user` re-reads the role from the database, which defeats a forged role
claim but not `sub=1`, and user 1 is the admin `create_admin.py` creates in the
README's own setup sequence.

Verified in this environment before fixing it:

    >>> from jose import jwt
    >>> t = jwt.encode({'sub': '1', 'role': 'admin'}, '', algorithm='HS256')
    >>> jwt.decode(t, '', algorithms=['HS256'])
    {'sub': '1', 'role': 'admin'}

The forgery tests below are the ones that matter. The startup tests exist so the
condition is caught at boot rather than on the first request.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from jose import jwt

from common.config import PLACEHOLDER_SECRETS, get_settings
from common.secrets import MIN_SECRET_BYTES, SecretError, check_jwt_secret

# A realistic `openssl rand -hex 32` output. Note "9f" * 32 would be refused,
# correctly: 64 characters with two distinct values is not a secret.
GOOD = "7b1e4c0a93f6d28b5a7e10c4fd39b862e5470af1cd83629be07a4f15d2c8930e"


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# --------------------------------------------------------------------------
# The forgery this prevents
# --------------------------------------------------------------------------


def test_an_empty_key_can_mint_and_verify_a_token():
    """Not a defect being asserted - a property of HMAC being documented, which
    is why an empty secret cannot be allowed to reach jwt.decode."""
    token = jwt.encode({"sub": "1", "role": "admin"}, "", algorithm="HS256")
    assert jwt.decode(token, "", algorithms=["HS256"]) == {"sub": "1", "role": "admin"}


def test_the_guard_refuses_the_empty_secret_that_makes_that_possible():
    with pytest.raises(SecretError) as excinfo:
        check_jwt_secret("")
    assert "not set" in str(excinfo.value)


def test_the_guard_refuses_the_shipped_placeholder():
    """`.env.example` ships JWT_SECRET=change-me-64-random-hex. An operator who
    copies the file and forgets this line must not get a running system."""
    with pytest.raises(SecretError) as excinfo:
        check_jwt_secret("change-me-64-random-hex")
    assert "placeholder" in str(excinfo.value)


@pytest.mark.parametrize("placeholder", sorted(PLACEHOLDER_SECRETS))
def test_every_placeholder_in_env_example_is_refused(placeholder):
    with pytest.raises(SecretError):
        check_jwt_secret(placeholder)


def test_the_placeholder_list_actually_covers_env_example():
    """If a future .env.example introduces a new placeholder and nobody adds it
    here, the guard stops covering the case it exists for."""
    from pathlib import Path

    text = Path(".env.example").read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("JWT_SECRET="):
            shipped = line.split("=", 1)[1].strip()
            assert shipped in PLACEHOLDER_SECRETS, (
                f".env.example ships JWT_SECRET={shipped!r}, which is not in "
                "PLACEHOLDER_SECRETS, so the guard would accept it"
            )
            break
    else:
        pytest.fail("no JWT_SECRET line in .env.example")


def test_placeholder_matching_ignores_case_and_surrounding_whitespace():
    with pytest.raises(SecretError):
        check_jwt_secret("  CHANGE-ME-64-RANDOM-HEX  ")


def test_a_short_secret_is_refused():
    """32 bytes is the floor: HS256's own key size. A short secret is brute
    forceable offline against any token the holder has seen."""
    with pytest.raises(SecretError) as excinfo:
        check_jwt_secret("a" * (MIN_SECRET_BYTES - 1))
    assert str(MIN_SECRET_BYTES) in str(excinfo.value)


def test_a_low_entropy_secret_of_adequate_length_is_refused():
    """`aaaa...` is 64 characters and worthless. Length alone is a poor proxy,
    so a secret with almost no distinct characters is refused too."""
    with pytest.raises(SecretError, match="distinct"):
        check_jwt_secret("a" * 64)


def test_a_generated_secret_is_accepted():
    check_jwt_secret(GOOD)
    check_jwt_secret("k7Qm2xPz9Lr4Vt8Nw1Hs6Jd3Bg5Yc0Af")  # 32 chars, mixed


def test_the_documented_generator_produces_an_acceptable_secret():
    """RUN.md tells the operator to use `openssl rand -hex 32`. Whatever that
    produces must pass, or the instruction is wrong."""
    import secrets

    check_jwt_secret(secrets.token_hex(32))


# --------------------------------------------------------------------------
# Startup
# --------------------------------------------------------------------------


def _import_app_with(monkeypatch, **env):
    """Import api.main from scratch under a given environment.

    Only `api.*` is dropped from the module cache, deliberately. Dropping
    `common.*` too would re-import common.secrets and mint a *new* SecretError
    class, so `pytest.raises(SecretError)` would not match the one the app
    raises - same name, different object. Clearing the settings cache is enough
    to make the new environment visible, since lifespan calls get_settings()
    itself.
    """
    import importlib
    import sys

    for key, value in env.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    for name in [n for n in list(sys.modules) if n.split(".")[0] == "api"]:
        del sys.modules[name]
    get_settings.cache_clear()
    return importlib.import_module("api.main")


def test_the_app_refuses_to_start_without_a_secret(monkeypatch):
    """The check runs in the lifespan handler, so uvicorn exits rather than
    serving. Importing the module must still work - otherwise nothing could
    introspect the app, including the topology tests."""
    main = _import_app_with(monkeypatch, JWT_SECRET="")

    with pytest.raises(SecretError):
        with TestClient(main.app):
            pass


def test_the_app_refuses_to_start_on_the_placeholder(monkeypatch):
    main = _import_app_with(monkeypatch, JWT_SECRET="change-me-64-random-hex")
    with pytest.raises(SecretError):
        with TestClient(main.app):
            pass


def test_the_app_starts_with_a_real_secret(monkeypatch):
    """And does not reach the database to do it: startup must not depend on
    Postgres being up."""
    main = _import_app_with(monkeypatch, JWT_SECRET=GOOD)
    with TestClient(main.app) as client:
        assert client.get("/config").status_code == 200


def test_the_failure_message_does_not_echo_the_secret(monkeypatch):
    """A startup error lands in logs and often in a paste. It must say what is
    wrong without reproducing the value."""
    secret = "sup3r-s3cret-but-far-too-short"
    with pytest.raises(SecretError) as excinfo:
        check_jwt_secret(secret)
    assert secret not in str(excinfo.value)
