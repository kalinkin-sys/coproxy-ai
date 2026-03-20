"""Auto-generated self-signed TLS certificate for localhost.

Generated once at first startup, stored next to .env.
Valid for 10 years, bound to 127.0.0.1 / localhost.
"""

from __future__ import annotations

import datetime
import ipaddress
import logging
import os

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

logger = logging.getLogger(__name__)

_DEFAULT_CERT_DIR = "~/.coproxy/tls"


def ensure_cert(cert_dir: str | None = None) -> tuple[str, str]:
    """Return (cert_path, key_path), generating if missing.

    Uses ECDSA P-256 — faster than RSA, smaller files, same security.
    """
    cert_dir = os.path.expanduser(cert_dir or _DEFAULT_CERT_DIR)
    cert_path = os.path.join(cert_dir, "cert.pem")
    key_path = os.path.join(cert_dir, "key.pem")

    if os.path.exists(cert_path) and os.path.exists(key_path):
        logger.info("TLS cert found: %s", cert_path)
        return cert_path, key_path

    logger.info("Generating self-signed TLS certificate...")
    os.makedirs(cert_dir, exist_ok=True)

    # Generate ECDSA key (P-256)
    key = ec.generate_private_key(ec.SECP256R1())

    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "coproxy-ai localhost"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "coproxy-ai"),
    ])

    now = datetime.datetime.now(datetime.timezone.utc)

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName("localhost"),
                x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
                x509.IPAddress(ipaddress.IPv6Address("::1")),
            ]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )

    # Write key (chmod 600)
    with open(key_path, "wb") as f:
        f.write(key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ))
    os.chmod(key_path, 0o600)

    # Write cert
    with open(cert_path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    os.chmod(cert_path, 0o644)

    logger.info("TLS cert generated: %s (valid 10 years)", cert_path)
    return cert_path, key_path

