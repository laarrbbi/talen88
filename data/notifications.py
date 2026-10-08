"""Engagement layer (Layer 4) — in-app, opt-in notifications.

Rides the audited data API; no new data path. A notification is addressed to an
OPERATOR by login email and references employees by TOKEN only (never PII). Two
guarantees are enforced here:

  - OPT-IN: `create()` writes nothing unless the recipient has opted into that
    `kind` (default off). Nothing is ever pushed without prior consent.
  - SCOPE: a recipient reads/marks ONLY their own notifications — every query is
    filtered by `recipient_email = actor.email`. There is no cross-recipient read.

`channel` on the prefs row is the abstraction seam (in_app for the MVP; slack/teams/
email/mobile are documented future channels). Every notification carries a `why_text`
so the recipient always sees why it was sent.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

# Notification kinds the system can emit, each with a default transparency line.
KINDS = {
    "retention_approved": "A retention action you approved was recorded.",
    "promotion_ready": "Someone in your scope looks promotion-ready.",
    "succession_gap": "A succession gap was flagged in your scope.",
    "learning_recommended": "A learning path was recommended for someone in your scope.",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_opted_in(conn: sqlite3.Connection, email: str, kind: str) -> bool:
    row = conn.execute(
        "SELECT opted_in FROM notification_prefs WHERE email = ? AND kind = ?",
        (email, kind),
    ).fetchone()
    return bool(row and row["opted_in"])


def create(conn: sqlite3.Connection, *, recipient_email: str, kind: str,
           payload: dict, why_text: str | None = None) -> dict:
    """Create a notification IF the recipient opted into `kind`. Returns a status dict.

    payload is token-only structured data (no PII). When the recipient has not opted
    in, nothing is written and {"created": False, "reason": "opted_out"} is returned —
    consent is a hard precondition, not a filter applied at read time.
    """
    if kind not in KINDS:
        return {"created": False, "reason": "unknown_kind"}
    if not is_opted_in(conn, recipient_email, kind):
        return {"created": False, "reason": "opted_out"}
    why = why_text or KINDS[kind]
    cur = conn.execute(
        "INSERT INTO notifications (recipient_email, kind, payload_json, why_text, "
        "created_ts) VALUES (?,?,?,?,?)",
        (recipient_email, kind, json.dumps(payload, sort_keys=True), why, _now()),
    )
    conn.commit()
    return {"created": True, "id": cur.lastrowid, "why": why}


def list_for(conn: sqlite3.Connection, *, actor_email: str,
             unread_only: bool = False, limit: int = 100) -> list[dict]:
    """A recipient's own notifications, newest first. Scoped to `actor_email` only."""
    sql = ("SELECT id, kind, payload_json, why_text, created_ts, read_ts "
           "FROM notifications WHERE recipient_email = ?")
    params: list = [actor_email]
    if unread_only:
        sql += " AND read_ts IS NULL"
    sql += " ORDER BY created_ts DESC, id DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    return [{"id": r["id"], "kind": r["kind"], "payload": json.loads(r["payload_json"]),
             "why": r["why_text"], "created_ts": r["created_ts"],
             "read": r["read_ts"] is not None} for r in rows]


def mark_read(conn: sqlite3.Connection, *, actor_email: str, notif_id: int) -> bool:
    """Mark one of the caller's own notifications read. Returns False if not theirs."""
    cur = conn.execute(
        "UPDATE notifications SET read_ts = ? WHERE id = ? AND recipient_email = ? "
        "AND read_ts IS NULL",
        (_now(), notif_id, actor_email),
    )
    conn.commit()
    if cur.rowcount:
        return True
    # Distinguish "already read / not found" from "not yours" without leaking existence.
    owned = conn.execute(
        "SELECT 1 FROM notifications WHERE id = ? AND recipient_email = ?",
        (notif_id, actor_email),
    ).fetchone()
    return owned is not None


def get_prefs(conn: sqlite3.Connection, *, actor_email: str) -> list[dict]:
    """All notification kinds with the caller's opt-in state (default off if unset)."""
    rows = conn.execute(
        "SELECT kind, channel, opted_in FROM notification_prefs WHERE email = ?",
        (actor_email,),
    ).fetchall()
    set_for = {r["kind"]: r for r in rows}
    out = []
    for kind, desc in KINDS.items():
        r = set_for.get(kind)
        out.append({"kind": kind, "description": desc,
                    "channel": r["channel"] if r else "in_app",
                    "opted_in": bool(r["opted_in"]) if r else False})
    return out


def set_pref(conn: sqlite3.Connection, *, actor_email: str, kind: str,
             opted_in: bool, channel: str = "in_app") -> dict:
    """Upsert one opt-in preference for the caller. Unknown kinds are rejected."""
    if kind not in KINDS:
        return {"updated": False, "reason": "unknown_kind"}
    conn.execute(
        "INSERT INTO notification_prefs (email, kind, channel, opted_in) VALUES (?,?,?,?) "
        "ON CONFLICT(email, kind) DO UPDATE SET opted_in = excluded.opted_in, "
        "channel = excluded.channel",
        (actor_email, kind, channel, 1 if opted_in else 0),
    )
    conn.commit()
    return {"updated": True, "kind": kind, "opted_in": opted_in, "channel": channel}
