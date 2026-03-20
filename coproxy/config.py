"""Configuration loader. Primary: env vars. Fallback: YAML (not recommended)."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class Config:
    host: str = "127.0.0.1"
    port: int = 8765
    proxy_secret: str = ""
    auth_file: str = "~/.codex/auth.json"
    refresh_before_seconds: int = 300
    log_level: str = "info"
    log_requests: bool = False
    rate_limit: int = 30  # requests per minute, 0 = unlimited
    tpm_limit: int = 30_000  # tokens per minute, 0 = disabled
    tpm_timeout: int = 120  # max seconds to wait for TPM budget
    tls: bool = False  # enable HTTPS with self-signed cert
    tls_cert_dir: str = "~/.coproxy/tls"
    unix_socket: str = ""  # path to Unix socket (overrides host:port)


def load(yaml_path: str | None = None) -> Config:
    cfg = Config()

    # 1. YAML fallback
    if yaml_path:
        _load_yaml(cfg, yaml_path)

    # 2. Env vars override everything
    cfg.proxy_secret = os.environ.get("COPROXY_SECRET", cfg.proxy_secret)
    cfg.host = os.environ.get("COPROXY_HOST", cfg.host)
    cfg.port = int(os.environ.get("COPROXY_PORT", str(cfg.port)))
    cfg.auth_file = os.environ.get("COPROXY_AUTH_FILE", cfg.auth_file)
    cfg.refresh_before_seconds = int(
        os.environ.get("COPROXY_REFRESH_BEFORE", str(cfg.refresh_before_seconds))
    )
    cfg.log_level = os.environ.get("COPROXY_LOG_LEVEL", cfg.log_level)
    cfg.log_requests = os.environ.get("COPROXY_LOG_REQUESTS", "").lower() in (
        "true",
        "1",
        "yes",
    )
    cfg.rate_limit = int(os.environ.get("COPROXY_RATE_LIMIT", str(cfg.rate_limit)))
    cfg.tpm_limit = int(os.environ.get("COPROXY_TPM_LIMIT", str(cfg.tpm_limit)))
    cfg.tpm_timeout = int(os.environ.get("COPROXY_TPM_TIMEOUT", str(cfg.tpm_timeout)))
    cfg.tls = os.environ.get("COPROXY_TLS", "").lower() in ("true", "1", "yes")
    cfg.tls_cert_dir = os.environ.get("COPROXY_TLS_CERT_DIR", cfg.tls_cert_dir)
    cfg.unix_socket = os.environ.get("COPROXY_UNIX_SOCKET", cfg.unix_socket)

    # 3. Validate
    _validate(cfg)
    return cfg


def _load_yaml(cfg: Config, path: str) -> None:
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        raise SystemExit(
            "pyyaml not installed. Install: pip install 'coproxy-ai[yaml]'"
        )

    with open(path) as f:
        data = yaml.safe_load(f) or {}

    server = data.get("server", {})
    auth = data.get("auth", {})
    log = data.get("logging", {})

    cfg.host = server.get("host", cfg.host)
    cfg.port = server.get("port", cfg.port)
    cfg.proxy_secret = server.get("proxy_secret", cfg.proxy_secret)
    cfg.rate_limit = server.get("rate_limit", cfg.rate_limit)
    cfg.auth_file = auth.get("auth_file", cfg.auth_file)
    cfg.refresh_before_seconds = auth.get(
        "refresh_before_seconds", cfg.refresh_before_seconds
    )
    cfg.log_level = log.get("level", cfg.log_level)
    cfg.log_requests = log.get("log_requests", cfg.log_requests)


def _validate(cfg: Config) -> None:
    if not cfg.proxy_secret:
        raise SystemExit(
            "COPROXY_SECRET is required.\n"
            'Generate: python -c "import secrets; print(secrets.token_hex(32))"'
        )

    if cfg.unix_socket:
        sock_dir = os.path.dirname(os.path.expanduser(cfg.unix_socket))
        if not os.path.isdir(sock_dir):
            raise SystemExit(f"Unix socket directory does not exist: {sock_dir}")
    elif cfg.host != "127.0.0.1":
        logger.warning("host=%s — proxy is accessible from the network!", cfg.host)

    auth_path = os.path.expanduser(cfg.auth_file)
    if not os.path.exists(auth_path):
        raise SystemExit(f"Auth file not found: {auth_path}")

    mode = os.stat(auth_path).st_mode & 0o077
    if mode != 0:
        actual = os.stat(auth_path).st_mode & 0o777
        logger.warning(
            "auth file %s is group/other accessible (mode %o), recommend chmod 600",
            auth_path,
            actual,
        )
