# coproxy-ai

[Русский](docs/README.ru.md) | [Deutsch](docs/README.de.md) | [Español](docs/README.es.md) | [Français](docs/README.fr.md) | [Українська](docs/README.uk.md) | [Ελληνικά](docs/README.el.md) | [中文](docs/README.zh.md) | [日本語](docs/README.ja.md) | [한국어](docs/README.ko.md)

Local proxy that lets you use your **ChatGPT Plus/Pro/Team subscription** as an OpenAI API — no API credits needed.

```
Your app → coproxy (localhost:8765) → api.openai.com
              ↑ uses your ChatGPT OAuth tokens
```

## How it works

1. You log in with your ChatGPT account (one-time device code flow)
2. coproxy obtains and auto-refreshes an API key from your OAuth session
3. Any OpenAI-compatible client can use `http://127.0.0.1:8765/v1` as the base URL

Works with: GPT-4o, GPT-4o-mini, GPT-4.1, o3-mini, and any model available on your subscription.

## Quick start

```bash
git clone https://github.com/kalinkin-sys/coproxy-ai.git
cd coproxy-ai
./setup.sh
```

The setup script will:
1. Create a Python venv and install dependencies
2. Open a device code login (you enter a code at openai.com)
3. Generate a proxy secret (your "API key" for clients)
4. Optionally install a systemd service

**That's it.** Your proxy is running on `http://127.0.0.1:8765/v1`.

## Usage

Point any OpenAI-compatible client at the proxy:

```bash
export OPENAI_BASE_URL=http://127.0.0.1:8765/v1
export OPENAI_API_KEY=<your COPROXY_SECRET from .env>
```

### curl example

```bash
curl http://127.0.0.1:8765/v1/chat/completions \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"Hello!"}]}'
```

### Python (openai SDK)

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8765/v1",
    api_key="<your COPROXY_SECRET>",
)

resp = client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "Hello!"}],
)
print(resp.choices[0].message.content)
```

### Unix socket (shared hosting)

If using `COPROXY_UNIX_SOCKET=~/.coproxy/coproxy.sock`:

```python
import httpx
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost/v1",
    api_key="<your COPROXY_SECRET>",
    http_client=httpx.Client(
        transport=httpx.HTTPTransport(uds="/home/you/.coproxy/coproxy.sock")
    ),
)
```

```bash
curl --unix-socket ~/.coproxy/coproxy.sock \
  http://localhost/v1/chat/completions \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o-mini","messages":[{"role":"user","content":"Hello!"}]}'
