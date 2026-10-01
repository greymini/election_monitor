"""Password hashing. A regression test for a defect that broke every login.

`requirements-api.txt` pinned `passlib[bcrypt]==1.7.4` with `bcrypt==5.0.0`.
passlib's bcrypt backend probes for an old wraparound bug by hashing a secret
longer than 72 bytes while it initialises; bcrypt 4.1 and later raise
`ValueError` on an over-long secret instead of truncating it. So the probe
itself raised, **every** `hash_password()` call failed with "password cannot be
longer than 72 bytes", `scripts/create_admin.py` could not create a user and no
password login could succeed.

Nothing caught it. The tests that touch auth build their JWTs directly and never
hash a password, and `create_admin.py` had never been run, because until
`scripts/dev_stack.py` existed there was no database to run it against.

See DECISIONS.md D-010. The fix calls bcrypt directly.

Ported from the OneDrive copy, whose fix *truncated* passwords at 72 bytes.
This repository had already fixed the same defect independently and chose to
*reject* an over-long password instead - a truncating hash lets every password
sharing the first 72 bytes open the same account - so the three truncation
tests are replaced by tests of rejection.
"""

from __future__ import annotations

import pytest

from api.deps import MAX_PASSWORD_BYTES, hash_password, verify_password


def test_a_password_round_trips():
    """The test that would have caught it: this raised, it did not fail."""
    hashed = hash_password("a-dev-password-1234")
    assert verify_password("a-dev-password-1234", hashed)


def test_hashing_produces_a_bcrypt_2b_hash():
    # The same format passlib produced, so any hash already in app_user still
    # verifies after the change.
    hashed = hash_password("whatever")
    assert hashed.startswith("$2b$")
    assert len(hashed) == 60


def test_the_salt_is_random_so_two_hashes_of_one_password_differ():
    assert hash_password("same") != hash_password("same")


def test_a_wrong_password_is_rejected():
    assert not verify_password("wrong", hash_password("right"))


@pytest.mark.parametrize("hashed", [None, "", "not-a-hash", "$2b$12$short", "x" * 60])
def test_a_missing_or_malformed_hash_is_a_failed_login_not_an_exception(hashed):
    # A corrupt password_hash column must not turn a login into a 500.
    assert verify_password("anything", hashed) is False


def test_a_password_longer_than_bcrypts_limit_is_refused_not_truncated():
    """bcrypt reads at most 72 bytes. Silently truncating would make two
    passwords that share a 72-byte prefix unlock the same account."""
    long_password = "correct horse battery staple " * 10
    assert len(long_password.encode()) > MAX_PASSWORD_BYTES
    with pytest.raises(ValueError, match="bytes"):
        hash_password(long_password)


def test_the_limit_is_bytes_not_characters():
    """Devanagari is three bytes a character: 24 fit, 25 do not."""
    assert verify_password("क" * 24, hash_password("क" * 24))
    with pytest.raises(ValueError):
        hash_password("क" * 25)


def test_an_over_long_password_never_verifies():
    """verify_password must not raise, and must not accept a long password
    against a hash of its 72-byte prefix."""
    base = "x" * MAX_PASSWORD_BYTES
    assert verify_password(base + "tail", hash_password(base)) is False
