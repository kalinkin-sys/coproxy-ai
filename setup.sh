#!/usr/bin/env bash
set -euo pipefail

# ─── coproxy-ai setup ───────────────────────────────────────────────
# One command: ./setup.sh
# What it does:
#   1. Creates Python venv and installs dependencies
#   2. Logs in to your ChatGPT account (device code flow)
#   3. Generates proxy secret and .env file
#   4. Optionally installs systemd service
# ─────────────────────────────────────────────────────────────────────

DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$DIR/.venv"
ENV_FILE="$DIR/.env"
AUTH_FILE="$HOME/.codex/auth.json"

echo "╔══════════════════════════════════════╗"
echo "║       coproxy-ai setup               ║"
echo "╚══════════════════════════════════════╝"
echo

# ─── Step 0: Prerequisites ──────────────────────────────────────────
if ! python3 -m venv --help >/dev/null 2>&1; then
    PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
    echo "Error: python3-venv is not installed."
    echo "  sudo apt install python${PY_VER}-venv"
    exit 1
fi

# ─── Step 1: Python venv ────────────────────────────────────────────
echo "→ Creating virtual environment..."
python3 -m venv "$VENV"
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q -e "$DIR"
echo "  Done."
echo

# ─── Step 2: Login ──────────────────────────────────────────────────
if [ -f "$AUTH_FILE" ]; then
    echo "→ Auth file found: $AUTH_FILE"
    read -rp "  Re-login? [y/N] " re
    if [[ "${re,,}" == "y" ]]; then
        "$VENV/bin/coproxy-login" --auth-file "$AUTH_FILE"
    fi
else
    echo "→ Logging in to ChatGPT..."
    "$VENV/bin/coproxy-login" --auth-file "$AUTH_FILE"
fi
echo

# ─── Step 3: Generate .env ──────────────────────────────────────────
if [ -f "$ENV_FILE" ]; then
    echo "→ .env already exists, keeping it."
else
    SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))")

    # Security options
    ENABLE_TLS=""
    ENABLE_UDS=""

    echo "→ Security options (recommended for shared hosting):"
    echo
    read -rp "  Enable Unix socket? (only you can access the proxy) [y/N] " uds_choice
    if [[ "${uds_choice,,}" == "y" ]]; then
        ENABLE_UDS="$HOME/.coproxy/coproxy.sock"
        mkdir -p "$(dirname "$ENABLE_UDS")"
    fi

    read -rp "  Enable TLS/HTTPS? (encrypts loopback traffic) [y/N] " tls_choice
    if [[ "${tls_choice,,}" == "y" ]]; then
        ENABLE_TLS="true"
    fi

    cat > "$ENV_FILE" <<EOF
COPROXY_SECRET=$SECRET
COPROXY_AUTH_FILE=$AUTH_FILE
EOF
    if [ -n "$ENABLE_UDS" ]; then
        echo "COPROXY_UNIX_SOCKET=$ENABLE_UDS" >> "$ENV_FILE"
    fi
    if [ -n "$ENABLE_TLS" ]; then
        echo "COPROXY_TLS=true" >> "$ENV_FILE"
    fi
    chmod 600 "$ENV_FILE"
    echo "→ Generated .env with proxy secret."
    echo "  Clients should use this as their API key:"
    echo "  $SECRET"
    if [ -n "$ENABLE_UDS" ]; then
        echo "  Unix socket: $ENABLE_UDS (chmod 600, owner-only)"
    fi
    if [ -n "$ENABLE_TLS" ]; then
        echo "  TLS enabled — certificate will be auto-generated on first run."
    fi
fi
echo

# ─── Step 4: Verify ─────────────────────────────────────────────────
echo "→ Verifying configuration..."
"$VENV/bin/coproxy" --dry-run
echo

# ─── Step 5: Systemd (optional) ─────────────────────────────────────
read -rp "→ Install systemd service? [y/N] " install_svc
if [[ "${install_svc,,}" == "y" ]]; then
    SERVICE_FILE="/etc/systemd/system/coproxy.service"
    sudo tee "$SERVICE_FILE" > /dev/null <<EOF
[Unit]
Description=coproxy-ai — Local OpenAI Proxy
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$DIR
ExecStart=$VENV/bin/coproxy
Restart=on-failure
RestartSec=5
EnvironmentFile=$ENV_FILE
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=$HOME/.codex $HOME/.coproxy

[Install]
WantedBy=multi-user.target
EOF
    sudo systemctl daemon-reload
    sudo systemctl enable --now coproxy
    echo "  Service installed and started."
    echo
    echo "  Useful commands:"
    echo "    sudo systemctl status coproxy"
    echo "    sudo journalctl -u coproxy -f"
else
    echo
    echo "  To run manually:"
    echo "    source $ENV_FILE && $VENV/bin/coproxy"
fi

PROTO="http"
grep -q "COPROXY_TLS=true" "$ENV_FILE" 2>/dev/null && PROTO="https"
UDS_PATH=$(grep "^COPROXY_UNIX_SOCKET=" "$ENV_FILE" 2>/dev/null | cut -d= -f2)

echo
echo "╔══════════════════════════════════════╗"
echo "║            Setup complete!           ║"
echo "╚══════════════════════════════════════╝"
echo
if [ -n "$UDS_PATH" ]; then
    echo "Proxy listens on unix:${UDS_PATH}"
    echo
    echo "Use with curl:"
    echo "  curl --unix-socket ${UDS_PATH} ${PROTO}://localhost/v1/chat/completions ..."
    echo
    echo "Use with Python:"
    echo "  OpenAI(base_url=\"${PROTO}://localhost/v1\","
    echo "         http_client=httpx.Client(transport=httpx.HTTPTransport(uds=\"${UDS_PATH}\")))"
else
    echo "Proxy listens on ${PROTO}://127.0.0.1:8765/v1"
    echo
    echo "Use with any OpenAI-compatible client:"
    echo "  OPENAI_BASE_URL=${PROTO}://127.0.0.1:8765/v1"
    echo "  OPENAI_API_KEY=<your COPROXY_SECRET>"
fi
if [ "$PROTO" = "https" ]; then
    echo
    echo "Note: self-signed certificate — clients may need:"
    echo "  Python:  OpenAI(http_client=httpx.Client(verify=False))"
    echo "  curl:    curl -k ..."
    echo "  Or add ~/.coproxy/tls/cert.pem to trusted CAs."
fi
