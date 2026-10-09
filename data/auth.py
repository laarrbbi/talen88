"""Authentication & permission scope (own module).

Dev login for the MVP: a user logs in with just their email (no password), and we
issue a short HMAC-signed token carrying their identity + scope. The token decodes
back into an `Actor`, which the query seam REQUIRES and uses to enforce row-level
permissions.

Replacement seam: to add real auth (password / SSO), implement password checking
inside `authenticate()` and keep returning an `Actor`. The query seam, API, UI, and
future agent layer are all unaffected because they only ever see the `Actor`.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sqlite3
from dataclasses import dataclass

# Dev-only signing secret. In production this comes from a secret manager.
_SECRET = os.environ.get("PULSESCORE_SECRET", "dev-only-insecure-secret").encode()


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


def authenticate(conn: sqlite3.Connection, email: str) -> Actor:
    """Dev login: look the user up by email and return their Actor.

    A real implementation would verify a password / SSO assertion here before
    constructing the Actor.
    """
    row = conn.execute(
        "SELECT email, name, role, division FROM users WHERE email = ?",
        (email.strip().lower(),),
    ).fetchone()
    if row is None:
        raise AuthError(f"unknown user: {email}")
    return Actor(
        email=row["email"], name=row["name"], role=row["role"], division=row["division"]
    )


def issue_token(actor: Actor) -> str:
    """Encode an Actor into a signed, URL-safe token (payload.signature)."""
    payload = json.dumps(
        {"email": actor.email, "name": actor.name, "role": actor.role, "division": actor.division},
        sort_keys=True,
    ).encode()
    body = base64.urlsafe_b64encode(payload).decode()
    sig = hmac.new(_SECRET, body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def decode_token(token: str) -> Actor:
    """Verify a token's signature and reconstruct the Actor."""
    try:
        body, sig = token.split(".", 1)
    except ValueError as exc:
        raise AuthError("malformed token") from exc
    expected = hmac.new(_SECRET, body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        raise AuthError("bad token signature")
    data = json.loads(base64.urlsafe_b64decode(body.encode()))
    return Actor(
        email=data["email"], name=data["name"], role=data["role"], division=data["division"]
    )
