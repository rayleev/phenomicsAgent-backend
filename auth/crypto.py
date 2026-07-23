"""AES encryption/decryption for API keys using Fernet."""

import os
import base64
import secrets

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

# ── Key management ────────────────────────────────────────────────────
# Encryption key is read from the environment ONLY — there is no fallback.
# The server refuses to start if ENCRYPTION_KEY is unset.
_RAW_KEY = os.environ.get("ENCRYPTION_KEY")
if not _RAW_KEY:
    raise RuntimeError(
        "ENCRYPTION_KEY environment variable is required. "
        "Set it before starting the server, e.g. export ENCRYPTION_KEY=$(openssl rand -base64 32)"
    )

# Legacy fixed salt — ONLY used to decrypt pre-existing data that was
# encrypted by the previous (insecure) implementation. New encryptions
# use a random per-record salt. Remove once all records are migrated.
_LEGACY_SALT = b"phenomics-fixed-salt"
_VERSION_PREFIX = "v2:"


def _derive_key(raw: str, salt: bytes) -> bytes:
    """Derive a Fernet-compatible 32-byte key from the raw key + salt."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100000,
    )
    return base64.urlsafe_b64encode(kdf.derive(raw.encode()))


def _get_fernet(salt: bytes) -> Fernet:
    return Fernet(_derive_key(_RAW_KEY, salt))


def encrypt_api_key(plaintext: str) -> str:
    """Encrypt an API key.

    Returns a versioned, salt-prefixed ciphertext string.
    Format: "v2:<base64_salt>:<fernet_ciphertext>"
    A fresh random salt is generated for every encryption, so identical
    plaintexts produce identical-derived keys but unique ciphertexts.
    """
    salt = secrets.token_bytes(16)
    f = _get_fernet(salt)
    ct = f.encrypt(plaintext.encode()).decode()
    return f"{_VERSION_PREFIX}{base64.b64encode(salt).decode()}:{ct}"


def decrypt_api_key(ciphertext: str) -> str:
    """Decrypt an API key.

    Supports both the new v2 format (random salt, prefixed) and the legacy
    fixed-salt format for backward compatibility with existing records.
    """
    if ciphertext.startswith(_VERSION_PREFIX):
        rest = ciphertext[len(_VERSION_PREFIX):]
        salt_b64, _, ct = rest.partition(":")
        try:
            salt = base64.b64decode(salt_b64)
        except Exception:
            raise ValueError("Invalid encrypted key format: bad salt")
        if not salt:
            raise ValueError("Invalid encrypted key format: empty salt")
        f = _get_fernet(salt)
    else:
        # Legacy path — fixed salt (data encrypted by old implementation)
        ct = ciphertext
        f = _get_fernet(_LEGACY_SALT)
    return f.decrypt(ct.encode()).decode()
