"""Pseudonymization & name-resolution seam (single, isolated, lockable).

THE MODEL
---------
Throughout the system an employee is a pseudonymous **token** (e.g. `emp_3f9a...`).
Real PII (full_name, email) lives ONLY in the encrypted `identities` table, keyed
by token. Scoring, the query seam, and all analytics operate on tokens and never
need a real name to function.

Real names are produced in exactly one place: `resolve_names()`. It is the "final
UI layer" boundary — the UI calls it to turn the tokens it is about to display
into names, and only for records the caller is authorized to see. Because every
name ever shown flows through this one function, the whole feature can be locked
down (disabled, further restricted, or routed to a stricter authz check) without
touching any query or scoring logic.

Every resolution is authorized (same permission scope as the query seam) and
audited (who resolved which tokens, when).
"""
from __future__ import annotations

import secrets
import sqlite3
from random import Random

from .audit import write_audit
from .auth import Actor
from .security import crypto


def new_token(rng: Random | None = None) -> str:
    """Mint an opaque, non-sequential employee token.

    Non-sequential so tokens cannot be enumerated or used to infer headcount/order.
    A seeded Random may be passed for reproducible synthetic data.
    """
    if rng is not None:
        return "emp_" + "".join(rng.choice("0123456789abcdef") for _ in range(12))
    return "emp_" + secrets.token_hex(6)


def store_identity(conn: sqlite3.Connection, token: str, full_name: str, email: str,
                   photo: str | None = None) -> None:
    """Encrypt and store PII for a token. The ONLY writer of the identity table.

    `photo` is an optional profile-picture data URL (small thumbnail); it is encrypted at rest
    exactly like the name/email and only ever read back through `resolve_photos`.
    """
    conn.execute(
        "INSERT INTO identities (token, full_name_enc, email_enc, photo_enc) VALUES (?, ?, ?, ?)",
        (token, crypto.encrypt(full_name), crypto.encrypt(email),
         crypto.encrypt(photo) if photo else None),
    )


def resolve_photos(
    conn: sqlite3.Connection, tokens: list[str], *, actor: Actor
) -> dict[str, str]:
    """Resolve authorized tokens to their profile-picture data URLs. Returns {token: data_url}
    for the tokens the caller may see that actually have a photo. Same authorization scope +
    audit as `resolve_names` (the identity boundary); tokens without a photo are omitted."""
    requested = list(dict.fromkeys(tokens))
    allowed = _authorized_tokens(conn, requested, actor)
    resolved: dict[str, str] = {}
    for token in requested:
        if token not in allowed:
            continue
        row = conn.execute(
            "SELECT photo_enc FROM identities WHERE token = ?", (token,)
        ).fetchone()
        if row is not None and row["photo_enc"] is not None:
            resolved[token] = crypto.decrypt(row["photo_enc"])
    write_audit(
        conn,
        actor_email=actor.email,
        action="resolve_photo",
        filters={"granted": len(resolved), "requested": len(requested)},
        result_count=len(resolved),
    )
    return resolved


def _authorized_tokens(
    conn: sqlite3.Connection, tokens: list[str], actor: Actor
) -> set[str]:
    """Return the subset of tokens the actor is permitted to resolve.

    Mirrors the query seam's permission scope (defense in depth): admins may
    resolve anyone; a manager may resolve only tokens in their own division.
    """
    if not tokens:
        return set()
    placeholders = ",".join("?" for _ in tokens)
    if actor.is_admin:
        rows = conn.execute(
            f"SELECT token FROM employees WHERE token IN ({placeholders})", tokens
        ).fetchall()
    else:
        rows = conn.execute(
            f"SELECT token FROM employees WHERE division = ? AND token IN ({placeholders})",
            [actor.division, *tokens],
        ).fetchall()
    return {r["token"] for r in rows}


def search_identities(
    conn: sqlite3.Connection, q: str, *, actor: Actor, limit: int = 8
) -> list[dict]:
    """Name->token typeahead for the UI person-picker. The reverse of resolve_names.

    Same authorization scope as resolve_names (defense in depth): admins may search
    everyone; a manager only their own division. Because names are Fernet-encrypted at
    rest, the substring match is done in Python on decrypted names over the (already
    scoped) candidate set — never as SQL on ciphertext. Returns up to `limit` matches as
    {token, name, role, division, location}; this is the SAME boundary as resolve_names,
    so locking identity down disables search too.

    Audited per call. The audit records WHO searched and HOW MANY matched, but NOT the
    raw query fragment (it is a partial name — PII) — only its length, for forensics.
    """
    needle = q.strip().lower()
    if actor.is_admin:
        rows = conn.execute(
            "SELECT e.token, e.role, e.division, e.location, i.full_name_enc "
            "FROM employees e JOIN identities i ON i.token = e.token"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT e.token, e.role, e.division, e.location, i.full_name_enc "
            "FROM employees e JOIN identities i ON i.token = e.token WHERE e.division = ?",
            (actor.division,),
        ).fetchall()

    matches: list[dict] = []
    if needle:
        for r in rows:
            name = crypto.decrypt(r["full_name_enc"])
            if needle in name.lower():
                matches.append({"token": r["token"], "name": name, "role": r["role"],
                                "division": r["division"], "location": r["location"]})
    # Deterministic ordering: prefix matches first, then alphabetical by name.
    matches.sort(key=lambda m: (not m["name"].lower().startswith(needle), m["name"].lower()))
    out = matches[:limit]

    write_audit(
        conn,
        actor_email=actor.email,
        action="search_identity",
        filters={"q_len": len(needle), "matched": len(out)},
        result_count=len(out),
    )
    return out


def resolve_names(
    conn: sqlite3.Connection, tokens: list[str], *, actor: Actor
) -> dict[str, str]:
    """Resolve authorized tokens to real names. Returns {token: name}.

    Unauthorized or unknown tokens are simply omitted (the caller sees only
    tokens for those). Always writes one audit record describing the resolution.
    """
    requested = list(dict.fromkeys(tokens))  # de-dup, preserve order
    allowed = _authorized_tokens(conn, requested, actor)

    resolved: dict[str, str] = {}
    for token in requested:
        if token not in allowed:
            continue
        row = conn.execute(
            "SELECT full_name_enc FROM identities WHERE token = ?", (token,)
        ).fetchone()
        if row is not None:
            resolved[token] = crypto.decrypt(row["full_name_enc"])

    write_audit(
        conn,
        actor_email=actor.email,
        action="resolve_name",
        filters={
            "tokens": sorted(requested),
            "granted": len(resolved),
            "denied": len(requested) - len(resolved),
        },
        result_count=len(resolved),
    )
    return resolved
