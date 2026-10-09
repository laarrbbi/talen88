"""Stage 5 — VALIDATE. Fail loudly before anything is written.

Collects ALL problems across the batch and raises a single `ValidationError` that lists
them, rather than dying on the first bad row. Four classes of check:

    schema       required canonical fields are present and non-null
    ranges       numeric fields fall inside sane bounds (salary > 0, level 1..6, ...)
    referential  every resolved employee_token actually exists in `employees`
    volume       the batch is not a suspicious near-empty drop (a silent upstream break)

Controlled-vocab values are validated earlier (normalize stage) so they cannot reach
here out of range. Nothing in this module writes; it only inspects.
"""
from __future__ import annotations

from typing import Any

Record = dict[str, Any]

# (field -> (low, high)) inclusive numeric bounds applied when the field is present.
RANGE_RULES: dict[str, tuple[float, float]] = {
    "level": (1, 6),
    "base_salary": (0, 100_000_000),
    "pay_percentile_in_role": (0, 1),
    "goal_attainment": (0, 2),
    "engagement_score": (0, 100),
    "enps_score": (-100, 100),
    "survey_response_rate": (0, 1),
    "span_of_control": (0, 10_000),
}


class ValidationError(Exception):
    """Raised when a batch fails validation. `errors` is the full list of problems."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__(f"{len(errors)} validation error(s): " + "; ".join(errors[:10]))


def validate(
    resolved: list[tuple[Record, str]],
    *,
    required: tuple[str, ...],
    known_tokens: set[str],
    min_rows: int = 1,
) -> None:
    """Validate resolved (record, token) pairs. Raises ValidationError listing every
    problem found. `known_tokens` is the set of valid employees(token) for the
    referential check; `min_rows` enforces volume sanity (fail on a near-empty batch)."""
    errors: list[str] = []

    if len(resolved) < min_rows:
        errors.append(
            f"volume too low: {len(resolved)} resolved row(s) < expected minimum {min_rows}")

    for i, (rec, token) in enumerate(resolved):
        for col in required:
            if rec.get(col) in (None, ""):
                errors.append(f"row {i}: missing required field {col!r}")
        if token not in known_tokens:
            errors.append(f"row {i}: employee_token {token!r} not in employees (broken ref)")
        for col, (lo, hi) in RANGE_RULES.items():
            val = rec.get(col)
            if val in (None, ""):
                continue
            try:
                num = float(val)
            except (TypeError, ValueError):
                errors.append(f"row {i}: {col}={val!r} is not numeric")
                continue
            if not (lo <= num <= hi):
                errors.append(f"row {i}: {col}={num} out of range [{lo}, {hi}]")

    if errors:
        raise ValidationError(errors)
