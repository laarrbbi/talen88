"""The pipeline — orchestrates land -> map -> normalize -> resolve -> validate -> write.

Glue only: each stage lives in its own module. `run_ingestion` drives them in order,
short-circuiting loudly on validation failure (nothing is written if validation fails),
and returns a structured `IngestionReport` (per-stage counts + the human review queue).
Writing is parameterized `INSERT OR REPLACE` into the adapter's canonical target,
restricted to columns that actually exist on that table (so transient resolution keys
such as `email` are dropped before write — they never become analytics columns).
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..security import crypto
from . import normalize
from .adapters import Adapter
from .readers import READERS
from .resolve import ResolutionResult, ReviewItem, resolve_records
from .validate import validate

Record = dict[str, Any]


@dataclass
class IngestionReport:
    adapter: str
    target: str
    landed: int = 0
    resolved: int = 0
    unmatched: int = 0
    written: int = 0
    review_queue: list[ReviewItem] = field(default_factory=list)

    def summary(self) -> str:
        return (f"[{self.adapter} -> {self.target}] landed={self.landed} "
                f"resolved={self.resolved} written={self.written} "
                f"unmatched={self.unmatched} review={len(self.review_queue)}")


def _map_record(adapter: Adapter, src: Record) -> Record:
    """Stage 2 — rename source columns to canonical fields and collect JSON groups."""
    rec: Record = {canon: src.get(source) for source, canon in adapter.field_map.items()}
    for canon_json, group in adapter.group_map.items():
        collected = {label: src.get(col) for label, col in group.items() if col in src}
        rec[canon_json] = json.dumps(collected)
    rec.update(adapter.constant_fields)
    return rec


def _normalize_record(adapter: Adapter, rec: Record) -> Record:
    """Stage 3 — coerce dates, currency, level and controlled-vocab in place."""
    for col in adapter.date_fields:
        if col in rec:
            rec[col] = normalize.to_iso_date(rec[col])
    for col in adapter.level_fields:
        if col in rec:
            rec[col] = normalize.normalize_level(rec[col])
    if adapter.currency_field and adapter.amount_fields:
        currency = rec.get(adapter.currency_field)
        for col in adapter.amount_fields:
            if col in rec:
                rec[col] = normalize.to_base_currency(rec[col], currency)
        rec[adapter.currency_field] = "USD"  # folded onto the base currency
    for col in adapter.enum_fields:
        if col in rec:
            rec[col] = normalize.normalize_enum(col, rec[col])
    return rec


def build_candidates(conn: sqlite3.Connection, key_fields: tuple[str, ...]) -> list[Record]:
    """Build resolution candidates: one dict per employee carrying its token plus the
    requested key fields. `email`/`full_name` are decrypted from `identities` (the sole
    PII store); any other key is read straight from the `employees` row."""
    id_keys = {"email", "full_name"}
    rows = conn.execute("SELECT * FROM employees").fetchall()
    candidates: list[Record] = []
    for row in rows:
        cand: Record = {"token": row["token"]}
        for k in key_fields:
            if k in id_keys:
                continue
            cand[k] = row[k] if k in row.keys() else None
        candidates.append(cand)
    if id_keys & set(key_fields):
        by_token = {c["token"]: c for c in candidates}
        for ident in conn.execute("SELECT token, full_name_enc, email_enc FROM identities"):
            cand = by_token.get(ident["token"])
            if cand is None:
                continue
            if "email" in key_fields:
                cand["email"] = crypto.decrypt(ident["email_enc"])
            if "full_name" in key_fields:
                cand["full_name"] = crypto.decrypt(ident["full_name_enc"])
    return candidates


def _write(conn: sqlite3.Connection, adapter: Adapter,
           resolved: list[tuple[Record, str]]) -> int:
    """Stage 6 — INSERT OR REPLACE into the canonical target, columns that exist only."""
    table_cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({adapter.target})")}
    written = 0
    for rec, token in resolved:
        row: Record = {"employee_token": token}
        for col, val in rec.items():
            if col in table_cols:
                row[col] = val
        cols = list(row)
        conn.execute(
            f"INSERT OR REPLACE INTO {adapter.target} ({','.join(cols)}) "
            f"VALUES ({','.join('?' * len(cols))})",
            tuple(row[c] for c in cols),
        )
        written += 1
    return written


def run_ingestion(
    conn: sqlite3.Connection,
    path: str | Path,
    adapter: Adapter,
    *,
    min_rows: int = 1,
    commit: bool = True,
) -> IngestionReport:
    """Run one source file through the full pipeline into the canonical target table.

    Raises `ValidationError` (writing nothing) if the batch fails validation. Ambiguous
    or unknown-person records go to the report's review queue and are NOT written.
    """
    report = IngestionReport(adapter=adapter.name, target=adapter.target)

    # 1 — LAND
    reader = READERS[adapter.fmt]
    kwargs = {"sheet": adapter.sheet, "header_row": adapter.header_row} if adapter.fmt == "excel" else {}
    raw = reader(path, **kwargs)
    report.landed = len(raw)

    # 2 — MAP, 3 — NORMALIZE
    mapped = [_normalize_record(adapter, _map_record(adapter, src)) for src in raw]

    # 4 — RESOLVE
    candidates = build_candidates(conn, adapter.key_fields)
    res: ResolutionResult = resolve_records(mapped, candidates, adapter.key_fields)
    report.resolved = len(res.resolved)
    report.unmatched = len(res.unmatched)
    report.review_queue = res.review_queue

    # 5 — VALIDATE (fail loudly; nothing written on failure)
    known = {r["token"] for r in conn.execute("SELECT token FROM employees")}
    validate(res.resolved, required=adapter.required, known_tokens=known, min_rows=min_rows)

    # 6 — WRITE
    report.written = _write(conn, adapter, res.resolved)
    if commit:
        conn.commit()
    return report
