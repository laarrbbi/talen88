"""Stage 4 — RESOLVE. Match a source record to an existing employee_token.

Exact-key resolution ONLY. A record is matched to a candidate when every configured
key field is equal (case-folded for strings). The three outcomes are deliberate and
auditable:

    1 candidate   -> resolved (record carries that token forward)
    0 candidates  -> unmatched (cannot enrich a person the system does not know)
    >1 candidates -> REVIEW QUEUE — never auto-merged

The last rule is the safety property the entity-resolution test pins down: two distinct
people that collide on the key are NEVER fused into one. There is no fuzzy/AI matching
here; ambiguity is a human decision.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

Record = dict[str, Any]


@dataclass
class ReviewItem:
    """A record that could not be safely auto-resolved, with the reason and the
    candidate tokens a human reviewer must adjudicate (never merged automatically)."""
    record: Record
    reason: str            # "ambiguous" | "missing_key"
    candidate_tokens: list[str] = field(default_factory=list)


@dataclass
class ResolutionResult:
    resolved: list[tuple[Record, str]] = field(default_factory=list)   # (record, token)
    unmatched: list[Record] = field(default_factory=list)
    review_queue: list[ReviewItem] = field(default_factory=list)


def _key(values, key_fields) -> tuple:
    out = []
    for k in key_fields:
        v = values.get(k)
        out.append(v.strip().lower() if isinstance(v, str) else v)
    return tuple(out)


def resolve_records(
    records: list[Record], candidates: list[Record], key_fields: tuple[str, ...]
) -> ResolutionResult:
    """Resolve each record to a candidate token by exact key match.

    `candidates` are dicts carrying a `token` plus the same key fields. A key that
    maps to more than one distinct token is ambiguous -> review queue (no merge).
    """
    index: dict[tuple, set[str]] = {}
    for cand in candidates:
        index.setdefault(_key(cand, key_fields), set()).add(cand["token"])

    result = ResolutionResult()
    for rec in records:
        if any(rec.get(k) in (None, "") for k in key_fields):
            result.review_queue.append(ReviewItem(rec, "missing_key"))
            continue
        tokens = sorted(index.get(_key(rec, key_fields), set()))
        if len(tokens) == 1:
            result.resolved.append((rec, tokens[0]))
        elif not tokens:
            result.unmatched.append(rec)
        else:
            result.review_queue.append(ReviewItem(rec, "ambiguous", tokens))
    return result
