"""Short-lived download tokens.

A token authorises exactly one file for exactly one user for a short window, so a
file can be handed to a player or an external downloader without ever exposing the
storage path or the long-lived access token.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import time
import uuid

from .config import Settings


def _sign(settings: Settings, payload: str) -> str:
    return hmac.new(settings.jwt_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def issue(settings: Settings, user_id: uuid.UUID, file_id: uuid.UUID) -> tuple[str, int]:
    expires_at = int(time.time()) + settings.download_token_ttl_seconds
    payload = f"{user_id}:{file_id}:{expires_at}"
    raw = f"{payload}:{_sign(settings, payload)}"
    token = base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")
    return token, expires_at


def verify(settings: Settings, token: str, file_id: uuid.UUID) -> uuid.UUID | None:
    try:
        padded = token + "=" * (-len(token) % 4)
        raw = base64.urlsafe_b64decode(padded.encode()).decode()
        user_id, token_file_id, expires_at, signature = raw.rsplit(":", 3)
    except Exception:
        return None
    if token_file_id != str(file_id):
        return None
    payload = f"{user_id}:{token_file_id}:{expires_at}"
    if not hmac.compare_digest(signature, _sign(settings, payload)):
        return None
    try:
        if int(expires_at) < int(time.time()):
            return None
        return uuid.UUID(user_id)
    except ValueError:
        return None
