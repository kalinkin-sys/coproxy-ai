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


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.http_client = httpx.AsyncClient(timeout=120.0)
    logger.info("HTTP client pool created")

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
        # /health returns only status (no sensitive data), skip auth
        if request.url.path == "/health":
            return await call_next(request)

        bearer = request.headers.get("Authorization", "").removeprefix("Bearer ")
        if not bearer or not secrets.compare_digest(bearer, cfg.proxy_secret):
            logger.warning("Unauthorized request from %s", request.client.host)
            return Response("Unauthorized", status_code=401)

        return await call_next(request)


# 10 MB — allows multimodal requests with base64-encoded images, prevents OOM
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
        logger.info("TPM dispatcher enabled: %d tokens/min, %ds timeout",
                     config.tpm_limit, config.tpm_timeout)
    # Order matters: outermost middleware runs first
    # body size → rate limit → auth → handler
    app.add_middleware(AuthMiddleware)
    if config.rate_limit > 0:
        app.add_middleware(RateLimitMiddleware, max_per_minute=config.rate_limit)
    app.add_middleware(BodySizeLimitMiddleware)
