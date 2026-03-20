"""Token encryption: keyring (desktop) or machine-bound Fernet (server).

Strategy:
  1. If `keyring` is installed and a backend is available → store/retrieve
     the Fernet encryption key in the OS credential store.
  2. Otherwise → derive a Fernet key from /etc/machine-id + UID + salt.
     The encrypted auth.json is useless on another machine or user.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

_SERVICE = "coproxy-ai"
_KEYRING_ACCOUNT = "auth-encryption-key"
_SALT_FILE_NAME = ".coproxy-salt"


# ── Keyring helpers ──────────────────────────────────────────────────

def _try_keyring_get() -> str | None:
    """Try to read the Fernet key from OS keyring. Returns None on failure."""
    try:
        import keyring
        key = keyring.get_password(_SERVICE, _KEYRING_ACCOUNT)
        if key:
            logger.debug("Encryption key loaded from OS keyring")
        return key
    except Exception:
        return None


def _try_keyring_set(key: str) -> bool:
    """Try to store the Fernet key in OS keyring. Returns True on success."""
    try:
        import keyring
        keyring.set_password(_SERVICE, _KEYRING_ACCOUNT, key)
        logger.info("Encryption key stored in OS keyring")
        return True
    except Exception:
        return False


# ── Machine-bound key derivation ─────────────────────────────────────

def _machine_id() -> str:
    """Read /etc/machine-id (Linux) or IOPlatformUUID (macOS)."""
    # Linux
    for path in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            return Path(path).read_text().strip()
        except OSError:
            continue

    # macOS
    try:
        import subprocess
        out = subprocess.check_output(
            ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
            text=True,
        )
        for line in out.splitlines():
            if "IOPlatformUUID" in line:
                return line.split('"')[-2]
    except Exception:
        pass

    # Fallback: hostname (weaker but still ties to machine)
    import socket
    return socket.gethostname()


def _get_or_create_salt(auth_dir: str) -> bytes:
    """Per-installation random salt, stored next to auth.json."""
    salt_path = os.path.join(auth_dir, _SALT_FILE_NAME)
    if os.path.exists(salt_path):
        return Path(salt_path).read_bytes()

    salt = os.urandom(32)
    os.makedirs(auth_dir, exist_ok=True)
    with open(salt_path, "wb") as f:
        f.write(salt)
    os.chmod(salt_path, 0o600)
    return salt


def _derive_machine_key(auth_dir: str) -> str:
    """Derive Fernet key from machine-id + UID + random salt."""
    mid = _machine_id()
    uid = str(os.getuid())
    salt = _get_or_create_salt(auth_dir)

    raw = hashlib.pbkdf2_hmac(
        "sha256",
        f"{mid}:{uid}".encode(),
        salt,
        iterations=480_000,
    )
    key = base64.urlsafe_b64encode(raw[:32])
    return key.decode()


# ── Public API ────────────────────────────────────────────────────────

def get_fernet(auth_file_path: str) -> Fernet:
    """Get a Fernet instance for encrypting/decrypting auth data.

    Tries OS keyring first, falls back to machine-bound derived key.
    """
    auth_dir = os.path.dirname(os.path.expanduser(auth_file_path))

    # 1. Try keyring
    key = _try_keyring_get()
    if key:
        return Fernet(key.encode())

    # 2. Check if we have a stored key in keyring from a previous run
    #    (skip, already tried above)

    # 3. Derive from machine
    key = _derive_machine_key(auth_dir)
    logger.debug("Using machine-bound encryption key")

    # Try to store in keyring for future use
    _try_keyring_set(key)

    return Fernet(key.encode())


def encrypt_json(data: dict, auth_file_path: str) -> bytes:
    """Encrypt a dict as JSON → Fernet ciphertext."""
    f = get_fernet(auth_file_path)
    plaintext = json.dumps(data, indent=2).encode()
    return f.encrypt(plaintext)


def decrypt_json(ciphertext: bytes, auth_file_path: str) -> dict:
    """Decrypt Fernet ciphertext → dict."""
    f = get_fernet(auth_file_path)
    plaintext = f.decrypt(ciphertext)
    return json.loads(plaintext)


def is_encrypted(path: str) -> bool:
    """Check if a file looks like Fernet ciphertext (starts with gAAAAA)."""
    try:
        with open(path, "rb") as f:
            head = f.read(6)
        return head.startswith(b"gAAAAA")
    except OSError:
        return False
