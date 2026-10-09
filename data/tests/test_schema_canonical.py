"""Phase 1 — canonical schema (Talent88 v2 FINAL §4/§5/§6).

Proves the additive overlay exists with the right shape and that its CONTROLLED
VOCAB CHECK constraints, §9 RED exclusions, and pseudonymization rule hold — without
touching the legacy tables or seams (those are covered by the unchanged suite).
"""
from __future__ import annotations

import sqlite3

import pytest

from data import canonical
from data.db import SCHEMA_CANONICAL_PATH, _CANONICAL_TABLES
from data.tests.conftest import T_ADA, T_BO, T_CY, T_OPS_HEAD, T_TECH_HEAD

# Every canonical table the overlay must create (Table 0 `identity` == legacy
# `identities`, intentionally not duplicated).
EXPECTED_TABLES = {
    "employee_core", "compensation", "career_mobility", "performance",
    "performance_division_signals", "skills_credentials", "engagement",
    "manager_team", "collaboration_metadata", "cv_trajectory", "external_market",
    "lifecycle", "label_attrition", "features_engineered", "interventions",
    "engagement_prefs",
}

SNAPSHOT_TABLES = {
    "engagement": "as_of_date",
    "collaboration_metadata": "as_of_date",
    "external_market": "as_of_date",
    "lifecycle": "as_of_date",
    "features_engineered": "score_snapshot_date",
}


def _columns(conn, table) -> set[str]:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}


def _all_canonical_columns(conn) -> dict[str, set[str]]:
    return {t: _columns(conn, t) for t in EXPECTED_TABLES}


def test_all_canonical_tables_exist(conn):
    present = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert EXPECTED_TABLES <= present
    # The drop list in db.py must cover every canonical table (reset stays clean).
    assert EXPECTED_TABLES == set(_CANONICAL_TABLES)


def test_employee_core_has_full_canonical_field_set(conn):
    cols = _columns(conn, "employee_core")
    assert {
        "employee_token", "role", "title", "level", "division", "team",
        "manager_token", "skiplevel_token", "location", "employment_type",
        "business_travel_frequency", "hire_date", "status",
        "birth_year_band", "gender", "marital_status",
    } <= cols


def test_snapshot_tables_key_on_a_date(conn):
    for table, date_col in SNAPSHOT_TABLES.items():
        pk = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})") if r["pk"]]
        assert date_col in pk, f"{table} must include {date_col} in its PRIMARY KEY"


def test_no_red_excluded_columns_anywhere(conn):
    for table, cols in _all_canonical_columns(conn).items():
        for col in cols:
            for forbidden in canonical.FORBIDDEN_COLUMN_SUBSTRINGS:
                assert forbidden not in col, f"§9 violation: {table}.{col}"


def test_no_pii_columns_in_canonical_analytics(conn):
    # PII lives ONLY in the encrypted `identities` table; no canonical table may carry it.
    pii_like = {"full_name", "name", "email", "phone", "address", "dob",
                "date_of_birth", "ssn", "national_id"}
    for table, cols in _all_canonical_columns(conn).items():
        assert not (cols & pii_like), f"PII-shaped column in {table}: {cols & pii_like}"


def test_check_constraints_mirror_canonical_enums():
    """Every allowed value in canonical.ENUMS must appear literally in the DDL, so the
    SQL CHECKs and the Python controlled vocab can never silently diverge.

    `division` is intentionally EXCLUDED: it is a data-driven open vocabulary (registered
    in the `divisions` table) so real-company org structures can be ingested, so it carries
    no DDL CHECK. Its data-driven behaviour is covered by test_employee_core_accepts_custom_division
    and the manual-ingest suite."""
    ddl = SCHEMA_CANONICAL_PATH.read_text()
    # These enums are enforced at the DB layer via CHECK (bias-trait enums are checked
    # too; birth_year_band values contain '<' which is still a literal in the DDL).
    for field in ("employment_type", "business_travel_frequency",
                  "status", "education_level", "education_field", "currency",
                  "tenure_curve_shape", "onboarding_status", "leaver_reason",
                  "leaver_destination", "gender", "marital_status", "birth_year_band"):
        for value in canonical.ENUMS[field]:
            assert f"'{value}'" in ddl, f"{field} value {value!r} missing from DDL CHECK"


# --- enum CHECK constraints reject out-of-vocab values at write time ----------------

def _insert_core(conn, token, **overrides):
    row = {
        "employee_token": token, "role": "SWE", "title": "Engineer", "level": 2,
        "division": "technology", "team": "Tech Team A", "manager_token": None,
        "skiplevel_token": None, "location": "New York", "employment_type": "full_time",
        "business_travel_frequency": "occasional", "hire_date": "2020-01-01",
        "status": "active", "birth_year_band": None, "gender": None,
        "marital_status": None,
    }
    row.update(overrides)
    cols = ",".join(row)
    conn.execute(f"INSERT INTO employee_core ({cols}) VALUES "
                 f"({','.join('?' * len(row))})", tuple(row.values()))


def test_employee_core_accepts_valid_enums(conn):
    _insert_core(conn, T_ADA)  # all-valid baseline inserts cleanly


@pytest.mark.parametrize("field,bad", [
    ("employment_type", "intern"),
    ("business_travel_frequency", "always"),
    ("status", "fired"),
    ("gender", "M"),
    ("marital_status", "complicated"),
])
def test_employee_core_rejects_bad_enum(conn, field, bad):
    with pytest.raises(sqlite3.IntegrityError):
        _insert_core(conn, T_ADA, **{field: bad})


def test_employee_core_accepts_custom_division(conn):
    """division is a data-driven open vocabulary (no DDL CHECK) so a real company can
    ingest its own org structure — a name outside the seed five inserts cleanly."""
    _insert_core(conn, T_ADA, division="Growth Engineering")
    got = conn.execute(
        "SELECT division FROM employee_core WHERE employee_token = ?", (T_ADA,)
    ).fetchone()["division"]
    assert got == "Growth Engineering"


def test_cv_trajectory_rejects_bad_tenure_curve_shape(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO cv_trajectory (employee_token, tenure_curve_shape) "
                     "VALUES (?,?)", (T_BO, "wiggly"))


def test_label_attrition_rejects_bad_leaver_reason(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO label_attrition (employee_token, attrition, leaver_reason) "
                     "VALUES (?,?,?)", (T_CY, 1, "bored"))


def test_engagement_prefs_consent_is_constrained(conn):
    conn.execute("INSERT INTO engagement_prefs (employee_token, monitoring_consent) "
                 "VALUES (?,?)", (T_OPS_HEAD, 1))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO engagement_prefs (employee_token, monitoring_consent) "
                     "VALUES (?,?)", (T_TECH_HEAD, 2))


# --- the scoring wall list is well-formed (enforced against models in phase 2) -----

def test_scoring_excluded_columns_are_present_in_their_tables(conn):
    core = _columns(conn, "employee_core")
    cv = _columns(conn, "cv_trajectory")
    assert canonical.BIAS_AUDIT_COLUMNS <= core
    assert canonical.IDENTITY_PROXY_COLUMNS <= cv
    # And the wall is exactly bias + identity-proxy, nothing dropped.
    assert canonical.SCORING_EXCLUDED_COLUMNS == (
        canonical.BIAS_AUDIT_COLUMNS | canonical.IDENTITY_PROXY_COLUMNS)
