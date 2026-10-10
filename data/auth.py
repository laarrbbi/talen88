"""Authentication & permission scope (own module).

Operators sign in with email + password. Passwords are stored only as salted scrypt
hashes (stdlib `hashlib.scrypt`, no extra dependency). A successful login issues a short
HMAC-signed token carrying the operator's identity + scope and an expiry; the token
decodes back into an `Actor`, which the query seam REQUIRES and uses to enforce
row-level permissions.

Replacement seam: to add SSO, verify the identity provider's assertion inside
`authenticate()` (or a sibling) and keep returning an `Actor`. The query seam, API, UI,
and agent layer are unaffected because they only ever see the `Actor`.

Fail-closed rules:
  * In production (`PULSESCORE_ENV=production`) the module refuses to load unless
    `PULSESCORE_SECRET` is set to a real value (not a known placeholder, >= 32 chars).
  * Tokens without a valid signature or past their expiry are rejected.
  * Login failures are generic (no hint whether the email exists), and repeated
    failures for one email are throttled.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass

# --- signing secret ---------------------------------------------------------------
_DEV_SECRET = "dev-only-insecure-secret"
# Values that must never sign tokens in production (placeholders that live in the repo).
_KNOWN_PLACEHOLDERS = {_DEV_SECRET, "change-me-set-via-fly-secrets", "e2e-only-secret"}
_MIN_SECRET_LEN = 32


def is_production() -> bool:
    return os.environ.get("PULSESCORE_ENV", "").strip().lower() == "production"


def _load_secret() -> bytes:
    secret = os.environ.get("PULSESCORE_SECRET", "")
    if is_production() and (secret in _KNOWN_PLACEHOLDERS or len(secret) < _MIN_SECRET_LEN):
        raise RuntimeError(
            "PULSESCORE_SECRET must be set to a random value of at least "
            f"{_MIN_SECRET_LEN} characters in production")
    return (secret or _DEV_SECRET).encode()


_SECRET = _load_secret()

# Token lifetime (minutes). Default: one working day.
TOKEN_TTL_SECONDS = int(os.environ.get("PULSESCORE_TOKEN_TTL_MINUTES", "480")) * 60

# --- password hashing (scrypt) ----------------------------------------------------
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2 ** 14, 8, 1
_MIN_PASSWORD_LEN = 10


def hash_password(password: str) -> str:
    """Salted scrypt hash, encoded as `scrypt$n$r$p$salt$hash` (base64 fields)."""
    if len(password) < _MIN_PASSWORD_LEN:
        raise ValueError(f"password must be at least {_MIN_PASSWORD_LEN} characters")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R,
                            p=_SCRYPT_P, dklen=32)
    b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${b64(salt)}${b64(digest)}"


def verify_password(password: str, stored: str | None) -> bool:
    """Constant-time check of `password` against a stored hash. False on any malformed hash."""
    if not stored:
        return False
    try:
        scheme, n, r, p, salt_b64, hash_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        salt, expected = base64.b64decode(salt_b64), base64.b64decode(hash_b64)
        digest = hashlib.scrypt(password.encode(), salt=salt, n=int(n), r=int(r), p=int(p),
                                dklen=len(expected))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest, expected)


# Verified against when the email is unknown, so a miss costs the same time as a hit.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


@dataclass(frozen=True)
class Actor:
    """The authenticated caller. Carries the scope the query seam enforces."""

    email: str
    name: str
    role: str               # "admin" | "manager"
    division: str | None     # None for admin; the manager's division otherwise

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


class AuthError(Exception):
    """Raised when login fails or a token is invalid."""


def authenticate(conn: sqlite3.Connection, email: str, password: str) -> Actor:
    """Check an operator's email + password and return their Actor.

    Raises AuthError with a generic message for an unknown email, a missing password
    hash, or a wrong password alike.
    """
    row = conn.execute(
        "SELECT email, name, role, division, password_hash FROM users WHERE email = ?",
        (email.strip().lower(),),
    ).fetchone()
    stored = row["password_hash"] if row is not None else _DUMMY_HASH
    ok = verify_password(password, stored)
    if row is None or not ok:
        raise AuthError("authentication failed")
    return Actor(
        email=row["email"], name=row["name"], role=row["role"], division=row["division"]
    )


def set_password(conn: sqlite3.Connection, email: str, password: str) -> None:
    """Set (or reset) an operator's password. Raises AuthError if the email is unknown."""
    cur = conn.execute("UPDATE users SET password_hash = ? WHERE email = ?",
                       (hash_password(password), email.strip().lower()))
    if cur.rowcount == 0:
        raise AuthError(f"unknown user: {email}")
    conn.commit()


# --- login throttling -----------------------------------------------------------------
class LoginThrottle:
    """In-memory limit on failed logins per email: after `max_failures` within `window`
    seconds, further attempts for that email are refused until the window passes.
    Per-process (the data API runs as one process)."""

    def __init__(self, max_failures: int = 5, window: float = 15 * 60) -> None:
        self.max_failures = max_failures
        self.window = window
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, key: str, now: float) -> list[float]:
        stamps = [t for t in self._failures.get(key, []) if now - t < self.window]
        self._failures[key] = stamps
        return stamps

    def is_locked(self, email: str) -> bool:
        with self._lock:
            return len(self._recent(email.strip().lower(), time.time())) >= self.max_failures

    def record_failure(self, email: str) -> None:
        with self._lock:
            key = email.strip().lower()
            self._recent(key, time.time()).append(time.time())

    def reset(self, email: str) -> None:
        with self._lock:
            self._failures.pop(email.strip().lower(), None)


# --- tokens -----------------------------------------------------------------------------
def issue_token(actor: Actor, *, ttl_seconds: int | None = None) -> str:
    """Encode an Actor into a signed, URL-safe token (payload.signature) with an expiry."""
    now = int(time.time())
    payload = json.dumps(
        {"email": actor.email, "name": actor.name, "role": actor.role,
         "division": actor.division, "iat": now,
         "exp": now + (TOKEN_TTL_SECONDS if ttl_seconds is None else ttl_seconds)},
        sort_keys=True,
    ).encode()
    body = base64.urlsafe_b64encode(payload).decode()
    sig = hmac.new(_SECRET, body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def decode_token(token: str) -> Actor:
    """Verify a token's signature and expiry, and reconstruct the Actor."""
    try:
        body, sig = token.split(".", 1)
    except ValueError as exc:
        raise AuthError("malformed token") from exc
    expected = hmac.new(_SECRET, body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        raise AuthError("bad token signature")
    try:
        data = json.loads(base64.urlsafe_b64decode(body.encode()))
        exp = int(data["exp"])
    except (ValueError, KeyError, TypeError) as exc:
        raise AuthError("malformed token") from exc
    if time.time() >= exp:
        raise AuthError("token expired")
    return Actor(
        email=data["email"], name=data["name"], role=data["role"], division=data["division"]
    )
