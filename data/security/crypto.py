"""Encryption-at-rest + the key-management (BYOK) seam.

PII (the identity table) is encrypted at rest with authenticated symmetric
encryption (Fernet = AES-128-CBC + HMAC-SHA256). Everything that needs to protect
data at rest depends only on `encrypt()` / `decrypt()`, and those depend only on
`load_key()` — so the entire key story is swappable in one place.

KEY-MANAGEMENT SEAM (where BYOK plugs in)
-----------------------------------------
`load_key()` resolves the data-encryption key in this priority order:
  1. `PULSESCORE_DATA_KEY`            (env var — how production should inject it)
  2. a local key file (gitignored)    (developer convenience for local runs)
  3. fail closed                       (no key -> refuse to operate)

For a production financial-grade deployment, replace step 1/2 with a provider
backed by the customer's KMS/HSM (AWS KMS, GCP KMS, Azure Key Vault, HashiCorp
Vault). The key never lives in code or the repo. There is no hardcoded key here.
"""
from __future__ import annotations

import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

# Default location for the developer-only local key (excluded by .gitignore).
_DEFAULT_KEYFILE = Path(__file__).resolve().parents[1] / ".localkey"


class KeyError_(Exception):
    """Key configuration or cryptographic failure. Carries no sensitive detail."""


def _keyfile_path() -> Path:
    return Path(os.environ.get("PULSESCORE_KEYFILE", str(_DEFAULT_KEYFILE)))


def load_key() -> bytes:
    """Resolve the active data-encryption key (env -> local keyfile -> fail closed)."""
    env_key = os.environ.get("PULSESCORE_DATA_KEY")
    if env_key:
        return env_key.encode()
    keyfile = _keyfile_path()
    if keyfile.exists():
        return keyfile.read_bytes().strip()
    raise KeyError_(
        "no data-encryption key configured: set PULSESCORE_DATA_KEY or run "
        "`python -m data.security.keygen`"
    )


def ensure_local_key() -> Path | None:
    """Dev convenience: if no key is configured, mint one into the gitignored
    keyfile so local generation/serving works. Returns the keyfile path if it
    created one, else None. Never overwrites an existing key. Not for production.
    """
    if os.environ.get("PULSESCORE_DATA_KEY"):
        return None
    keyfile = _keyfile_path()
    if not keyfile.exists():
        keyfile.write_bytes(Fernet.generate_key())
        try:
            keyfile.chmod(0o600)
        except OSError:
            pass  # best-effort on platforms without POSIX perms
        return keyfile
    return None


def _cipher() -> Fernet:
    try:
        return Fernet(load_key())
    except KeyError_:
        raise
    except Exception as exc:  # malformed key material
        raise KeyError_("invalid encryption key configuration") from exc


def encrypt(plaintext: str) -> bytes:
    """Encrypt a UTF-8 string to an authenticated ciphertext blob."""
    return _cipher().encrypt(plaintext.encode("utf-8"))


def decrypt(blob: bytes) -> str:
    """Decrypt a ciphertext blob. Raises KeyError_ on tamper/wrong key — never
    echoes ciphertext, plaintext, or key material in the error."""
    try:
        return _cipher().decrypt(blob).decode("utf-8")
    except InvalidToken as exc:
        raise KeyError_("decryption failed") from exc
