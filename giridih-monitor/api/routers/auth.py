"""Authentication (LLD 9).

Two modes, set by AUTH_MODE:
  password  admin-issued passwords, no SMS cost - the default
  otp       phone OTP through an SMS provider
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from api.deps import CurrentUser, make_token, verify_password
from common.config import get_settings
from common.db import execute, query_one
from common.logging_setup import get_logger

log = get_logger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

OTP_TTL_MINUTES = 10
OTP_MAX_ATTEMPTS = 5


class PasswordLogin(BaseModel):
    phone: str = Field(min_length=6, max_length=20)
    password: str = Field(min_length=6, max_length=128)


class OtpRequest(BaseModel):
    phone: str = Field(min_length=6, max_length=20)


class OtpVerify(BaseModel):
    phone: str
    code: str = Field(min_length=4, max_length=8)


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    name: str
    block_id: int | None = None


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


@router.post("/login", response_model=Token)
def login(body: PasswordLogin) -> Token:
    if get_settings().auth_mode != "password":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Password login is disabled; use OTP")
    user = query_one(
        "SELECT user_id, name, role, block_id, password_hash FROM app_user "
        "WHERE phone = %s AND is_active",
        (body.phone,),
    )
    if user is None or not verify_password(body.password, user["password_hash"]):
        # Same message either way - do not reveal whether the phone is registered.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect phone or password")

    execute("UPDATE app_user SET last_login_at = now() WHERE user_id = %s", (user["user_id"],))
    return Token(access_token=make_token(user["user_id"], user["role"]),
                 role=user["role"], name=user["name"], block_id=user["block_id"])


@router.post("/otp", status_code=status.HTTP_202_ACCEPTED)
def request_otp(body: OtpRequest) -> dict:
    if get_settings().auth_mode != "otp":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "OTP login is disabled; use a password")
    user = query_one("SELECT user_id FROM app_user WHERE phone = %s AND is_active", (body.phone,))
    code = f"{secrets.randbelow(1_000_000):06d}"
    if user is not None:
        execute(
            "INSERT INTO auth_otp (phone, code_hash, expires_at) VALUES (%s, %s, %s)",
            (body.phone, _hash_code(code), datetime.now(UTC) + timedelta(minutes=OTP_TTL_MINUTES)),
        )
        _send_sms(body.phone, code)
    # Always the same response, registered or not.
    return {"sent": True, "expires_in_minutes": OTP_TTL_MINUTES}


@router.post("/verify", response_model=Token)
def verify_otp(body: OtpVerify) -> Token:
    row = query_one(
        "SELECT otp_id, code_hash, attempts FROM auth_otp "
        "WHERE phone = %s AND used_at IS NULL AND expires_at > now() "
        "ORDER BY otp_id DESC LIMIT 1",
        (body.phone,),
    )
    if row is None or row["attempts"] >= OTP_MAX_ATTEMPTS:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Code expired or too many attempts")

    execute("UPDATE auth_otp SET attempts = attempts + 1 WHERE otp_id = %s", (row["otp_id"],))
    if not secrets.compare_digest(row["code_hash"], _hash_code(body.code)):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect code")

    execute("UPDATE auth_otp SET used_at = now() WHERE otp_id = %s", (row["otp_id"],))
    user = query_one(
        "SELECT user_id, name, role, block_id FROM app_user WHERE phone = %s AND is_active",
        (body.phone,),
    )
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found")
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


def _send_sms(phone: str, code: str) -> None:
    settings = get_settings()
    provider = (settings.auth_mode == "otp") and __import__("os").environ.get("SMS_PROVIDER", "")
    if not provider:
        log.warning("SMS provider not configured - OTP for %s was generated but not sent", phone[-4:])
        return
    # Wire the provider here (MSG91 / Textlocal). Never log the code itself.
    log.info("OTP dispatched to ...%s via %s", phone[-4:], provider)
