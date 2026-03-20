"""Auth token storage: encrypted read/write of auth data.

File format:
  - Encrypted: Fernet ciphertext (binary, starts with gAAAAA)
  - Legacy plaintext: JSON (auto-migrated to encrypted on first load)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_refresh_lock = asyncio.Lock()

# Codex CLI refreshes every ~8 days. We use refresh_before_seconds from config.
_DEFAULT_TOKEN_LIFETIME = 8 * 24 * 3600  # 8 days in seconds


@dataclass
class AuthData:
    access_token: str
    refresh_token: str
    id_token: str = ""
    api_key: str = ""  # OpenAI API key (exchanged from id_token)
    last_refresh: str = ""  # ISO 8601 timestamp


def _parse_auth_dict(data: dict) -> AuthData:
    """Parse auth dict (from JSON) into AuthData."""
    tokens = data.get("tokens", {})
    return AuthData(
        access_token=tokens.get("access_token", ""),
        refresh_token=tokens.get("refresh_token", ""),
        id_token=tokens.get("id_token", ""),
        api_key=data.get("api_key", ""),
        last_refresh=data.get("last_refresh", ""),
    )


def _to_dict(data: AuthData) -> dict:
    """Serialize AuthData to dict."""
    return {
        "auth_mode": "chatgpt",
        "tokens": {
            "id_token": data.id_token,
            "access_token": data.access_token,
            "refresh_token": data.refresh_token,
        },
        "api_key": data.api_key,
        "last_refresh": data.last_refresh,
    }


def load(path: str) -> AuthData:
    """Read auth file — encrypted or legacy plaintext (auto-migrates)."""
    from .crypto import decrypt_json, is_encrypted

    if is_encrypted(path):
        data = decrypt_json(open(path, "rb").read(), path)
        return _parse_auth_dict(data)

    # Legacy plaintext — load and migrate
    with open(path) as f:
        data = json.load(f)

    auth_data = _parse_auth_dict(data)

    # Auto-migrate: re-save as encrypted
    logger.info("Migrating auth file to encrypted format")
    save(path, auth_data)

    return auth_data


def save(path: str, data: AuthData) -> None:
    """Atomic write auth file in encrypted format."""
    from .crypto import encrypt_json

    auth_dict = _to_dict(data)
    ciphertext = encrypt_json(auth_dict, path)

    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(ciphertext)
    os.replace(tmp, path)
    os.chmod(path, 0o600)
    logger.info("Auth file updated (encrypted), last_refresh=%s", data.last_refresh)


def _seconds_since_refresh(data: AuthData) -> float:
    """How many seconds since the last token refresh."""
    if not data.last_refresh:
        return float("inf")
    try:
        dt = datetime.fromisoformat(data.last_refresh)
        return (datetime.now(timezone.utc) - dt).total_seconds()
    except (ValueError, TypeError):
        return float("inf")


async def get_valid_token(cfg) -> str:
    """Return a valid API key for OpenAI, refreshing if needed."""
    async with _refresh_lock:
        path = os.path.expanduser(cfg.auth_file)
        data = load(path)

        age = _seconds_since_refresh(data)
        ttl = _DEFAULT_TOKEN_LIFETIME - age

        if ttl < cfg.refresh_before_seconds:
            logger.info(
                "Token age=%.0fs, TTL=%.0fs < %ds, refreshing...",
                age,
                ttl,
                cfg.refresh_before_seconds,
            )
            from .refresh import refresh_token

            data = await refresh_token(data)
            save(path, data)

        # If we have an API key, use it (works with api.openai.com)
        if data.api_key:
            return data.api_key

        # No API key yet — exchange id_token for one
        if data.id_token:
            logger.info("No API key cached, exchanging id_token...")
            from .refresh import exchange_id_token

            data.api_key = await exchange_id_token(data)
            if data.api_key:
                save(path, data)
                return data.api_key

        # Fallback to access_token (may not work with api.openai.com)
        logger.warning("No API key available, falling back to access_token")
        return data.access_token
