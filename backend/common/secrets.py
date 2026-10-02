"""Secret validation, run at startup so a misconfiguration is a boot failure.

Audit E1. `JWT_SECRET` defaulted to `""`; the app logged "every authenticated
request will fail" and started. The message was wrong in the dangerous
direction: `make_token` refuses to mint without a secret, but HS256
*verification* with an empty key is valid HMAC, so a token signed offline with
an empty key verified. `current_user` re-reads the role from the database, which
defeats a forged role claim - but not `sub=1`, and user 1 is the admin created
by `scripts/create_admin.py` in the README's own setup sequence. So an unset
variable was a working unauthenticated admin login.

Three rules, each because a real deployment gets it wrong in that specific way:

  * **Not empty** - the defect above.
  * **Not a placeholder** - `.env.example` ships `change-me-64-random-hex`, and
    an operator copying the file will miss a line.
  * **Long enough and not degenerate** - 32 bytes is HS256's own key size, and
    length alone is a poor proxy: `'a' * 64` is long and worthless.

Errors never echo the secret. A startup failure goes to logs and often into a
paste in a support channel.
"""

from __future__ import annotations

MIN_SECRET_BYTES = 32

# A secret this weak is indistinguishable from unset. 8 distinct characters over
# 32+ bytes still leaves a keyspace nobody should rely on, but it is far enough
# above 'aaaa...' to avoid refusing a legitimate if unusual passphrase.
MIN_DISTINCT_CHARS = 8


class SecretError(RuntimeError):
    """A secret is missing, a placeholder, or too weak to be worth having."""


def check_jwt_secret(secret: str | None) -> None:
    """Raise `SecretError` unless `secret` is fit to verify tokens with.

    Imported here rather than from common.config to avoid a cycle: config owns
    the placeholder list because it owns the environment contract.
    """
    from common.config import PLACEHOLDER_SECRETS

    if secret is None or not secret.strip():
        raise SecretError(
            "JWT_SECRET is not set. The API will not start without it: HS256 "
            "verification with an empty key succeeds, so anyone could sign an "
            "admin token offline. Generate one with `openssl rand -hex 32`."
        )

    value = secret.strip()

    if value.lower() in PLACEHOLDER_SECRETS:
        raise SecretError(
            "JWT_SECRET is still the placeholder from .env.example. Replace it "
            "with `openssl rand -hex 32`."
        )

    if len(value.encode("utf-8")) < MIN_SECRET_BYTES:
        raise SecretError(
            f"JWT_SECRET is shorter than {MIN_SECRET_BYTES} bytes, which is HS256's "
            "own key size. A short secret can be brute forced offline against any "
            "token its holder has seen. Generate one with `openssl rand -hex 32`."
        )

    if len(set(value)) < MIN_DISTINCT_CHARS:
        raise SecretError(
            f"JWT_SECRET has fewer than {MIN_DISTINCT_CHARS} distinct characters, so "
            "its length is not buying any entropy. Generate one with "
            "`openssl rand -hex 32`."
        )
