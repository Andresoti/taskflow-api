"""Password hashing and access-token helpers independent of database settings."""

from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from pwdlib import PasswordHash


_password_hash = PasswordHash.recommended()
_JWT_ALGORITHM = "HS256"


def _validate_secret_key(secret_key: str) -> None:
    """Reject undersized HS256 keys instead of relying on library warnings."""
    if len(secret_key.encode("utf-8")) < 32:
        raise ValueError("HS256 requires a secret key of at least 32 bytes")


def hash_password(password: str) -> str:
    """Return an Argon2 hash for storage instead of the plaintext password."""
    return _password_hash.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Check a candidate password against its stored hash."""
    return _password_hash.verify(password, password_hash)


def create_access_token(
    subject: str,
    *,
    secret_key: str,
    expires_delta: timedelta = timedelta(minutes=15),
) -> str:
    """Sign an expiring token; the caller supplies its secret from configuration."""
    _validate_secret_key(secret_key)
    if not subject.strip():
        raise ValueError("Token subject must not be blank")
    if expires_delta <= timedelta(0):
        raise ValueError("Token lifetime must be positive")
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {"sub": subject, "iat": now, "exp": now + expires_delta},
        secret_key,
        algorithm=_JWT_ALGORITHM,
    )


def decode_access_token(token: str, *, secret_key: str) -> dict[str, Any]:
    """Verify the signature and expiration, raising PyJWT errors on invalid tokens."""
    _validate_secret_key(secret_key)
    # Pin the algorithm in code, not from the untrusted token header.
    return jwt.decode(
        token,
        secret_key,
        algorithms=[_JWT_ALGORITHM],
        options={"require": ["sub", "iat", "exp"]},
    )
