"""JWT token creation and verification."""

import os
from datetime import datetime, timedelta, timezone
from uuid import UUID

import jwt

# Use env var in production, fallback for development
JWT_SECRET = os.environ.get("JWT_SECRET", "phenomics-dev-jwt-secret-key-2026")
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = 7


def create_access_token(
    user_id: UUID,
    username: str,
    role: str,
    expires_delta: timedelta | None = None,
) -> str:
    """Create a JWT access token."""
    if expires_delta is None:
        expires_delta = timedelta(days=ACCESS_TOKEN_EXPIRE_DAYS)

    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "username": username,
        "role": role,
        "iat": now,
        "exp": now + expires_delta,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def verify_token(token: str) -> dict:
    """Verify a JWT token and return the payload.

    Raises jwt.PyJWTError on invalid/expired token.
    """
    return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
