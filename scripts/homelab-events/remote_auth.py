"""Timestamped HMAC authentication for remote canonical event intake."""

from __future__ import annotations

import hashlib
import hmac
import time


class AuthError(ValueError):
    """The remote request failed authentication or freshness checks."""


def _material(body: bytes, timestamp: int) -> bytes:
    return str(timestamp).encode("ascii") + b"." + body


def _validate_secret(secret: str) -> None:
    if not isinstance(secret, str) or not secret:
        raise AuthError("missing shared secret")


def sign_payload(body: bytes, secret: str, *, timestamp: int) -> str:
    """Return a v1 signature over ``timestamp.body``."""

    if not isinstance(body, bytes):
        raise TypeError("body must be bytes")
    _validate_secret(secret)
    try:
        stamp = int(timestamp)
    except (TypeError, ValueError) as exc:
        raise AuthError("invalid timestamp") from exc
    digest = hmac.new(
        secret.encode("utf-8"), _material(body, stamp), hashlib.sha256
    ).hexdigest()
    return f"v1={digest}"


def verify_payload(
    body: bytes,
    signature: str,
    timestamp: str,
    secret: str,
    *,
    now: float | None = None,
    max_skew: float = 300.0,
) -> bool:
    """Verify signature and freshness; raise ``AuthError`` on any failure."""

    if not isinstance(body, bytes):
        raise AuthError("invalid body")
    _validate_secret(secret)
    if max_skew < 0:
        raise AuthError("invalid freshness window")
    try:
        stamp = int(timestamp)
    except (TypeError, ValueError) as exc:
        raise AuthError("invalid timestamp") from exc
    if now is None:
        now = time.time()
    if abs(float(now) - stamp) > max_skew:
        raise AuthError("stale request")
    if not isinstance(signature, str) or not signature.startswith("v1="):
        raise AuthError("invalid signature")
    supplied = signature[3:]
    if len(supplied) != hashlib.sha256().digest_size * 2:
        raise AuthError("invalid signature")
    try:
        bytes.fromhex(supplied)
    except ValueError as exc:
        raise AuthError("invalid signature") from exc
    expected = sign_payload(body, secret, timestamp=stamp)[3:]
    if not hmac.compare_digest(supplied, expected):
        raise AuthError("invalid signature")
    return True
