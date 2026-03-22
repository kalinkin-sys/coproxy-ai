"""FastAPI app: lifespan, auth middleware, rate limiter."""

from __future__ import annotations

import logging
import os
import secrets
import time
from collections import deque
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import httpx
from fastapi import FastAPI, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from coproxy.proxy.tpm import TPMDispatcher

if TYPE_CHECKING:
    from coproxy.config import Config

logger = logging.getLogger(__name__)

# Will be set by main.py before app starts
cfg: Config = None  # type: ignore[assignment]
tpm: TPMDispatcher | None = None


async def _auto_detect_tpm(client: httpx.AsyncClient, token: str) -> int | None:
    """Detect actual TPM limit from OpenAI rate-limit headers.

    OpenAI returns per-model rate-limit headers on every response:
      x-ratelimit-limit-tokens     — org TPM limit for this model
      x-ratelimit-remaining-tokens — tokens remaining in current window

    We probe multiple models and use the MINIMUM detected limit (conservative).
    Each probe costs ~20 tokens.
    """
    OPENAI_BASE = "https://api.openai.com"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # Probe models that are commonly used
    probe_models = ["gpt-4o", "gpt-4o-mini"]
    limits: dict[str, int] = {}

    for model in probe_models:
        body = {
            "model": model,
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 1,
        }
        try:
            resp = await client.post(
                f"{OPENAI_BASE}/v1/chat/completions",
                json=body,
                headers=headers,
                timeout=30.0,
            )

            limit_tokens = resp.headers.get("x-ratelimit-limit-tokens")
            remaining = resp.headers.get("x-ratelimit-remaining-tokens")
            limit_requests = resp.headers.get("x-ratelimit-limit-requests")

            logger.info(
                "TPM auto-detect [%s]: status=%d, limit-tokens=%s, "
                "remaining=%s, limit-requests=%s",
                model, resp.status_code, limit_tokens, remaining, limit_requests,
            )

            if limit_tokens:
                limits[model] = int(limit_tokens)

        except Exception as e:
            logger.warning("TPM auto-detect [%s]: probe failed: %s", model, e)

    if not limits:
        logger.warning("TPM auto-detect: no rate-limit headers from any model")
        return None

    # Use minimum across models (most restrictive)
    min_model = min(limits, key=limits.get)  # type: ignore[arg-type]
    min_limit = limits[min_model]
    logger.info(
        "TPM auto-detect results: %s — using min=%d (%s)",
        ", ".join(f"{m}={v}" for m, v in sorted(limits.items())),
        min_limit,
        min_model,
    )
    return min_limit


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http_client = httpx.AsyncClient(timeout=120.0)
    logger.info("HTTP client pool created")

    # Initialize exchange logging
    from coproxy.proxy.handler import _init_exchange_logging
    _init_exchange_logging()

    # Auto-detect TPM limit if configured
    if cfg.tpm_auto_detect and tpm is not None:
        try:
            from coproxy.auth import store as auth_store
            token = await auth_store.get_valid_token(cfg)
            detected = await _auto_detect_tpm(app.state.http_client, token)
            if detected is not None and detected != tpm.limit:
                old = tpm.limit
                tpm.limit = detected
                logger.info("TPM limit updated: %d → %d (auto-detected)", old, detected)
        except Exception as e:
            logger.warning("TPM auto-detect failed: %s — keeping configured limit", e)

    yield
    await app.state.http_client.aclose()
    logger.info("HTTP client pool closed")


app = FastAPI(
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path == "/health":
            return await call_next(request)

        bearer = request.headers.get("Authorization", "").removeprefix("Bearer ")
        if not bearer or not secrets.compare_digest(bearer, cfg.proxy_secret):
            logger.warning("Unauthorized request from %s", request.client.host)
            return Response("Unauthorized", status_code=401)

        return await call_next(request)


MAX_BODY_SIZE = 10 * 1024 * 1024


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method in ("POST", "PUT", "PATCH"):
            content_length = request.headers.get("content-length")
            if not content_length:
                return Response("Content-Length required", status_code=411)
            if int(content_length) > MAX_BODY_SIZE:
                return Response("Request body too large", status_code=413)
        return await call_next(request)


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, max_per_minute: int):
        super().__init__(app)
        self.timestamps: deque[float] = deque()
        self.max = max_per_minute

    async def dispatch(self, request: Request, call_next):
        if self.max <= 0 or request.url.path == "/health":
            return await call_next(request)

        now = time.monotonic()
        while self.timestamps and self.timestamps[0] < now - 60:
            self.timestamps.popleft()

        if len(self.timestamps) >= self.max:
            logger.warning("Rate limit exceeded from %s", request.client.host)
            return Response("Rate limit exceeded", status_code=429)

        self.timestamps.append(now)
        return await call_next(request)


def configure(config: Config) -> None:
    """Set config and add middleware. Must be called before app starts."""
    global cfg, tpm
    cfg = config

    if config.tpm_limit > 0:
        tpm = TPMDispatcher(limit=config.tpm_limit, timeout=config.tpm_timeout)
        logger.info("TPM dispatcher enabled: %d tokens/min, %ds timeout", config.tpm_limit, config.tpm_timeout)
        if config.tpm_aggressive:
            logger.info("TPM aggressive mode: ON — will try direct send before queuing")
        if config.tpm_auto_detect:
            logger.info("TPM auto-detect: ON — will probe actual limit at startup")

    if config.log_exchanges:
        logger.warning(
            "⚠️  EXCHANGE LOGGING ENABLED — full request/response bodies will be saved. "
            "NOT SAFE for production!"
        )

    app.add_middleware(AuthMiddleware)
    if config.rate_limit > 0:
        app.add_middleware(RateLimitMiddleware, max_per_minute=config.rate_limit)
    app.add_middleware(BodySizeLimitMiddleware)
