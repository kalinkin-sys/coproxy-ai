"""Request handlers: /health, /v1/models, /v1/chat/completions, /v1/stats."""

from __future__ import annotations

import json
import logging
import os
import secrets
import time
from pathlib import Path

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

# ── Exchange logging (debug only) ───────────────────────────────────
LOG_DIR: Path | None = None
_seq = 0
_LOG_WARNING_INTERVAL = 300  # warn every 5 min
_last_log_warning: float = 0


def _init_exchange_logging():
    global LOG_DIR
    if not cfg.log_exchanges:
        return
    LOG_DIR = Path.home() / ".coproxy-logs" / "exchanges"
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    _emit_log_security_warning()


def _emit_log_security_warning():
    """Emit security warning about exchange logging."""
    msg = (
        "⚠️  EXCHANGE LOGGING IS ENABLED (COPROXY_LOG_EXCHANGES=true). "
        "Full request/response bodies are saved to disk INCLUDING TOKENS AND CONTENT. "
        "This is NOT SAFE for production — use ONLY for debugging. "
        "Disable with COPROXY_LOG_EXCHANGES=false"
    )
    logger.warning(msg)
    # Also write to the log directory itself
    if LOG_DIR:
        (LOG_DIR / "WARNING_NOT_FOR_PRODUCTION.txt").write_text(
            "⚠️  EXCHANGE LOGGING IS ACTIVE\n\n"
            "Full request and response bodies (including all message content) "
            "are being saved to this directory.\n\n"
            "This is NOT safe for production use.\n"
            "Disable: set COPROXY_LOG_EXCHANGES=false and restart.\n"
        )


def _maybe_warn_logging():
    """Periodically re-emit the logging warning."""
    global _last_log_warning
    now = time.monotonic()
    if now - _last_log_warning > _LOG_WARNING_INTERVAL:
        _last_log_warning = now
        logger.warning(
            "⚠️  Exchange logging is ACTIVE — not safe for production. "
            "Disable: COPROXY_LOG_EXCHANGES=false"
        )


def _next_prefix(model: str) -> str:
    global _seq
    _seq += 1
    ts = time.strftime("%Y%m%d_%H%M%S")
    return f"{ts}_{_seq:04d}_{model}"


def _log_exchange(prefix: str, body: dict, resp_data: dict | None, elapsed: float):
    """Save request and response to paired files."""
    if LOG_DIR is None:
        return
    _maybe_warn_logging()
    try:
        req_path = LOG_DIR / f"{prefix}_req.json"
        with open(req_path, "w") as f:
            json.dump(body, f, ensure_ascii=False, indent=2)

        resp_path = LOG_DIR / f"{prefix}_resp.json"
        with open(resp_path, "w") as f:
            if resp_data is not None:
                json.dump(resp_data, f, ensure_ascii=False, indent=2)

        meta_path = LOG_DIR / f"{prefix}_meta.txt"
        msgs = body.get("messages", [])
        usage = (resp_data or {}).get("usage", {})
        inp_tok = usage.get("prompt_tokens", "?")
        out_tok = usage.get("completion_tokens", "?")

        choices = (resp_data or {}).get("choices", [])
        summary = ""
        if choices:
            msg = choices[0].get("message", {})
            content = msg.get("content", "")
            tool_calls = msg.get("tool_calls", [])
            if tool_calls:
                tc_names = [tc.get("function", {}).get("name", "?") for tc in tool_calls]
                summary = f"tool_calls: {', '.join(tc_names)}"
            elif content:
                summary = content[:200].replace("\n", " ")

        with open(meta_path, "w") as f:
            f.write(f"elapsed: {elapsed:.2f}s\n")
            f.write(f"messages: {len(msgs)}\n")
            f.write(f"tokens: {inp_tok} in / {out_tok} out\n")
            f.write(f"response: {summary}\n")

        logger.info("Logged exchange to %s (%.2fs)", prefix, elapsed)
    except Exception as e:
        logger.warning("Failed to log exchange: %s", e)

    _cleanup_old_logs()


