"""Auth and role scoping (LLD 9, 12).

Three roles:
  admin       everything, including the review queue and spend reports
  strategist  everything except admin; sees the caste module
  block       their own block only, and the caste module is hidden from them

Block scoping is enforced here rather than in each query, so a new endpoint
cannot forget it: `scoped_block_id(user)` returns the block a query must be
filtered to, or None for unrestricted roles.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext

from common.config import get_settings
from common.db import query_one
from common.logging_setup import get_logger

log = get_logger(__name__)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer = HTTPBearer(auto_error=False)

ROLES = ("admin", "strategist", "block")


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
    return pwd_context.hash(raw)


def verify_password(raw: str, hashed: str | None) -> bool:
    if not hashed:
        return False
    try:
        return pwd_context.verify(raw, hashed)
    except Exception:
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

    row = query_one(
        "SELECT user_id, name, role, block_id, daily_token_budget FROM app_user "
        "WHERE user_id = %s AND is_active",
        (int(payload.get("sub", 0)),),
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
