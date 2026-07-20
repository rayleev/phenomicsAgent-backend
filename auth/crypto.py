"""AES encryption/decryption for API keys using Fernet."""

import os
import base64

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

# Use env var in production, fallback for development
_RAW_KEY = os.environ.get("ENCRYPTION_KEY", "phenomics-encryption-key-32chr!")


def _get_fernet() -> Fernet:
    """Derive a Fernet-compatible 32-byte key from the raw key string."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"phenomics-fixed-salt",
        iterations=100000,
    )
    key = base64.urlsafe_b64encode(kdf.derive(_RAW_KEY.encode()))
    return Fernet(key)


def encrypt_api_key(plaintext: str) -> str:
    """Encrypt an API key. Returns a base64-encoded ciphertext string."""
    f = _get_fernet()
    return f.encrypt(plaintext.encode()).decode()


def decrypt_api_key(ciphertext: str) -> str:
    """Decrypt an API key from its base64-encoded ciphertext."""
    f = _get_fernet()
    return f.decrypt(ciphertext.encode()).decode()