def _log_stream_exchange(prefix: str, body: dict, chunks: list[str], elapsed: float):
    """Reassemble streaming response and log as exchange."""
    if LOG_DIR is None:
        return
    try:
        content_parts = []
        tool_calls_map: dict[int, dict] = {}
        usage_data = {}

        for chunk_str in chunks:
            try:
                chunk = json.loads(chunk_str)
            except json.JSONDecodeError:
                continue
            usage = chunk.get("usage")
            if usage:
                usage_data = usage
            for choice in chunk.get("choices", []):
                delta = choice.get("delta", {})
                if delta.get("content"):
                    content_parts.append(delta["content"])
                for tc in delta.get("tool_calls", []):
                    idx = tc.get("index", 0)
                    if idx not in tool_calls_map:
                        tool_calls_map[idx] = {
                            "id": "", "type": "function",
                            "function": {"name": "", "arguments": ""},
                        }
                    entry = tool_calls_map[idx]
                    if tc.get("id"):
                        entry["id"] = tc["id"]
                    fn = tc.get("function", {})
                    if fn.get("name"):
                        entry["function"]["name"] = fn["name"]
                    if fn.get("arguments"):
                        entry["function"]["arguments"] += fn["arguments"]

        message: dict = {"role": "assistant"}
        if content_parts:
            message["content"] = "".join(content_parts)
        if tool_calls_map:
            message["tool_calls"] = [tool_calls_map[i] for i in sorted(tool_calls_map)]

        resp_data = {
            "choices": [{"message": message, "finish_reason": "stop"}],
            "usage": usage_data,
            "_stream_reassembled": True,
            "_chunks_count": len(chunks),
        }
        _log_exchange(prefix, body, resp_data, elapsed)
    except Exception as e:
        logger.warning("Failed to log stream exchange: %s", e)


MAX_EXCHANGES = 200


def _cleanup_old_logs():
    if LOG_DIR is None:
        return
    try:
        meta_files = sorted(LOG_DIR.glob("*_meta.txt"))
        if len(meta_files) <= MAX_EXCHANGES:
            return
        to_remove = meta_files[:-MAX_EXCHANGES]
        for meta in to_remove:
            prefix = meta.name.rsplit("_meta.txt", 1)[0]
            for suffix in ("_req.json", "_resp.json", "_meta.txt"):
                p = LOG_DIR / f"{prefix}{suffix}"
                p.unlink(missing_ok=True)
    except Exception:
        pass


# ── Aggressive mode helpers ─────────────────────────────────────────

async def _send_to_openai(client, body: dict, headers: dict, is_stream: bool):
    """Send request to OpenAI, return (response, is_rate_limited)."""
    if is_stream:
        req = client.build_request(
            "POST", f"{OPENAI_BASE}/v1/chat/completions", json=body, headers=headers
        )
        resp = await client.send(req, stream=True)
        if resp.status_code == 429:
            await resp.aclose()
            return None, True
        return resp, False
    else:
        resp = await client.post(
            f"{OPENAI_BASE}/v1/chat/completions", json=body, headers=headers
        )
        if resp.status_code == 429:
            return None, True
        return resp, False


OPENAI_BASE = "https://api.openai.com"

_PRIORITY_MAP = {
    "high": PRIORITY_HIGH,
    "normal": PRIORITY_NORMAL,
    "low": PRIORITY_LOW,
}


def _parse_priority(request: Request) -> int:
    raw = request.headers.get("X-Priority", "normal").strip().lower()
    return _PRIORITY_MAP.get(raw, PRIORITY_NORMAL)


@app.get("/health")
async def health(request: Request):
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
            result["aggressive_mode"] = cfg.tpm_aggressive
            result["exchange_logging"] = cfg.log_exchanges
        return result
    except Exception:
        return JSONResponse({"status": "error"}, status_code=503)


@app.get("/v1/stats")
async def stats(request: Request):
    if tpm is None:
        return {"error": "TPM dispatcher not enabled"}
    s = tpm.get_stats()
    s["aggressive_mode"] = cfg.tpm_aggressive
    s["exchange_logging"] = cfg.log_exchanges
    return s


