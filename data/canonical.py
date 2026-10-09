"""Canonical schema vocabulary + walls (Talent88 Data Schema v2 FINAL).

Single source of truth for:
  * the CONTROLLED VOCABULARIES (§4 enums) — used both to build the CHECK
    constraints in `schema_canonical.sql` (kept in sync by `test_schema_canonical`)
    and to validate ingestion input before it ever touches the DB; and
  * the SCORING WALL (`SCORING_EXCLUDED_COLUMNS`) — the canonical column names that
    must NEVER reach any scoring feature set: the bias-audit-only protected traits
    (§4 Table 1) and the identity-proxy fields (§4 Table 9). These are enforced by
    `test_bias_wall` / `test_identity_proxy_wall`, which assert the names appear in
    no `model_service` feature list.

This module has NO database or model dependency: it is plain data + tiny helpers, so
both the data layer and the test suite can import it without side effects.
"""
from __future__ import annotations

# --- Controlled vocabularies (§4) -------------------------------------------------
# Division is DATA-DRIVEN (open vocabulary): the `divisions` table is the registry, so a
# real company can ingest its own org structure (Engineering, Sales, ...) via the manual
# import path. These five remain the SEED set — the values the synthetic generator emits
# and the canonical targets the Workday alias-normalizer folds onto — but the DB no longer
# CHECK-constrains employee_core.division to them (see schema_canonical.sql / migration in
# data/manual_ingest.ensure_dynamic_divisions). The manual-add path validates a division as
# any non-empty string and registers it, rather than against this tuple.
DIVISIONS = ("front office", "technology", "risk & compliance", "operations", "corporate")

EMPLOYMENT_TYPES = ("full_time", "part_time", "contractor")
BUSINESS_TRAVEL_FREQUENCIES = ("none", "occasional", "frequent")
EMPLOYEE_STATUSES = ("active", "on_leave", "terminated")
# Statuses meaning the person no longer works here. Current-state views (Watchlist,
# Dashboard, people lists, survey audiences, cost/forecast analytics) exclude them;
# historical views (turnover, attrition labels) keep them.
FORMER_STATUSES = frozenset({"terminated"})

EDUCATION_LEVELS = ("high_school", "associate", "bachelor", "master", "doctorate")
EDUCATION_FIELDS = (
    "life_sciences", "medical", "marketing", "technical_degree",
    "human_resources", "business", "other",
)
CURRENCIES = ("USD", "GBP", "EUR", "SGD", "HKD", "JPY")

TENURE_CURVE_SHAPES = ("lengthening", "shortening", "stable")
ONBOARDING_STATUSES = ("not_started", "in_progress", "complete")

LEAVER_REASONS = (
    "compensation", "career_growth", "management", "relocation",
    "performance", "retirement", "other",
)
LEAVER_DESTINATIONS = ("competitor", "other_industry", "startup", "unknown")

# Bias-audit protected-trait controlled vocab (read ONLY via data/bias_audit.py).
GENDERS = ("female", "male", "non_binary", "undisclosed")
MARITAL_STATUSES = ("single", "married", "divorced", "undisclosed")
BIRTH_YEAR_BANDS = ("<1970", "1970-1979", "1980-1989", "1990-1999", "2000+")

# Map name -> the allowed value tuple, so tests can drive enum coverage generically.
ENUMS: dict[str, tuple[str, ...]] = {
    "division": DIVISIONS,
    "employment_type": EMPLOYMENT_TYPES,
    "business_travel_frequency": BUSINESS_TRAVEL_FREQUENCIES,
    "status": EMPLOYEE_STATUSES,
    "education_level": EDUCATION_LEVELS,
    "education_field": EDUCATION_FIELDS,
    "currency": CURRENCIES,
    "tenure_curve_shape": TENURE_CURVE_SHAPES,
    "onboarding_status": ONBOARDING_STATUSES,
    "leaver_reason": LEAVER_REASONS,
    "leaver_destination": LEAVER_DESTINATIONS,
    "gender": GENDERS,
    "marital_status": MARITAL_STATUSES,
    "birth_year_band": BIRTH_YEAR_BANDS,
}

# --- The scoring wall (§4 Table 1 bias fields + §4 Table 9 identity proxies) -------
# These canonical columns are fairness/skills-search inputs ONLY and must never enter
# a feature set for any score that touches pay, promotion, or retention. Enforced by
# the bias-/identity-proxy-wall tests against the model_service feature lists.
BIAS_AUDIT_COLUMNS = frozenset({"gender", "birth_year_band", "marital_status"})
IDENTITY_PROXY_COLUMNS = frozenset({"school_prestige", "named_employer"})
SCORING_EXCLUDED_COLUMNS = BIAS_AUDIT_COLUMNS | IDENTITY_PROXY_COLUMNS

# --- §9 explicit RED exclusions (substrings that must not appear as a column) ------
# Used by `test_schema_canonical` to prove no forbidden data category was modeled.
FORBIDDEN_COLUMN_SUBSTRINGS = (
    "message_content", "email_content", "chat_content", "keystroke",
    "screenshot", "screen_capture", "webcam", "biometric", "fingerprint",
    "ssn", "social_security", "national_id", "bank_account",
)


def current_employee_sql(alias: str) -> str:
    """SQL predicate that keeps only current employees, for an `employee_core` alias.

    A token with no canonical row (status NULL) counts as current. `alias` must be a
    code literal (e.g. "ec"), never caller input. The status list is a fixed constant.
    """
    former = ", ".join(f"'{s}'" for s in sorted(FORMER_STATUSES))
    return f"COALESCE({alias}.status, 'active') NOT IN ({former})"


def validate_enum(field: str, value) -> None:
    """Raise ValueError if `value` is not a member of the named controlled vocab.

    NULL/None is allowed here (nullability is a column-level concern); callers that
    require presence check that separately. Used by ingestion's normalize stage so a
    bad controlled-vocab value fails loudly BEFORE the DB CHECK would catch it."""
    if value is None:
        return
    allowed = ENUMS.get(field)
    if allowed is None:
        raise KeyError(f"no controlled vocabulary named {field!r}")
    if value not in allowed:
        raise ValueError(f"{field}={value!r} not in controlled vocab {allowed}")
