"""Phase 2 — core canonical tables populated by the synthetic generator.

Builds a real synthetic DB (the generator path, not the hand-seeded conftest fixture)
and proves the core canonical tables (employee_core, compensation, career_mobility,
performance + §5 packs) are populated 1:1 with employees, key only on tokens, carry no
PII, use only in-vocabulary enum values, and populate the bias-audit traits — which a
companion test proves are walled off from scoring.
"""
from __future__ import annotations

import json
import os

import pytest
from cryptography.fernet import Fernet

os.environ.setdefault("PULSESCORE_DATA_KEY", Fernet.generate_key().decode())

from data import canonical, generate  # noqa: E402
from data.db import get_connection  # noqa: E402
from data.generate import DIVISION_SIGNAL_PACKS, LICENSED_MARKET_REGIONS  # noqa: E402
from data.security import crypto  # noqa: E402
from data.tests.test_schema_canonical import EXPECTED_TABLES  # noqa: E402


@pytest.fixture(scope="module")
def gen_conn(tmp_path_factory):
    """Generate a full synthetic DB into a temp file and hand back a connection."""
    db = tmp_path_factory.mktemp("canon") / "gen.db"
    prev = os.environ.get("PULSESCORE_DB")
    os.environ["PULSESCORE_DB"] = str(db)
    try:
        generate.build(seed=7)
        conn = get_connection()
        yield conn
        conn.close()
    finally:
        if prev is None:
            os.environ.pop("PULSESCORE_DB", None)
        else:
            os.environ["PULSESCORE_DB"] = prev


def _count(conn, table) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_core_tables_are_one_to_one_with_employees(gen_conn):
    n = _count(gen_conn, "employees")
    assert n > 100
    for table in ("employee_core", "compensation", "career_mobility", "performance"):
        assert _count(gen_conn, table) == n, f"{table} not 1:1 with employees"


def test_core_tables_reference_only_known_tokens(gen_conn):
    for table in ("employee_core", "compensation", "career_mobility", "performance"):
        orphans = gen_conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE employee_token NOT IN "
            "(SELECT token FROM employees)").fetchone()[0]
        assert orphans == 0
    # skiplevel_token, when present, must also resolve to a real employee.
    bad_skip = gen_conn.execute(
        "SELECT COUNT(*) FROM employee_core WHERE skiplevel_token IS NOT NULL AND "
        "skiplevel_token NOT IN (SELECT token FROM employees)").fetchone()[0]
    assert bad_skip == 0


def test_employee_core_carries_no_real_name(gen_conn):
    """The decrypted name lives ONLY in `identities`; it must not leak into the
    token-keyed employee_core row anywhere."""
    rows = gen_conn.execute("SELECT token, full_name_enc FROM identities LIMIT 25").fetchall()
    assert rows
    for r in rows:
        name = crypto.decrypt(r["full_name_enc"])
        core = gen_conn.execute(
            "SELECT * FROM employee_core WHERE employee_token = ?", (r["token"],)).fetchone()
        values = [str(v) for v in tuple(core)]
        assert name not in values, f"PII leaked into employee_core for {r['token']}"


def test_division_signals_belong_to_their_division_pack(gen_conn):
    rows = gen_conn.execute(
        "SELECT division, signal FROM performance_division_signals").fetchall()
    assert rows
    for r in rows:
        allowed = {s for s, *_ in DIVISION_SIGNAL_PACKS[r["division"]]}
        assert r["signal"] in allowed, f"{r['signal']} not in {r['division']} pack"


@pytest.mark.parametrize("column", [
    "division", "employment_type", "status", "business_travel_frequency",
    "gender", "marital_status", "birth_year_band",
])
def test_generated_enum_values_are_in_vocabulary(gen_conn, column):
    distinct = {r[0] for r in gen_conn.execute(
        f"SELECT DISTINCT {column} FROM employee_core WHERE {column} IS NOT NULL")}
    assert distinct, f"no values generated for {column}"
    assert distinct <= set(canonical.ENUMS[column]), f"out-of-vocab {column}: {distinct}"


def test_bias_audit_traits_are_populated(gen_conn):
    # Bias path must be demonstrable: every core row carries the protected traits, with
    # genuine variety (so the fairness audit has cohorts to compare).
    missing = gen_conn.execute(
        "SELECT COUNT(*) FROM employee_core WHERE gender IS NULL OR marital_status IS NULL "
        "OR birth_year_band IS NULL").fetchone()[0]
    assert missing == 0
    genders = {r[0] for r in gen_conn.execute("SELECT DISTINCT gender FROM employee_core")}
    assert len(genders) >= 2


# --- Phase 3: remaining 🟢 panel tables + snapshots + labels ----------------------

PANEL_PER_EMPLOYEE = ("skills_credentials", "cv_trajectory")
SNAPSHOT_PANEL = {"engagement": "as_of_date", "lifecycle": "as_of_date",
                  "features_engineered": "score_snapshot_date"}


def test_panel_tables_reference_known_tokens(gen_conn):
    for table in (*PANEL_PER_EMPLOYEE, *SNAPSHOT_PANEL, "manager_team", "label_attrition"):
        col = "manager_token" if table == "manager_team" else "employee_token"
        orphans = gen_conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE {col} NOT IN "
            "(SELECT token FROM employees)").fetchone()[0]
        assert orphans == 0, f"{table} has orphan tokens"


