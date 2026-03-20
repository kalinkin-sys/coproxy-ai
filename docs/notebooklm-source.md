# coproxy-ai: Security Architecture & Technical Deep Dive

## What is coproxy-ai?

coproxy-ai is an open-source local proxy that allows developers to use their ChatGPT Plus, Pro, or Team subscription as an OpenAI-compatible API — without spending any API credits. It runs entirely on your own machine, handles OAuth token management automatically, and exposes a standard `/v1/chat/completions` endpoint that any OpenAI-compatible client can use.

The basic flow is simple: your application sends requests to coproxy on localhost, and coproxy forwards them to api.openai.com using OAuth tokens obtained from your ChatGPT subscription.

## Why does this exist?

Many developers already pay for ChatGPT Plus ($20/month) or Pro ($200/month) but also need API access for their tools, scripts, and local development. Instead of paying twice — once for the subscription and again for API credits — coproxy bridges the gap by reusing your existing subscription tokens.

This is particularly useful for:
- Local development and testing with GPT-4o, o3-mini, and other models
- Running AI-powered CLI tools like OpenAI Codex CLI without API costs
- Small teams that want to share a Team subscription across dev tools
- Prototyping applications before committing to API pricing

## How the OAuth flow works

When you first set up coproxy, it guides you through a one-time device code login — the same kind of flow used by streaming services and smart TVs:

1. coproxy generates a short code (like "ABCD-1234")
2. You visit openai.com/deviceauth and enter the code
3. You log in with your ChatGPT credentials
4. coproxy receives OAuth tokens (access token, refresh token, ID token)
5. It exchanges the ID token for an API key via OpenAI's token exchange endpoint
6. All tokens are encrypted and saved locally

After this, coproxy handles everything automatically. Tokens are valid for about 8 days, and coproxy auto-refreshes them 5 minutes before expiry. You never need to think about authentication again unless you switch accounts.

## Security architecture — the core of the project

Security was the primary design concern because coproxy handles sensitive OAuth credentials. Here's how every layer is protected:

### Token encryption at rest

OAuth tokens are never stored in plaintext. coproxy uses Fernet encryption (AES-128-CBC + HMAC-SHA256) with a two-tier key management strategy:

On desktop systems with a graphical environment, the encryption key is stored in the OS keyring — GNOME Keyring on Linux, macOS Keychain on Mac. This means the key is protected by the operating system's credential store.

On headless servers without a keyring, coproxy derives the encryption key from three components: the machine's unique ID (from /etc/machine-id), the Unix user ID, and a random 32-byte salt. These are combined using PBKDF2-HMAC-SHA256 with 480,000 iterations. The result: even if someone copies the encrypted auth.json file to another machine or another user account, it's completely useless — the derived key will be different.

The salt is stored alongside the auth file with permissions 600 (owner-only). The auth file itself also gets 600 permissions, and coproxy warns at startup if the permissions are too open.

### Network security — localhost by default

coproxy binds exclusively to 127.0.0.1 (the loopback interface). It is literally impossible to reach from the network. If you override this to 0.0.0.0, coproxy logs a prominent warning.

For shared hosting environments where multiple users share the same localhost, coproxy supports Unix domain sockets. The socket file is created with chmod 600, meaning only the socket owner can connect. This is the most secure option for multi-user systems.

Optional TLS with auto-generated ECDSA P-256 certificates adds encryption even on the loopback interface — defense in depth for containerized or multi-tenant environments.

### Request authentication

Every request to coproxy (except the health check endpoint) requires a Bearer token — the COPROXY_SECRET. This is a 32-byte hex string (128 bits of entropy) generated during setup. Token comparison uses Python's secrets.compare_digest function, which is constant-time and immune to timing attacks.

### Rate limiting and token budget

coproxy includes two layers of request throttling:

1. A sliding-window rate limiter (default: 30 requests per minute)
2. A Token-Per-Minute (TPM) dispatcher with a priority queue system

The TPM dispatcher deserves special attention — it's one of the most interesting components of coproxy.

## The TPM Dispatcher: intelligent request scheduling

OpenAI enforces token-per-minute limits on API usage. If multiple applications share the same proxy — say, a coding assistant, a cron job, and a background batch processor — they can easily exceed these limits and get throttled. The TPM dispatcher solves this by acting as an intelligent traffic controller.

### How it works internally

The dispatcher maintains a sliding 60-second window of token usage. Think of it like a water tank that drains at a constant rate: tokens flow in with each request and "expire" after 60 seconds. The budget is the total capacity of this tank.

When a request arrives, coproxy estimates how many tokens it will consume. The estimation formula is straightforward: take the prompt length, divide by 3 (roughly 3 characters per token), add 500 for overhead, and add the max_tokens parameter. This gives a conservative upper bound. After OpenAI responds, the estimate is replaced with the actual token count — this is called "settling" the reservation.

If there's enough budget available, the request goes through immediately. If the budget is full, the request enters a priority queue and waits.

### The priority system

Clients tag their requests with an X-Priority header. There are three levels:

- **High priority (0)** — for live user interactions. When a user is typing in a chat and waiting for a response, they shouldn't be stuck behind a batch job. High-priority requests always get served first.
- **Normal priority (1)** — the default. Used for cron jobs, automated workflows, and general-purpose requests.
- **Low priority (2)** — for background tasks, batch processing, and anything that isn't time-sensitive. These requests only get budget when nothing more important is waiting.