```

## Configuration

All config is via environment variables (in `.env` file):

| Variable | Default | Description |
|---|---|---|
| `COPROXY_SECRET` | *required* | Bearer token clients use to authenticate |
| `COPROXY_PORT` | `8765` | Listen port |
| `COPROXY_HOST` | `127.0.0.1` | Listen address (keep localhost!) |
| `COPROXY_AUTH_FILE` | `~/.codex/auth.json` | OAuth token storage |
| `COPROXY_RATE_LIMIT` | `30` | Max requests/minute (0 = unlimited) |
| `COPROXY_LOG_LEVEL` | `info` | Logging level |
| `COPROXY_LOG_REQUESTS` | `false` | Log each proxied request |
| `COPROXY_TPM_LIMIT` | `0` | Token-per-minute budget (0 = unlimited). Enables priority queue |
| `COPROXY_TLS` | `false` | Enable HTTPS with auto-generated self-signed cert |
| `COPROXY_TLS_CERT_DIR` | `~/.coproxy/tls` | Directory for TLS certificate and key |
| `COPROXY_UNIX_SOCKET` | *(disabled)* | Unix socket path (overrides host:port, chmod 600) |

## Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Health check (token TTL requires auth) |
| `GET` | `/v1/models` | List available models |
| `POST` | `/v1/chat/completions` | Chat completions (streaming supported) |
| `GET` | `/v1/stats` | TPM dispatcher statistics (requires auth) |

## TPM Dispatcher (priority queue)

When `COPROXY_TPM_LIMIT` is set, coproxy enforces a sliding 60-second token budget with a priority queue:

- Clients set priority via `X-Priority: high|normal|low` header (default: `normal`)
- Requests are queued when the TPM window is full
- **Greedy best-fit dispatch**: highest-priority requests get budget first
- Token estimation: `len(prompt)/3 + 500 + max_tokens` → settled to actual after OpenAI responds

### `/v1/stats` response

```json
{
  "uptime_seconds": 5653,
  "tpm_limit": 60000,
  "tpm_used": 273,
  "tpm_utilization_pct": 0.5,
  "queue_depth": 0,
  "queue_max_depth": 7,
  "requests": {
    "total": 46,
    "by_priority": {"high": 10, "normal": 23, "low": 13},
    "timeouts": 0
  },
  "tokens": {"total_settled": 81434, "avg_per_request": 1770},
  "wait_time": {"avg": 0.0, "max": 19.37, "p50": 0.0, "p95": 0.0, "p99": 0.0, "samples": 46}
}
```

See [LOADTEST-RESULTS.md](LOADTEST-RESULTS.md) for benchmark data.

## Token lifecycle

- OAuth tokens are stored in `~/.codex/auth.json` **(encrypted at rest)**
- Encryption: Fernet (AES-128-CBC + HMAC-SHA256), key from OS keyring or machine-bound derivation
- Tokens are valid for ~8 days
- coproxy auto-refreshes 5 minutes before expiry
- If tokens expire, re-run: `.venv/bin/coproxy-login`

## Re-login

If your session expires or you need to switch accounts:

```bash
.venv/bin/coproxy-login
sudo systemctl restart coproxy  # if using systemd
```

## Requirements

- Python 3.12+
- ChatGPT Plus, Pro, or Team subscription
- Linux/macOS (systemd optional)

## Security

coproxy is designed to run on your own server with no secrets leaving localhost.

**Network**
- Binds to `127.0.0.1` only — not accessible from the network
- Warning logged if you override `COPROXY_HOST` to a non-loopback address
- Optional **Unix socket** (`COPROXY_UNIX_SOCKET=~/.coproxy/coproxy.sock`) — file permissions `chmod 600`, only socket owner can connect; **best option for shared hosting** where other users share the same `127.0.0.1`
- Optional **TLS/HTTPS** (`COPROXY_TLS=true`) — self-signed ECDSA cert auto-generated on first run; can be combined with Unix socket
- Upstream to OpenAI is always HTTPS (`https://api.openai.com`)
- Swagger / OpenAPI / ReDoc endpoints disabled (`docs_url=None`)

**Authentication & request safety**
- All requests require a Bearer token (`COPROXY_SECRET`)
- Token comparison uses `secrets.compare_digest` (constant-time, no timing attacks)
- Rate limiting: in-memory sliding window (default 30 req/min, configurable)
- Request body size limit: 10 MB (allows multimodal base64 images, prevents OOM/DoS)
- Model allowlist: only `gpt-*`, `o1-*`, `o3-*`, `o4-*`, `chatgpt-*` (prevents subscription abuse)
- `/health` endpoint: token TTL shown only to authenticated clients

**Token storage (encrypted)**
- OAuth tokens are **encrypted at rest** using Fernet (AES-128-CBC + HMAC-SHA256)
- Desktop: encryption key stored in OS keyring (GNOME Keyring, macOS Keychain, etc.)
- Headless server: encryption key derived from `/etc/machine-id` + UID + random salt (PBKDF2, 480K iterations) — **stolen file is useless on another machine/user**
- Legacy plaintext `auth.json` is auto-migrated to encrypted format on first load
- File permissions: `chmod 600`, verified at startup (warns if group/other accessible)
- Atomic writes: `tmp file → os.replace()` — no partial reads on crash
- `asyncio.Lock()` on refresh — no race conditions between concurrent requests
- Optional: `pip install 'coproxy-ai[keyring]'` to enable OS keyring backend

**Logging policy**
- Logged: HTTP method, path, model, status code, latency
- Never logged: prompts, responses, access/refresh/API tokens (not even partially), proxy secret, account IDs
- Error responses are sanitized — no token fragments in exceptions

**Other**
- `--dry-run` flag to validate config and auth without starting the server
- No secrets in config files — everything via environment variables or `.env`
- Graceful shutdown on SIGTERM (uvicorn)
- systemd service template with `NoNewPrivileges`, `PrivateTmp`, `ProtectSystem=strict`

## License

MIT