@app.get("/v1/models")
async def models(request: Request):
    try:
        token = await auth_store.get_valid_token(cfg)
        client = request.app.state.http_client
        resp = await client.get(
            f"{OPENAI_BASE}/v1/models",
            headers={"Authorization": f"Bearer {token}"},
        )
        return JSONResponse(resp.json(), status_code=resp.status_code)
    except Exception:
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

    if not any(model.startswith(p) for p in ALLOWED_MODELS_PREFIX):
        return JSONResponse(
            {"error": {"message": f"Model not allowed: {model}", "type": "invalid_request_error"}},
            status_code=400,
        )

    t0 = time.monotonic()
    prefix = _next_prefix(model)
    is_stream = body.get("stream", False)

    # Get auth token early
    try:
        token = await auth_store.get_valid_token(cfg)
    except Exception:
        logger.exception("Failed to get valid token")
        return JSONResponse(
            {"error": {"message": "Proxy authentication error", "type": "proxy_error"}},
            status_code=502,
        )

    client = request.app.state.http_client
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    # ── Aggressive mode: try immediately, fall back to queue on 429 ──
    if cfg.tpm_aggressive and tpm is not None:
        resp, rate_limited = await _send_to_openai(client, body, headers, is_stream)
        if not rate_limited and resp is not None:
            elapsed = time.monotonic() - t0
            logger.info("Aggressive: direct hit [%s] (%.2fs)", model, elapsed)
            tpm.record_direct(TPMDispatcher.estimate_total(body, limit=tpm.limit))
            if is_stream:
                return _stream_response(resp, body, model, prefix, t0, ticket_id=None)
            else:
                resp_data = resp.json()
                actual = resp_data.get("usage", {}).get("total_tokens", 0)
                if actual:
                    tpm.record_direct(actual - TPMDispatcher.estimate_total(body, limit=tpm.limit))
                _log_exchange(prefix, body, resp_data, elapsed)
                return JSONResponse(resp_data, status_code=resp.status_code)

        # Rate limited — fall through to normal queue path
        logger.info("Aggressive: got 429, falling back to queue [%s]", model)

    # ── Normal TPM gate ──────────────────────────────────────────────
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
                {"error": {"message": "Rate limit exceeded. Try again later.", "type": "rate_limit_error"}},
                status_code=429,
            )

    if is_stream:
        body.setdefault("stream_options", {})["include_usage"] = True
        req = client.build_request(
            "POST", f"{OPENAI_BASE}/v1/chat/completions", json=body, headers=headers
        )
        resp = await client.send(req, stream=True)
        elapsed_first = time.monotonic() - t0
        logger.info("POST /v1/chat/completions [%s] -> %d (%.2fs, streaming)", model, resp.status_code, elapsed_first)
        return _stream_response(resp, body, model, prefix, t0, ticket_id)

    # --- Non-streaming ---
    resp = await client.post(
        f"{OPENAI_BASE}/v1/chat/completions", json=body, headers=headers
    )
    elapsed = time.monotonic() - t0
    resp_data = resp.json()

    if tpm is not None and ticket_id:
        actual = resp_data.get("usage", {}).get("total_tokens", 0) or estimated
        tpm.settle(ticket_id, actual)
        logger.info("TPM: [%s] tokens=%d (%.2fs)", model, actual, elapsed)
    else:
        logger.info("POST /v1/chat/completions [%s] -> %d (%.2fs)", model, resp.status_code, elapsed)

    _log_exchange(prefix, body, resp_data, elapsed)
    return JSONResponse(resp_data, status_code=resp.status_code)


def _stream_response(resp, body, model, prefix, t0, ticket_id):
    """Wrap a streaming response with TPM tracking and exchange logging."""
    estimated = TPMDispatcher.estimate_total(body, limit=tpm.limit) if tpm else 0

    async def stream_with_tracking():
        usage_tokens = 0
        buf = ""
        collected_chunks = []
        try:
            async for raw in resp.aiter_bytes():
                decoded = raw.decode("utf-8", errors="replace")
                buf += decoded
                while "\n\n" in buf:
                    event, buf = buf.split("\n\n", 1)
                    for line in event.split("\n"):
                        if line.startswith("data: ") and line != "data: [DONE]":
                            try:
                                data = json.loads(line[6:])
                                collected_chunks.append(line[6:])
                                usage = data.get("usage")
                                if usage:
                                    usage_tokens = usage.get("total_tokens", 0)
                            except (json.JSONDecodeError, AttributeError):
                                pass
                yield raw
        finally:
            await resp.aclose()
            elapsed = time.monotonic() - t0
            if tpm is not None and ticket_id:
                actual = usage_tokens or estimated
                tpm.settle(ticket_id, actual)
                logger.info("TPM: stream done [%s] tokens=%d%s", model, actual, "" if usage_tokens else " (estimated)")
            _log_stream_exchange(prefix, body, collected_chunks, elapsed)

    return StreamingResponse(
        stream_with_tracking(),
        status_code=resp.status_code,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


ALLOWED_EMBEDDING_MODELS = ("text-embedding-3-small", "text-embedding-3-large", "text-embedding-ada-002")


@app.post("/v1/embeddings")
async def embeddings(request: Request):
    body = await request.json()
    model = body.get("model", "?")

    if model not in ALLOWED_EMBEDDING_MODELS:
        return JSONResponse(
            {"error": {"message": f"Embedding model not allowed: {model}", "type": "invalid_request_error"}},
            status_code=400,
        )

    try:
        token = await auth_store.get_valid_token(cfg)
    except Exception:
        logger.exception("Failed to get valid token for embeddings")
        return JSONResponse(
            {"error": {"message": "Proxy authentication error", "type": "proxy_error"}},
            status_code=502,
        )

    client = request.app.state.http_client
    t0 = time.monotonic()
    resp = await client.post(
        f"{OPENAI_BASE}/v1/embeddings",
        json=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    elapsed = time.monotonic() - t0
    logger.info("POST /v1/embeddings [%s] -> %d (%.2fs)", model, resp.status_code, elapsed)
    return JSONResponse(resp.json(), status_code=resp.status_code)
