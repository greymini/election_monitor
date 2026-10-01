"""Auth, role scoping and constituency resolution (LLD 9, 12; spec 2).

Three roles:
  admin       everything, including the review queue and spend reports
  strategist  everything except admin; sees the caste module
  block       their own block only, and the caste module is hidden from them

Block scoping is enforced here rather than in each query, so a new endpoint
cannot forget it: `scoped_block_id(user)` returns the block a query must be
filtered to, or None for unrestricted roles.

Constituency scoping works the same way. Every data route lives under
`/acs/{ac_number}/...` and takes `CurrentAC`, which resolves the number to a row
and 404s on an unknown or inactive one. Doing it in a dependency rather than in
each handler is the point: the spec warns that a half-scoped schema is worse
than an unscoped one because it silently mixes constituencies, and the same is
true of the API. A route that forgets to filter by AC would serve one
constituency's booths under another's URL.

A block-role user belongs to a block, and a block belongs to exactly one AC, so
asking for a different AC's data is a 403 rather than an empty result - an empty
result would read as "nothing loaded" and send someone looking for a data
problem that does not exist.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated

import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from common.config import get_settings
from common.db import query_one
from common.logging_setup import get_logger

log = get_logger(__name__)

# bcrypt directly, not through passlib.
#
# `passlib[bcrypt]==1.7.4` was pinned while bcrypt itself was left unpinned, so
# pip installed bcrypt 5.0.0 and passlib 1.7.4 - which reads
# `bcrypt.__about__.__version__`, an attribute removed in bcrypt 4.1. Its
# backend detection then failed and every call raised "password cannot be longer
# than 72 bytes" **for every password, at any length**, including a
# twelve-character one. Nobody could be created and nobody could log in.
#
# Nothing caught it because nothing had ever called these two functions against
# the installed dependencies: the auth tests exercised token minting, not
# hashing. It surfaced the first time a test tried to create a user and log in.
#
# Pinning bcrypt back below 4.1 would restore passlib, but passlib's last
# release was 2020 and this breakage is permanent, so the dependency goes
# instead. The output format is unchanged - the same `$2b$12$` strings passlib
# was producing - so any hash already stored still verifies.

# bcrypt truncates at 72 bytes. That is a property of the algorithm, not of any
# library, and silently accepting a longer password would mean two different
# passwords that share a 72-byte prefix both unlock the account.
MAX_PASSWORD_BYTES = 72

BCRYPT_ROUNDS = 12
bearer = HTTPBearer(auto_error=False)

ROLES = ("admin", "strategist", "block")


@dataclass
class AC:
    """One constituency, as resolved from the path."""

    ac_id: int
    ac_number: int
    name_en: str
    name_hi: str
    reservation: str
    verified: bool
    bypoll_due: object | None
    vacancy_date: object | None

    @property
    def label(self) -> str:
        return f"AC-{self.ac_number}"


@dataclass
class User:
    user_id: int
    name: str
    role: str
    block_id: int | None
    daily_token_budget: int

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def sees_caste(self) -> bool:
        # The caste module is hidden from block-level users (LLD 12).
        return self.role in {"admin", "strategist"}


def hash_password(raw: str) -> str:
    """A bcrypt hash, as a `$2b$` string.

    Raises on a password over 72 bytes rather than truncating it, because a
    truncating hash means every password sharing the first 72 bytes opens the
    same account. The limit is in bytes, not characters: a Devanagari
    passphrase reaches it in about 24 characters, so the message says bytes.
    """
    encoded = raw.encode("utf-8")
    if len(encoded) > MAX_PASSWORD_BYTES:
        raise ValueError(
            f"password is {len(encoded)} bytes; bcrypt accepts at most "
            f"{MAX_PASSWORD_BYTES}. Note this is bytes, not characters - "
            "non-Latin scripts reach the limit sooner."
        )
    return bcrypt.hashpw(encoded, bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode("ascii")


def verify_password(raw: str, hashed: str | None) -> bool:
    """Whether `raw` matches `hashed`. False on anything malformed.

    Never raises. A stored hash that is empty, truncated or from a scheme this
    no longer supports is a failed login, not a 500 - and an exception here
    would be an authentication bypass in any caller that wrapped it in a
    try/except returning True.
    """
    if not hashed:
        return False
    try:
        encoded = raw.encode("utf-8")
        if len(encoded) > MAX_PASSWORD_BYTES:
            return False
        return bcrypt.checkpw(encoded, hashed.encode("ascii"))
    except (ValueError, TypeError, UnicodeEncodeError):
        return False


def make_token(user_id: int, role: str) -> str:
    settings = get_settings()
    if not settings.jwt_secret:
        raise RuntimeError("JWT_SECRET is not set")
    expires = datetime.now(UTC) + timedelta(minutes=settings.jwt_ttl_minutes)
    payload = {"sub": str(user_id), "role": role, "exp": expires}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def current_user(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> User:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    settings = get_settings()
    try:
        payload = jwt.decode(creds.credentials, settings.jwt_secret,
                             algorithms=[settings.jwt_algorithm])
    except JWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token") from exc

    try:
        user_id = int(payload.get("sub", 0))
    except (TypeError, ValueError):
        # A well-signed token with a malformed subject is still not a session;
        # int() raising here was a 500.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token subject") from None
    row = query_one(
        "SELECT user_id, name, role, block_id, daily_token_budget FROM app_user "
        "WHERE user_id = %s AND is_active",
        (user_id,),
    )
    if row is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found or disabled")
    return User(**row)


CurrentUser = Annotated[User, Depends(current_user)]


def require_role(*allowed: str):
    def checker(user: CurrentUser) -> User:
        if user.role not in allowed:
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                f"This view needs one of: {', '.join(allowed)}")
        return user

    return checker


AdminUser = Annotated[User, Depends(require_role("admin"))]
StrategistUser = Annotated[User, Depends(require_role("admin", "strategist"))]


def scoped_block_id(user: User) -> int | None:
    """The block a query must be restricted to, or None if unrestricted."""
    return user.block_id if user.role == "block" else None


def current_ac(ac_number: int, user: CurrentUser) -> AC:
    """Resolve `/acs/{ac_number}/...` to a constituency the caller may see."""
    row = query_one(
        "SELECT ac_id, ac_number, name_en, name_hi, reservation, verified, "
        "bypoll_due, vacancy_date, is_active FROM ac WHERE ac_number = %s",
        (ac_number,),
    )
    if row is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"No constituency numbered {ac_number}. Seeded constituencies are listed at /acs.",
        )
    if not row.pop("is_active"):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"AC-{ac_number} ({row['name_en']}) is not active in this deployment.",
        )

    ac = AC(**row)

    # A block belongs to one AC. Asking for another AC's data is a refusal, not
    # an empty answer.
    if user.role == "block" and user.block_id is not None:
        owner = query_one("SELECT ac_id FROM block WHERE block_id = %s", (user.block_id,))
        if owner is not None and owner["ac_id"] != ac.ac_id:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Your block is not in this constituency.",
            )
    return ac


CurrentAC = Annotated[AC, Depends(current_ac)]
