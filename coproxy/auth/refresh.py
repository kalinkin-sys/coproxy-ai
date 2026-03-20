"""OAuth token refresh and API key exchange."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx

from .store import AuthData

logger = logging.getLogger(__name__)

TOKEN_URL = "https://auth.openai.com/oauth/token"
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"


async def refresh_token(data: AuthData) -> AuthData:
    """Exchange refresh_token for new tokens, then get API key."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        # Step 1: Refresh OAuth tokens
        resp = await client.post(
            TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "client_id": CLIENT_ID,
                "refresh_token": data.refresh_token,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if resp.status_code != 200:
            logger.error("Token refresh failed: HTTP %d", resp.status_code)
            raise RuntimeError(f"Token refresh failed: HTTP {resp.status_code}")
        body = resp.json()

        id_token = body.get("id_token", data.id_token)
        refresh_token_new = body.get("refresh_token", data.refresh_token)

        # Step 2: Exchange id_token for API key
        api_key = await _exchange_for_api_key(client, id_token)

    now = datetime.now(timezone.utc).isoformat()

    new_data = AuthData(
        access_token=body.get("access_token", data.access_token),
        refresh_token=refresh_token_new,
        id_token=id_token,
        api_key=api_key,
        last_refresh=now,
    )
    logger.info("Token refreshed at %s, api_key obtained: %s", now, bool(api_key))
    return new_data


async def exchange_id_token(data: AuthData) -> str:
    """Exchange id_token for API key (without full refresh)."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        return await _exchange_for_api_key(client, data.id_token)


async def _exchange_for_api_key(client: httpx.AsyncClient, id_token: str) -> str:
    """Token exchange: id_token -> OpenAI API key."""
    if not id_token:
        logger.warning("No id_token available for API key exchange")
        return ""

    resp = await client.post(
        TOKEN_URL,
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
            "client_id": CLIENT_ID,
            "requested_token": "openai-api-key",
            "subject_token": id_token,
            "subject_token_type": "urn:ietf:params:oauth:token-type:id_token",
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    if resp.status_code != 200:
        logger.error("API key exchange failed: HTTP %d", resp.status_code)
        return ""

    body = resp.json()
    api_key = body.get("access_token", "")
    logger.info("API key obtained via token exchange")
    return api_key
