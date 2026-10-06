"""Authentication (LLD 9).

Production uses AUTH_MODE=password with a single APP_USERNAME / APP_PASSWORD.
"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from api.deps import CurrentUser, make_token, verify_password
from common.config import get_settings
from common.db import execute, query_one
from common.logging_setup import get_logger

log = get_logger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

class PasswordLogin(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=6, max_length=128)


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    name: str
    block_id: int | None = None


@router.post("/login", response_model=Token)
def login(body: PasswordLogin) -> Token:
    settings = get_settings()
    if settings.auth_mode != "password":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Password login is disabled")
    expected = (settings.app_username or "").strip()
    if expected and not secrets.compare_digest(body.username.strip(), expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect user id or password")
    user = query_one(
        "SELECT user_id, name, role, block_id, password_hash FROM app_user "
        "WHERE is_active AND (username = %s OR (%s = '' AND phone = %s))",
        (body.username.strip(), expected, body.username.strip()),
    )
    if user is None or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect user id or password")

    execute("UPDATE app_user SET last_login_at = now() WHERE user_id = %s", (user["user_id"],))
    return Token(access_token=make_token(user["user_id"], user["role"]),
                 role=user["role"], name=user["name"], block_id=user["block_id"])


@router.get("/me")
def me(user: CurrentUser) -> dict:
    return {
        "user_id": user.user_id, "name": user.name, "role": user.role,
        "block_id": user.block_id, "sees_caste": user.sees_caste,
        "daily_token_budget": user.daily_token_budget,
    }


