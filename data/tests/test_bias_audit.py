"""Phase 7 — the bias-audit gateway (data/bias_audit.py).

This is the sole read path to the walled protected traits. The tests pin its three
guarantees: it only serves the bias-audit wall set (rejecting any other column), every
read is permission-scoped and audited, and — structurally — `model_service` contains no
import of it, so the scorer physically cannot reach a protected trait.
"""
from __future__ import annotations

import pathlib

import pytest

from data import bias_audit, canonical
from data.audit import verify_chain


def test_only_bias_wall_traits_are_auditable():
    assert set(bias_audit.list_auditable_traits()) == canonical.BIAS_AUDIT_COLUMNS


def test_non_bias_trait_is_rejected(conn, admin):
    for bad in ("role", "level", "school_prestige", "named_employer"):
        with pytest.raises(ValueError):
            bias_audit.get_bias_audit_cohort(conn, bad, actor=admin)


def test_bad_metric_is_rejected(conn, admin):
    with pytest.raises(ValueError):
        bias_audit.get_bias_audit_cohort(conn, "gender", actor=admin, metric="salary")


def test_cohort_aggregation_groups_by_trait(conn, admin):
    # Seed two cohorts in employee_core with known scores; assert per-cohort means.
    conn.execute("INSERT INTO employee_core (employee_token, role, level, division, team, "
                 "location, employment_type, hire_date, status, gender) VALUES "
                 "('emp_tech00000002','SWE',2,'technology','Tech Team A','New York',"
                 "'full_time','2020-01-01','active','female')")
    conn.execute("INSERT INTO employee_core (employee_token, role, level, division, team, "
                 "location, employment_type, hire_date, status, gender) VALUES "
                 "('emp_tech00000003','SWE',3,'technology','Tech Team A','New York',"
                 "'full_time','2020-01-01','active','male')")
    conn.commit()
    out = bias_audit.get_bias_audit_cohort(conn, "gender", actor=admin)
    by_cohort = {c["cohort"]: c for c in out["cohorts"]}
    assert out["trait"] == "gender" and out["metric"] == "flight_risk"
    # T_ADA (female) flight_risk 85, T_BO (male) 55 from the seed scores.
    assert by_cohort["female"]["mean"] == 85.0
    assert by_cohort["male"]["mean"] == 55.0


def test_read_is_scoped_and_audited(conn, admin, tech_manager):
    # Non-admin gets an empty cohort set...
    denied = bias_audit.get_bias_audit_cohort(conn, "gender", actor=tech_manager)
    assert denied["cohorts"] == []
    # ...and every call (granted or denied) leaves an intact audit trail.
    bias_audit.get_bias_audit_cohort(conn, "marital_status", actor=admin)
    rows = conn.execute(
        "SELECT COUNT(*) FROM audit_log WHERE action = 'bias_audit_cohort'").fetchone()[0]
    assert rows >= 2
    assert verify_chain(conn)


def test_model_service_never_imports_the_bias_gateway():
    # The wall is physical: no scorer module may import the protected-trait gateway.
    root = pathlib.Path(__file__).resolve().parents[2] / "model_service"
    offenders = [p for p in root.rglob("*.py")
                 if "bias_audit" in p.read_text() and "test" not in p.name]
    assert not offenders, f"model_service must not import bias_audit: {offenders}"
