"""Request handlers: /health, /v1/models, /v1/chat/completions, /v1/stats."""

from __future__ import annotations

import json
import logging
import os
import secrets
import time

from fastapi import Request
from fastapi.responses import JSONResponse, StreamingResponse

from coproxy.auth import store as auth_store
from coproxy.proxy.server import app, cfg, tpm
from coproxy.proxy.tpm import (
    PRIORITY_HIGH,
    PRIORITY_LOW,
    PRIORITY_NORMAL,
    TPMDispatcher,
)

logger = logging.getLogger(__name__)

OPENAI_BASE = "https://api.openai.com"

_PRIORITY_MAP = {
    "high": PRIORITY_HIGH,
    "normal": PRIORITY_NORMAL,
    "low": PRIORITY_LOW,
}


def _parse_priority(request: Request) -> int:
    """Read X-Priority header (high / normal / low), default normal."""
    raw = request.headers.get("X-Priority", "normal").strip().lower()
    return _PRIORITY_MAP.get(raw, PRIORITY_NORMAL)


@app.get("/health")
async def health(request: Request):
    """Health check. Token TTL shown only to authenticated clients."""
    is_authed = False
    bearer = request.headers.get("Authorization", "").removeprefix("Bearer ")
    if bearer and secrets.compare_digest(bearer, cfg.proxy_secret):
        is_authed = True

    try:
        path = os.path.expanduser(cfg.auth_file)
        data = auth_store.load(path)
        result: dict = {"status": "ok"}
        if is_authed:
            age = auth_store._seconds_since_refresh(data)
            ttl = int(auth_store._DEFAULT_TOKEN_LIFETIME - age)
            result["token_ttl_seconds"] = max(ttl, 0)
            result["token_ttl_hours"] = round(max(ttl, 0) / 3600, 1)
            if tpm is not None:
                result["tpm_available_pct"] = round(tpm.available / tpm.limit * 100)
                result["tpm_queue_depth"] = tpm.queue_depth
        return result
    except Exception:
        return JSONResponse({"status": "error"}, status_code=503)


@app.get("/v1/stats")
async def stats(request: Request):
    """Detailed proxy statistics. Requires authentication."""
    if tpm is None:
        return {"error": "TPM dispatcher not enabled"}
    return tpm.get_stats()


@app.get("/v1/models")
async def models(request: Request):
    """Proxy models list from OpenAI."""
    try:
        token = await auth_store.get_valid_token(cfg)
        client = request.app.state.http_client
        resp = await client.get(
            f"{OPENAI_BASE}/v1/models",
            headers={"Authorization": f"Bearer {token}"},
        )
        return JSONResponse(resp.json(), status_code=resp.status_code)
    except Exception:
        # Fallback: return minimal list
        return {
            "object": "list",
            "data": [
                {"id": "gpt-4o", "object": "model", "owned_by": "openai"},
                {"id": "gpt-4o-mini", "object": "model", "owned_by": "openai"},
            ],
        }


ALLOWED_MODELS_PREFIX = ("gpt-", "o1-", "o3-", "o4-", "chatgpt-")


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    model = body.get("model", "?")

    # Model allowlist: only chat models
    if not any(model.startswith(p) for p in ALLOWED_MODELS_PREFIX):
        return JSONResponse(
            {"error": {"message": f"Model not allowed: {model}", "type": "invalid_request_error"}},
            status_code=400,
        )

    # --- TPM gate: queue with priority, wait for budget ---
    ticket_id: str | None = None
    estimated = 0
    if tpm is not None:
        priority = _parse_priority(request)
        estimated = TPMDispatcher.estimate_total(body, limit=tpm.limit)
        try:
            ticket_id = await tpm.acquire(estimated, priority)
        except TimeoutError:
            logger.warning("TPM timeout for [%s]", model)
            return JSONResponse(
                {
                    "error": {
                        "message": "Rate limit exceeded. Try again later.",
                        "type": "rate_limit_error",
                    }
                },
                status_code=429,
            )

    t0 = time.monotonic()

    try:
        token = await auth_store.get_valid_token(cfg)
    except Exception:
        logger.exception("Failed to get valid token")
        if tpm is not None and ticket_id:
            tpm.settle(ticket_id, 0)  # release reservation
        return JSONResponse(
            {"error": {"message": "Proxy authentication error", "type": "proxy_error"}},
            status_code=502,
        )

    client = request.app.state.http_client

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    is_stream = body.get("stream", False)

    if is_stream:
        # Ask OpenAI to include usage stats in the final SSE event
        body.setdefault("stream_options", {})["include_usage"] = True

        req = client.build_request(
            "POST", f"{OPENAI_BASE}/v1/chat/completions", json=body, headers=headers
        )
        resp = await client.send(req, stream=True)
        elapsed = time.monotonic() - t0
        logger.info(
            "POST /v1/chat/completions [%s] -> %d (%.2fs, streaming)",
            model,
            resp.status_code,
            elapsed,
        )

        async def stream_with_tpm_tracking():
            """Forward SSE chunks and capture usage from the final event."""
            usage_tokens = 0
            buf = ""
            try:
                async for raw in resp.aiter_bytes():
                    # Parse SSE to capture usage (best-effort)
                    if tpm is not None:
                        buf += raw.decode("utf-8", errors="replace")
                        while "\n\n" in buf:
                            event, buf = buf.split("\n\n", 1)
                            for line in event.split("\n"):
                                if line.startswith("data: ") and line != "data: [DONE]":
                                    try:
                                        data = json.loads(line[6:])
                                        usage = data.get("usage")
                                        if usage:
                                            usage_tokens = usage.get(
                                                "total_tokens", 0
                                            )
                                    except (json.JSONDecodeError, AttributeError):
                                        pass
                    yield raw
            finally:
                await resp.aclose()
                if tpm is not None and ticket_id:
                    actual = usage_tokens or estimated
                    tpm.settle(ticket_id, actual)
                    logger.info(
                        "TPM: stream done [%s] tokens=%d%s",
                        model,
                        actual,
                        "" if usage_tokens else " (estimated)",
                    )

        return StreamingResponse(
            stream_with_tpm_tracking(),
            status_code=resp.status_code,
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    # --- Non-streaming ---
    resp = await client.post(
        f"{OPENAI_BASE}/v1/chat/completions", json=body, headers=headers
    )
    elapsed = time.monotonic() - t0
    resp_data = resp.json()

    # Settle: replace reservation with actual usage, re-dispatch queue
    if tpm is not None and ticket_id:
        actual = resp_data.get("usage", {}).get("total_tokens", 0) or estimated
        tpm.settle(ticket_id, actual)
        logger.info("TPM: [%s] tokens=%d (%.2fs)", model, actual, elapsed)
    else:
        logger.info(
            "POST /v1/chat/completions [%s] -> %d (%.2fs)",
            model,
            resp.status_code,
            elapsed,
        )

    return JSONResponse(resp_data, status_code=resp.status_code)
