"""CLI entry point for coproxy-ai."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path


def _load_dotenv() -> None:
    """Load .env file from project root if it exists."""
    env_file = Path(__file__).resolve().parent.parent / ".env"
    if not env_file.exists():
        return
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            # Strip surrounding quotes: KEY="value" or KEY='value'
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            if key and key not in os.environ:
                os.environ[key] = value


def cli() -> None:
    _load_dotenv()

    parser = argparse.ArgumentParser(
        description="coproxy — local OAuth proxy for OpenAI-compatible requests "
        "using ChatGPT OAuth tokens"
    )
    parser.add_argument(
        "--config",
        metavar="PATH",
        help="Path to YAML config file (not recommended, use env vars instead)",
    )
    parser.add_argument("--port", type=int, help="Override port (default: 8765)")
    parser.add_argument("--log-level", help="Override log level (default: info)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate config and auth file, then exit",
    )
    args = parser.parse_args()

    # Load config
    from coproxy.config import load

    cfg = load(yaml_path=args.config)

    if args.port:
        cfg.port = args.port
    if args.log_level:
        cfg.log_level = args.log_level

    # Setup logging
    logging.basicConfig(
        level=cfg.log_level.upper(),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    logger = logging.getLogger("coproxy")

    if args.dry_run:
        from coproxy.auth.store import (
            _DEFAULT_TOKEN_LIFETIME,
            _seconds_since_refresh,
            load as load_auth,
        )

        auth_path = os.path.expanduser(cfg.auth_file)
        data = load_auth(auth_path)
        age = _seconds_since_refresh(data)
        ttl = _DEFAULT_TOKEN_LIFETIME - age
        logger.info("Config OK")
        logger.info("Auth file: %s", auth_path)
        logger.info("Last refresh: %s", data.last_refresh or "unknown")
        logger.info("Token TTL: %.0fs (%.1f hours)", ttl, ttl / 3600)
        if cfg.unix_socket:
            logger.info("Listening would be on unix:%s",
                         os.path.expanduser(cfg.unix_socket))
        else:
            logger.info("Listening would be on %s://%s:%d",
                         "https" if cfg.tls else "http", cfg.host, cfg.port)
        logger.info("TLS: %s", "enabled" if cfg.tls else "disabled")
        logger.info("Unix socket: %s", cfg.unix_socket or "disabled")
        logger.info("Rate limit: %d req/min", cfg.rate_limit)
        sys.exit(0)

    # Configure and import app (imports handler.py which registers routes)
    from coproxy.proxy.server import app, configure

    configure(cfg)

    # Force import to register routes
    import coproxy.proxy.handler  # noqa: F401

    import uvicorn

    uvicorn_kwargs: dict = {
        "log_level": cfg.log_level,
    }

    if cfg.unix_socket:
        sock_path = os.path.expanduser(cfg.unix_socket)
        uvicorn_kwargs["uds"] = sock_path
        listen_info = f"unix:{sock_path}"
    else:
        uvicorn_kwargs["host"] = cfg.host
        uvicorn_kwargs["port"] = cfg.port
        listen_info = f"{cfg.host}:{cfg.port}"

    if cfg.tls:
        from coproxy.tls import ensure_cert

        cert_path, key_path = ensure_cert(cfg.tls_cert_dir)
        uvicorn_kwargs["ssl_certfile"] = cert_path
        uvicorn_kwargs["ssl_keyfile"] = key_path
        proto = "https"
    else:
        proto = "http"

    logger.info("Starting coproxy on %s://%s", proto, listen_info)
    uvicorn.run(app, **uvicorn_kwargs)


if __name__ == "__main__":
    cli()
