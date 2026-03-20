"""OAuth login for headless servers. Generates auth.json for coproxy."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import sys
import time
from datetime import datetime, timezone
from urllib.parse import urlencode, urlparse, parse_qs

import httpx

CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
AUTH_BASE = "https://auth.openai.com"
DEVICE_VERIFY_URL = "https://auth.openai.com/deviceauth/callback"
SCOPES = "openid profile email offline_access api.connectors.read api.connectors.invoke"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _pkce_pair() -> tuple[str, str]:
    verifier = _b64url(secrets.token_bytes(64))
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge


def _parse_expires(data: dict) -> int:
    """Parse expires_in or expires_at into seconds remaining."""
    if "expires_in" in data:
        return int(data["expires_in"])
    if "expires_at" in data:
        try:
            dt = datetime.fromisoformat(data["expires_at"])
            return max(1, int((dt - datetime.now(timezone.utc)).total_seconds()))
        except (ValueError, TypeError):
            pass
    return 900  # default 15 min


def device_code_login(auth_file: str) -> None:
    """Device code flow — user gets a code to enter at openai.com."""
    print("Requesting device code...")

    with httpx.Client(timeout=30.0) as client:
        resp = client.post(
            f"{AUTH_BASE}/api/accounts/deviceauth/usercode",
            json={"client_id": CLIENT_ID},
        )
        if resp.status_code != 200:
            print(f"Device code flow not available (HTTP {resp.status_code})")
            print("\nFalling back to manual PKCE flow...\n")
            pkce_login(auth_file)
            return

        data = resp.json()

    user_code = data.get("user_code", "")
    # OpenAI uses device_auth_id instead of device_code
    device_code = data.get("device_code") or data.get("device_auth_id", "")
    interval = int(data.get("interval", 5))
    expires_in = _parse_expires(data)

    # OpenAI doesn't return verification_uri — it's a fixed URL
    verification_uri = (
        data.get("verification_uri")
        or data.get("verification_url")
        or data.get("verification_uri_complete")
        or data.get("verification_url_complete")
        or DEVICE_VERIFY_URL
    )

    print()
    print("=" * 50)
    print(f"  1. Open:       {verification_uri}")
    print(f"  2. Enter code: {user_code}")
    print(f"  3. Log in with your ChatGPT account")
    print("=" * 50)
    print()
    print(f"Waiting for authorization (expires in {expires_in // 60} min)...")

    deadline = time.time() + expires_in
    with httpx.Client(timeout=30.0) as client:
        while time.time() < deadline:
            time.sleep(interval)
            resp = client.post(
                f"{AUTH_BASE}/api/accounts/deviceauth/token",
                json={
                    "client_id": CLIENT_ID,
                    "device_code": device_code,
                    "device_auth_id": device_code,
                },
            )
            body = resp.json()

            # Success — got tokens
            if resp.status_code == 200 and ("access_token" in body or "id_token" in body):
                _save_tokens(auth_file, body)
                return

            error = body.get("error", "")
            if error == "authorization_pending":
                sys.stdout.write(".")
                sys.stdout.flush()
                continue
            elif error == "slow_down":
                interval = min(interval + 2, 30)
                continue
            elif error in ("expired_token", "access_denied"):
                print(f"\nLogin failed: {error}")
                sys.exit(1)
            else:
                # Check for tokens in non-standard response
                if "access_token" in body or "id_token" in body:
                    _save_tokens(auth_file, body)
                    return
                # Still pending — keep polling
                sys.stdout.write(".")
                sys.stdout.flush()
                continue

    print("\nDevice code expired. Try again.")
    sys.exit(1)


def pkce_login(auth_file: str) -> None:
    """Manual PKCE flow — user opens URL, copies redirect back."""
    verifier, challenge = _pkce_pair()
    state = _b64url(secrets.token_bytes(32))

    redirect_uri = "http://localhost:1455/auth/callback"

    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": redirect_uri,
        "scope": SCOPES,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "id_token_add_organizations": "true",
    }

    auth_url = f"{AUTH_BASE}/oauth/authorize?{urlencode(params)}"

    print()
    print("Open this URL in your browser:")
    print()
    print(auth_url)
    print()
    print("After login, the browser will try to redirect to localhost.")
    print("It will fail — that's OK. Copy the FULL URL from the browser address bar")
    print("and paste it here:")
    print()

    callback_url = input("> ").strip()

    parsed = urlparse(callback_url)
    qs = parse_qs(parsed.query)

    code = qs.get("code", [None])[0]
    returned_state = qs.get("state", [None])[0]

    if not code:
        print("No 'code' found in URL. Check and try again.")
        sys.exit(1)

    if returned_state != state:
        print("State mismatch — possible CSRF. Try again.")
        sys.exit(1)

    print("Exchanging code for tokens...")

    with httpx.Client(timeout=30.0) as client:
        resp = client.post(
            f"{AUTH_BASE}/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": CLIENT_ID,
                "code_verifier": verifier,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()
        body = resp.json()
        _save_tokens(auth_file, body, client)


def _save_tokens(auth_file: str, body: dict, client: httpx.Client | None = None) -> None:
    """Save tokens to auth.json in Codex CLI format, exchange for API key."""
    path = os.path.expanduser(auth_file)
    os.makedirs(os.path.dirname(path), exist_ok=True)

    # Try to exchange id_token for API key
    api_key = ""
    id_token = body.get("id_token", "")
    if id_token:
        print("Exchanging id_token for API key...")
        should_close = client is None
        client = client or httpx.Client(timeout=30.0)
        try:
            resp = client.post(
                f"{AUTH_BASE}/oauth/token",
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
                    "client_id": CLIENT_ID,
                    "requested_token": "openai-api-key",
                    "subject_token": id_token,
                    "subject_token_type": "urn:ietf:params:oauth:token-type:id_token",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if resp.status_code == 200:
                api_key = resp.json().get("access_token", "")
                print("  API key obtained: yes")
            else:
                print(f"  API key exchange failed: HTTP {resp.status_code}")
        finally:
            if should_close and client:
                client.close()

    from .crypto import encrypt_json

    auth_data = {
        "auth_mode": "chatgpt",
        "tokens": {
            "id_token": id_token,
            "access_token": body.get("access_token", ""),
            "refresh_token": body.get("refresh_token", ""),
        },
        "api_key": api_key,
        "last_refresh": datetime.now(timezone.utc).isoformat(),
    }

    ciphertext = encrypt_json(auth_data, path)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(ciphertext)
    os.replace(tmp, path)
    os.chmod(path, 0o600)

    print(f"\n\nLogin successful! Tokens saved to {path} (encrypted)")
    print(f"  access_token: present")
    print(f"  refresh_token: {'present' if auth_data['tokens']['refresh_token'] else 'missing'}")
    print(f"  api_key: {'present' if api_key else 'missing'}")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Login to OpenAI for coproxy")
    parser.add_argument(
        "--auth-file",
        default="~/.codex/auth.json",
        help="Path to save auth.json (default: ~/.codex/auth.json)",
    )
    parser.add_argument(
        "--pkce",
        action="store_true",
        help="Use manual PKCE flow instead of device code",
    )
    args = parser.parse_args()

    if args.pkce:
        pkce_login(args.auth_file)
    else:
        device_code_login(args.auth_file)


if __name__ == "__main__":
    main()
