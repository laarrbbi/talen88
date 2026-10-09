"""Tamper-evident audit logging of data-access and name-resolution events.

Every call through the query seam and every name resolution records who asked,
what they asked for, and when. The log is **hash-chained**: each row stores
`entry_hash = sha256(prev_hash + canonical(row))`, so any silent edit or deletion
of a historical row breaks the chain and is detectable via `verify_chain()`.

This is tamper-EVIDENT, not tamper-proof. Production hardening (append-only/WORM
storage, an external immutable log sink, periodic anchoring) is noted in SECURITY.md.
Logged fields are deliberately non-sensitive: actor email, action, filter values,
counts, timestamps — never PII or decrypted names.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

GENESIS = ""  # prev_hash for the first row


def _canonical(actor_email: str, action: str, filters_json: str,
               result_count: int | None, ts: str) -> str:
    return "|".join([actor_email, action, filters_json, str(result_count), ts])


def _last_hash(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT entry_hash FROM audit_log ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return row["entry_hash"] if row is not None else GENESIS


def write_audit(
    conn: sqlite3.Connection,
    *,
    actor_email: str,
    action: str,
    filters: dict[str, Any] | None = None,
    result_count: int | None = None,
) -> None:
    filters_json = json.dumps(filters or {}, sort_keys=True)
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    prev_hash = _last_hash(conn)
    entry_hash = hashlib.sha256(
        (prev_hash + _canonical(actor_email, action, filters_json, result_count, ts)).encode()
    ).hexdigest()
    conn.execute(
        "INSERT INTO audit_log (actor_email, action, filters_json, result_count, ts, "
        "prev_hash, entry_hash) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (actor_email, action, filters_json, result_count, ts, prev_hash, entry_hash),
    )
    conn.commit()


def verify_chain(conn: sqlite3.Connection) -> bool:
    """Recompute the hash chain over all rows; return True iff intact."""
    prev = GENESIS
    for row in conn.execute(
        "SELECT actor_email, action, filters_json, result_count, ts, prev_hash, entry_hash "
        "FROM audit_log ORDER BY id ASC"
    ).fetchall():
        if row["prev_hash"] != prev:
            return False
        expected = hashlib.sha256(
            (prev + _canonical(row["actor_email"], row["action"], row["filters_json"],
                               row["result_count"], row["ts"])).encode()
        ).hexdigest()
        if expected != row["entry_hash"]:
            return False
        prev = row["entry_hash"]
    return True