### Greedy best-fit dispatch

When budget frees up, the dispatcher doesn't just serve the next request in line. Instead, it uses a greedy best-fit algorithm: it looks at all waiting requests, finds those that fit within the available budget, and picks the one with the highest priority and longest wait time. Then it loops — maybe another smaller request also fits. This maximizes throughput by packing requests efficiently into the budget window.

This is similar to how operating system schedulers work: high-priority processes get CPU time first, but low-priority work still eventually runs when the system is idle.

### Automatic retry scheduling

When no requests fit the current budget, the dispatcher doesn't poll. Instead, it calculates exactly when the oldest record in the sliding window will expire (freeing budget) and sets a timer. When the timer fires, it re-runs dispatch. This is CPU-efficient — no busy-waiting, no polling loops.

### Metrics and observability

The /v1/stats endpoint exposes rich metrics about the dispatcher: current utilization percentage, queue depth, request counts by priority, timeout counts, total tokens processed, average tokens per request, and wait time percentiles (p50, p95, p99). This makes it easy to tune the TPM limit and understand how the proxy is performing.

For example, if you see high p95 wait times for normal-priority requests but low utilization, it might mean your TPM limit is set too conservatively. If you see timeouts, the limit might be too aggressive for your workload.

### Settlement and budget correction

After OpenAI responds, coproxy knows the exact token count. It "settles" the reservation — replacing the estimate with the actual value. If the estimate was too high (which is common, since estimates are conservative), this frees up budget immediately and triggers re-dispatch of queued requests. This self-correcting behavior means the system naturally becomes more efficient over time within each 60-second window.

### Timeout protection

If a request waits too long in the queue (default: 120 seconds), it's cancelled with a TimeoutError and the client receives a 429 status code. This prevents requests from hanging indefinitely when the system is overloaded. The timeout is configurable via COPROXY_TPM_TIMEOUT.

This prevents accidentally hitting OpenAI's rate limits and allows fair sharing when multiple applications use the same proxy.

### What never gets logged

coproxy has a strict logging policy. It logs operational data (HTTP method, path, model name, status code, latency) but never logs: prompts, responses, any form of tokens or credentials (not even partial fragments), the proxy secret, or account identifiers. Error responses are sanitized before being sent to clients.

### Systemd hardening

The included systemd service template enables multiple Linux security features:
- NoNewPrivileges: the process cannot gain elevated privileges
- PrivateTmp: isolated temporary directory
- ProtectSystem=strict: system directories are read-only
- ReadWritePaths: only the auth and TLS directories are writable

### Model allowlist

To prevent subscription abuse, coproxy only allows models matching: gpt-*, o1-*, o3-*, o4-*, chatgpt-*. Any other model is rejected with a 400 error.

## Performance

Load testing with 20 concurrent requests showed:
- Average latency: ~1.5 seconds (dominated by OpenAI's response time)
- TPM dispatcher overhead: negligible
- No request drops under normal load
- Priority queue correctly serves high-priority requests first when budget is constrained

## How to deploy

Deployment is a single command:

```
git clone https://github.com/youruser/coproxy-ai.git
cd coproxy-ai
./setup.sh
```

The setup script creates a Python virtual environment, guides you through login, generates a secure proxy secret, and optionally installs a systemd service. The entire process takes about 2 minutes.

Requirements are minimal: Python 3.12+ and a ChatGPT subscription. No Docker, no databases, no external services.

## Comparison with alternatives

Unlike cloud-hosted proxy services, coproxy runs entirely on your own hardware. No tokens leave your machine except to OpenAI. There's no third-party service to trust, no monthly proxy fees, and no risk of a proxy service being compromised.

Unlike manually copying tokens from browser DevTools, coproxy handles the complete OAuth lifecycle: login, token exchange, refresh, encryption, and rotation. It's the difference between a one-time hack and a production-ready solution.

## Technical stack

- Python 3.12+ with FastAPI and uvicorn (ASGI)
- httpx for async HTTP proxying with connection pooling
- cryptography library for Fernet encryption and TLS certificate generation
- Zero external services — everything runs locally

## OWASP Top 10 compliance

The project was audited against the OWASP Top 10 (2021):

- A01 Broken Access Control: Mitigated — Bearer token with constant-time comparison
- A02 Cryptographic Failures: Mitigated — Fernet encryption, HTTPS upstream
- A03 Injection: Mitigated — No eval/exec, JSON parsing only, model allowlist
- A04 Insecure Design: Mitigated — Documented threat model
- A05 Security Misconfiguration: Mitigated — Config validation, loopback warnings
- A06 Vulnerable Components: Monitored — All dependencies actively maintained
- A07 Authentication Failures: Mitigated — OAuth 2.0 with auto-refresh
- A08 Data Integrity Failures: Mitigated — Fernet includes HMAC-SHA256
- A09 Logging Failures: Mitigated — No secrets ever logged
- A10 SSRF: Mitigated — Upstream URL hardcoded, no user input in URLs

## Open source and MIT licensed

coproxy-ai is fully open source under the MIT license. Contributions are welcome.
