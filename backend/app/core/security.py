"""Password hashing (Argon2id), JWT access tokens, opaque refresh tokens (CONTRACTS §3).

- Access: JWT HS256 (PyJWT). Payload: sub, org, roles, typ:"access", iat, exp, jti.
- Refresh: opaque 256-bit random, SHA-256 hash stored (char64), plaintext returned once.
"""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import argon2
import jwt
from argon2 import PasswordHasher

from app.core.config import Settings
from app.core.errors import TokenExpired, TokenInvalid

_ph = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)  # argon2id by default (argon2-cffi)


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _ph.verify(password_hash, password)
    except argon2.exceptions.VerifyMismatchError:
        return False
    except argon2.exceptions.InvalidHashError:
        return False


def password_needs_rehash(password_hash: str) -> bool:
    try:
        return _ph.check_needs_rehash(password_hash)
    except argon2.exceptions.InvalidHashError:
        return True


def create_access_token(settings: Settings, *, user_id: uuid.UUID | str,
                        org_id: uuid.UUID | str | None, roles: list[str]) -> tuple[str, datetime]:
    """Return (jwt, expires_at). exp = now + ACCESS_TOKEN_TTL_MIN (§3)."""
    now = datetime.now(timezone.utc)
    exp = now + timedelta(minutes=settings.access_token_ttl_min)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "org": str(org_id) if org_id else None,
        "roles": roles,
        "typ": "access",
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
        "jti": uuid.uuid4().hex,
    }
    token = jwt.encode(payload, settings.secret_key, algorithm="HS256")
    return token, exp


def decode_access_token(settings: Settings, token: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
    except jwt.ExpiredSignatureError as exc:
        raise TokenExpired("Access token expired") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenInvalid("Access token is invalid") from exc
    if payload.get("typ") != "access":
        raise TokenInvalid("Not an access token")
    if "sub" not in payload:
        raise TokenInvalid("Access token missing subject")
    return payload


def generate_refresh_token() -> tuple[str, str]:
    """Return (plaintext, sha256_hex64). Plaintext is shown to the client exactly once."""
    plaintext = secrets.token_urlsafe(32)  # 256 bits of entropy
    return plaintext, hash_refresh_token(plaintext)


def hash_refresh_token(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def refresh_expires_at(settings: Settings) -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_ttl_days)
