"""Tests for the query seam: filters, permission scoping, audit, token-only output."""
from __future__ import annotations

import pytest

from data.query import (
    QueryValidationError,
    get_dashboard_metrics,
    get_employee_history,
    get_employees,
)
from .conftest import T_ADA, T_CY, T_OPS_HEAD


def test_admin_sees_all_divisions(conn, admin):
    rows = get_employees(conn, actor=admin)
    assert len(rows) == 5
    assert {r["division"] for r in rows} == {"technology", "operations"}


def test_manager_scoped_to_own_division(conn, tech_manager):
    rows = get_employees(conn, actor=tech_manager)
    assert {r["division"] for r in rows} == {"technology"} and len(rows) == 3


def test_manager_cannot_widen_scope(conn, tech_manager):
    rows = get_employees(conn, actor=tech_manager, division="operations")
    assert all(r["division"] == "technology" for r in rows)


def test_query_seam_returns_tokens_not_names(conn, admin):
    rows = get_employees(conn, actor=admin)
    for r in rows:
        assert r["token"].startswith("emp_")
        assert "display_name" not in r and "full_name" not in r


def test_risk_band_filter(conn, admin):
    high = {r["token"] for r in get_employees(conn, actor=admin, risk_band="high")}
    assert high == {T_ADA, T_OPS_HEAD}


def test_comp_gap_threshold(conn, admin):
    underpaid = {r["token"] for r in get_employees(conn, actor=admin, comp_gap=0.2)}
    assert underpaid == {T_ADA, T_OPS_HEAD}


def test_combined_high_risk_and_underpaid(conn, admin):
    rows = get_employees(conn, actor=admin, risk_band="high", comp_gap=0.2)
    assert {r["token"] for r in rows} == {T_ADA, T_OPS_HEAD}


def test_sort_and_limit(conn, admin):
    rows = get_employees(conn, actor=admin, sort="flight_risk:desc", limit=2)
    assert [r["token"] for r in rows] == [T_ADA, T_OPS_HEAD]


def test_invalid_inputs_rejected(conn, admin):
    with pytest.raises(QueryValidationError):
        get_employees(conn, actor=admin, risk_band="critical")
    with pytest.raises(QueryValidationError):
        get_employees(conn, actor=admin, comp_gap=5.0)
    with pytest.raises(QueryValidationError):
        get_employees(conn, actor=admin, sort="full_name;DROP TABLE employees:asc")


def test_record_joins_score_and_reason_codes(conn, admin):
    row = next(r for r in get_employees(conn, actor=admin) if r["token"] == T_ADA)
    assert row["latest_score"]["flight_risk"] == 85
    assert row["reason_codes"][0]["label"] == "comp_below_band"
    assert row["features"]["comp_gap"] == 0.25


def test_history_permission_denied_across_division(conn, tech_manager):
    assert get_employee_history(conn, T_CY, actor=tech_manager) == {}
    rec = get_employee_history(conn, T_ADA, actor=tech_manager)
    assert rec["token"] == T_ADA and len(rec["history"]) == 1


def test_dashboard_metrics_scoped(conn, tech_manager):
    m = get_dashboard_metrics(conn, actor=tech_manager)
    assert m["headline"]["total_scored"] == 3
    assert m["headline"]["act_now_count"] == 1  # Ada only
    assert all(d["division"] == "technology" for d in m["by_division"])
    assert all("token" in p and "display_name" not in p for p in m["quadrant"])