def test_snapshot_panel_has_six_monthly_rows_per_employee(gen_conn):
    n = _count(gen_conn, "employees")
    for table, date_col in SNAPSHOT_PANEL.items():
        # Exactly the six monthly snapshots, for every employee (history mandatory).
        assert _count(gen_conn, table) == n * 6
        min_dates = gen_conn.execute(
            f"SELECT MIN(c) FROM (SELECT COUNT(DISTINCT {date_col}) c FROM {table} "
            "GROUP BY employee_token)").fetchone()[0]
        assert min_dates == 6


def test_features_engineered_trend_is_computable(gen_conn):
    token = gen_conn.execute("SELECT token FROM employees LIMIT 1").fetchone()[0]
    series = gen_conn.execute(
        "SELECT score_snapshot_date, comp_gap_normalized FROM features_engineered "
        "WHERE employee_token = ? ORDER BY score_snapshot_date", (token,)).fetchall()
    assert len(series) == 6
    # A monthly series with distinct dates -> a trend (period-over-period delta) exists.
    assert len({r["score_snapshot_date"] for r in series}) == 6


def test_identity_proxy_fields_live_only_in_cv_trajectory(gen_conn):
    row = gen_conn.execute(
        "SELECT school_prestige, named_employer FROM cv_trajectory LIMIT 1").fetchone()
    assert row["school_prestige"] is not None and row["named_employer"] is not None
    # No other canonical table may even have these columns (proxy containment).
    for table in EXPECTED_TABLES - {"cv_trajectory"}:
        cols = {r["name"] for r in gen_conn.execute(f"PRAGMA table_info({table})")}
        assert "school_prestige" not in cols and "named_employer" not in cols


def test_label_attrition_is_leavers_only(gen_conn):
    labelled = {r[0] for r in gen_conn.execute("SELECT employee_token FROM label_attrition")}
    terminated = {r[0] for r in gen_conn.execute(
        "SELECT employee_token FROM employee_core WHERE status = 'terminated'")}
    assert labelled and labelled == terminated
    # Leavers-only table: every row is a positive attrition label.
    non_positive = gen_conn.execute(
        "SELECT COUNT(*) FROM label_attrition WHERE attrition != 1").fetchone()[0]
    assert non_positive == 0


def test_manager_team_rows_are_managers_with_reports(gen_conn):
    managers = {r[0] for r in gen_conn.execute("SELECT manager_token FROM manager_team")}
    with_reports = {r[0] for r in gen_conn.execute(
        "SELECT DISTINCT manager_token FROM employees WHERE manager_token IS NOT NULL")}
    assert managers and managers <= with_reports
    assert gen_conn.execute(
        "SELECT COUNT(*) FROM manager_team WHERE span_of_control < 1").fetchone()[0] == 0


def test_skills_credentials_match_the_skills_graph(gen_conn):
    for token, in gen_conn.execute("SELECT token FROM employees LIMIT 20").fetchall():
        graph = {r[0] for r in gen_conn.execute(
            "SELECT s.name FROM employee_skills es JOIN skills s ON s.id = es.skill_id "
            "WHERE es.employee_token = ?", (token,))}
        cred = gen_conn.execute(
            "SELECT skills FROM skills_credentials WHERE employee_token = ?", (token,)).fetchone()
        assert set(json.loads(cred["skills"])) == graph


# --- Phase 4: 🟡 consent/license-gated tables + the consent record ----------------

def test_engagement_prefs_one_per_employee(gen_conn):
    assert _count(gen_conn, "engagement_prefs") == _count(gen_conn, "employees")
    # A demonstrable consent mix (both opted-in and opted-out present).
    consents = {r[0] for r in gen_conn.execute(
        "SELECT DISTINCT monitoring_consent FROM engagement_prefs")}
    assert consents == {0, 1}


def test_collaboration_metadata_is_consent_gated(gen_conn):
    # CORE GATE: not one collaboration row may exist for a non-consenting employee.
    leaked = gen_conn.execute(
        "SELECT COUNT(*) FROM collaboration_metadata WHERE employee_token IN "
        "(SELECT employee_token FROM engagement_prefs WHERE monitoring_consent = 0)"
    ).fetchone()[0]
    assert leaked == 0
    # Consenting employees do get the six monthly ONA snapshots.
    consenting = {r[0] for r in gen_conn.execute(
        "SELECT employee_token FROM engagement_prefs WHERE monitoring_consent = 1")}
    collected = {r[0] for r in gen_conn.execute(
        "SELECT DISTINCT employee_token FROM collaboration_metadata")}
    assert collected and collected <= consenting
    snaps = gen_conn.execute(
        "SELECT MIN(c) FROM (SELECT COUNT(*) c FROM collaboration_metadata "
        "GROUP BY employee_token)").fetchone()[0]
    assert snaps == 6


def test_external_market_is_license_gated(gen_conn):
    rows = gen_conn.execute(
        "SELECT DISTINCT em.employee_token, ec.location FROM external_market em "
        "JOIN employee_core ec ON ec.employee_token = em.employee_token").fetchall()
    assert rows
    for r in rows:
        assert r["location"] in LICENSED_MARKET_REGIONS
    # Unlicensed-region employees get no market data at all.
    unlicensed_with_data = gen_conn.execute(
        "SELECT COUNT(*) FROM external_market WHERE employee_token IN "
        "(SELECT token FROM employees WHERE location NOT IN "
        f"({','.join('?' * len(LICENSED_MARKET_REGIONS))}))",
        tuple(LICENSED_MARKET_REGIONS)).fetchone()[0]
    assert unlicensed_with_data == 0


def test_interventions_placeholder_is_empty(gen_conn):
    # §6 future table: schema present, intentionally not populated.
    assert _count(gen_conn, "interventions") == 0
