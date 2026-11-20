"""Signed, short-lived links for report media (LLD §11: no public links; 5 minutes).

The token names the member it was issued to, the report and the asset, and expires after 5 minutes.
It is signed with a key derived from JWT_SECRET, so it can never be used as a login token (and a
login token is never accepted here). Viewing re-checks that the member can still see the report.
"""
from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timedelta
from uuid import UUID

import jwt

from pulse.common.clock import utcnow

TTL = timedelta(minutes=5)
AUDIENCE = "pulse-media"


class BadToken(ValueError):
    pass


def _key(secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), b"pulse-media-link-v1", hashlib.sha256).hexdigest()


def issue(secret: str, member_id: UUID, report_id: UUID, asset_id: UUID, *, now: datetime | None = None) -> str:
    now = now or utcnow()
    claims = {"aud": AUDIENCE, "sub": str(member_id), "rid": str(report_id), "aid": str(asset_id),
              "iat": int(now.timestamp()), "exp": int((now + TTL).timestamp())}
    return jwt.encode(claims, _key(secret), algorithm="HS256")


def verify(secret: str, token: str, report_id: UUID, asset_id: UUID, *, now: datetime | None = None) -> UUID:
    """Return the member the link was issued to. Raises BadToken if forged, expired or for another file."""
    now = now or utcnow()
    try:
        claims = jwt.decode(token, _key(secret), algorithms=["HS256"], audience=AUDIENCE,
                            # times are checked against our clock below, not PyJWT's wall clock
                            options={"verify_exp": False, "verify_iat": False,
                                     "require": ["exp", "sub", "rid", "aid"]})
    except jwt.PyJWTError:
        raise BadToken("invalid link") from None
    if now.timestamp() >= claims["exp"]:
        raise BadToken("link expired")
    if claims["rid"] != str(report_id) or claims["aid"] != str(asset_id):
        raise BadToken("link is for another file")
    return UUID(claims["sub"])
