"""Short-lived, one-use pre-authentication CSRF nonces."""

import hashlib
import secrets
import threading
from datetime import UTC, datetime, timedelta

_NONCE_TTL = timedelta(minutes=5)
_lock = threading.Lock()
_nonces: dict[bytes, datetime] = {}


def issue_challenge(*, now: datetime | None = None) -> str:
    """Return a nonce while retaining only its digest in process memory."""
    current = now or datetime.now(UTC)
    nonce = secrets.token_urlsafe(32)
    nonce_hash = hashlib.sha256(nonce.encode("ascii")).digest()
    with _lock:
        expired = [digest for digest, expires_at in _nonces.items() if expires_at <= current]
        for digest in expired:
            del _nonces[digest]
        _nonces[nonce_hash] = current + _NONCE_TTL
    return nonce


def consume_challenge(nonce: str, *, now: datetime | None = None) -> bool:
    """Consume a challenge once and reject unknown or expired values."""
    current = now or datetime.now(UTC)
    try:
        nonce_hash = hashlib.sha256(nonce.encode("ascii")).digest()
    except UnicodeEncodeError:
        return False
    with _lock:
        expires_at = _nonces.pop(nonce_hash, None)
    return expires_at is not None and expires_at > current
