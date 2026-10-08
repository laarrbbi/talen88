"""Tests for the Employee-360 read (Layer 1): join, scope, audit, consent gate."""
from __future__ import annotations

from data.audit import verify_chain
from data.query import get_employee_360

from .conftest import T_ADA, T_BO, T_CY, T_TECH_HEAD


def test_360_returns_core_attrs_and_skills(conn, admin):
    rec = get_employee_360(conn, T_TECH_HEAD, actor=admin)
    assert rec["token"] == T_TECH_HEAD
    assert rec["division"] == "technology"
    assert rec["attributes"]["perf_rating"] == 4.6
    assert rec["attributes"]["span_of_control"] == 6
    # skills are joined to their catalog names + family, sorted by proficiency desc.
    names = [s["name"] for s in rec["skills"]]
    assert "People Leadership" in names and "Python" in names
    assert rec["skills"][0]["proficiency"] >= rec["skills"][-1]["proficiency"]


def test_360_engagement_gated_by_consent(conn, admin):
    # T_BO withheld consent -> engagement_pulse must be suppressed (no covert signal).
    bo = get_employee_360(conn, T_BO, actor=admin)
    assert bo["attributes"]["consented_signals"] == 0
    assert bo["attributes"]["engagement_pulse"] is None
    # T_ADA consented -> engagement is present.
    ada = get_employee_360(conn, T_ADA, actor=admin)
    assert ada["attributes"]["engagement_pulse"] == 65


def test_360_manager_scope_enforced(conn, tech_manager):
    # A technology manager cannot read an operations employee.
    assert get_employee_360(conn, T_CY, actor=tech_manager) == {}
    # but can read someone in their own division.
    assert get_employee_360(conn, T_ADA, actor=tech_manager)["token"] == T_ADA


def test_360_audited_and_chain_intact(conn, admin):
    get_employee_360(conn, T_ADA, actor=admin)
    n = conn.execute(
        "SELECT COUNT(*) FROM audit_log WHERE action='get_employee_360'").fetchone()[0]
    assert n == 1
    assert verify_chain(conn) is True


def test_360_denied_read_is_audited(conn, tech_manager):
    get_employee_360(conn, T_CY, actor=tech_manager)  # cross-division -> denied
    row = conn.execute(
        "SELECT filters_json FROM audit_log WHERE action='get_employee_360'").fetchone()
    assert '"denied": true' in row["filters_json"]


def test_360_profile_block_nulls_without_canonical_row(conn, admin):
    # The operational seed carries no canonical rows -> the profile block is present
    # with a consistent shape (null scalars, empty lists), never a crash or missing key.
    p = get_employee_360(conn, T_ADA, actor=admin)["profile"]
    assert p["title"] is None and p["pay_band"] is None and p["tenure_years"] is None
    assert p["certifications"] == [] and p["licenses"] == [] and p["promotions"] == []


def test_360_profile_block_enriches_from_canonical_tables(conn, admin):
    # Seed the GOLD-layer canonical record for T_ADA and assert the 360 surfaces the
    # privacy-safe enrichment (certs, licenses, education, experience, comp percentile,
    # tenure, promotions) — all token-only, no bias/identity-proxy columns.
    conn.execute(
        "INSERT INTO employee_core (employee_token, role, title, level, division, team, "
        "location, employment_type, hire_date, status) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (T_ADA, "SWE", "Software Engineer II", 2, "technology", "Tech Team A",
         "New York", "full_time", "2020-01-01", "active"))
    conn.execute(
        "INSERT INTO skills_credentials (employee_token, education_level, education_field, "
        "certifications, licenses, training_times_last_year) VALUES (?,?,?,?,?,?)",
        (T_ADA, "master", "technical_degree", '["CFA", "AWS Solutions Architect"]',
         '[{"name": "Series 7", "expiry": "2027-03-01"}]', 4))
    conn.execute("INSERT INTO cv_trajectory (employee_token, years_of_experience) "
                 "VALUES (?,?)", (T_ADA, 8))
    conn.execute("INSERT INTO compensation (employee_token, base_salary, currency, "
                 "pay_percentile_in_role, pay_band) VALUES (?,?,?,?,?)",
                 (T_ADA, 150000.0, "USD", 62.0, "L2"))
    conn.execute("INSERT INTO career_mobility (employee_token, tenure, years_in_current_role, "
                 "total_working_years, time_since_last_promotion, promotions) "
                 "VALUES (?,?,?,?,?,?)",
                 (T_ADA, 6.4, 2.1, 8.0, 14,
                  '[{"date": "2024-01-01", "from": "Junior SWE", "to": "SWE"}]'))
    conn.commit()

    p = get_employee_360(conn, T_ADA, actor=admin)["profile"]
    assert p["title"] == "Software Engineer II"
    assert p["status"] == "active"
    assert p["education_level"] == "master"
    assert p["certifications"] == ["CFA", "AWS Solutions Architect"]
    assert p["licenses"][0]["name"] == "Series 7"
    assert p["years_of_experience"] == 8
    assert p["pay_percentile_in_role"] == 62.0
    assert p["pay_band"] == "L2"
    assert p["tenure_years"] == 6.4
    assert p["months_since_promotion"] == 14
    assert p["promotions"][0]["to"] == "SWE"


def test_360_profile_never_reads_bias_or_proxy_columns(conn, admin):
    # Even if the canonical row carries bias-audit / identity-proxy values, the 360
    # profile block must not surface them (the wall holds on the individual read).
    conn.execute(
        "INSERT INTO employee_core (employee_token, role, level, division, team, location, "
        "employment_type, hire_date, status, birth_year_band, gender, marital_status) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (T_ADA, "SWE", 2, "technology", "Tech Team A", "New York", "full_time",
         "2020-01-01", "active", "1990-1999", "female", "single"))
    conn.commit()
    p = get_employee_360(conn, T_ADA, actor=admin)["profile"]
    flat = repr(p)
    assert "1990-1999" not in flat and "female" not in flat and "single" not in flat
    assert "birth_year_band" not in p and "gender" not in p and "marital_status" not in p


def test_360_surfaces_panel_and_capital(conn, admin):
    # Seed a panel + capital row for T_ADA so the 360 read returns them.
    date = "2026-06-01"
    conn.execute("INSERT INTO metric_scores (employee_token, as_of_date, metric, score) "
                 "VALUES (?,?,?,?)", (T_ADA, date, "skills_depth", 72))
    conn.execute("INSERT INTO reason_codes (employee_token, as_of_date, metric, label, "
                 "direction, weight) VALUES (?,?,?,?,?,?)",
                 (T_ADA, date, "skills_depth", "deep_expertise", "increases", 1.0))
    conn.execute("INSERT INTO capital_metrics (employee_token, as_of_date, metric, amount, "
                 "unit, is_estimate) VALUES (?,?,?,?,?,?)",
                 (T_ADA, date, "cost_to_lose", 250000.0, "USD", 1))
    conn.commit()
    rec = get_employee_360(conn, T_ADA, actor=admin)
    panel = {m["metric"]: m for m in rec["panel"]}
    assert panel["skills_depth"]["score"] == 72
    assert panel["skills_depth"]["reason_codes"][0]["label"] == "deep_expertise"
    cap = {c["metric"]: c for c in rec["capital"]}
    assert cap["cost_to_lose"]["is_estimate"] == 1
    assert rec["history"]  # retention trend present
