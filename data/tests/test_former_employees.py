"""People who have left must not appear in current-state views.

The synthetic generator keeps leavers in the database (they are the attrition labels), so
every reader that describes *today's* workforce has to drop status 'terminated' — otherwise
someone who left a year ago still tops the flight-risk Watchlist. Historical views
(turnover, risk-vs-attrition) keep them on purpose.

Seed: the shared conftest cast, plus canonical `employee_core` rows where T_ADA (flight
risk 85, the highest in technology) has left.
"""
from __future__ import annotations

import pytest

from data import analytics, bias_audit, identity, manual_ingest, query, surveys
from data.tests.conftest import T_ADA, T_BO, T_CY, T_OPS_HEAD, T_TECH_HEAD

_CORE = [
    # token,        division,     level, status,       gender
    (T_TECH_HEAD, "technology", 6, "active", "female"),
    (T_ADA, "technology", 2, "terminated", "female"),
    (T_BO, "technology", 3, "active", "male"),
    (T_OPS_HEAD, "operations", 6, "on_leave", "male"),
    (T_CY, "operations", 1, "active", "female"),
]


@pytest.fixture()
def db(conn):
    for token, division, level, status, gender in _CORE:
        conn.execute(
            "INSERT INTO employee_core (employee_token, role, title, level, division, team, "
            "location, employment_type, hire_date, status, gender) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (token, "Analyst", "Analyst", level, division, "Team", "New York", "full_time",
             "2020-01-01", status, gender))
        conn.execute(
            "INSERT INTO capital_metrics (employee_token, as_of_date, metric, amount, unit, "
            "is_estimate) VALUES (?,?,?,?,?,?)",
            (token, "2026-06-01", "cost_to_lose", 100000.0, "USD", 1))
    conn.execute(
        "INSERT INTO label_attrition (employee_token, attrition, term_date, voluntary, regretted) "
        "VALUES (?,?,?,?,?)", (T_ADA, 1, "2025-09-15", 1, 1))
    conn.commit()
    return conn


def _tokens(rows):
    return {r["token"] for r in rows}


def test_employee_list_excludes_leavers_by_default(db, admin):
    rows = query.get_employees(db, actor=admin)
    assert T_ADA not in _tokens(rows)
    # on_leave people still work here
    assert T_OPS_HEAD in _tokens(rows)
    assert {r["status"] for r in rows} == {"active", "on_leave"}


def test_employee_list_can_include_leavers(db, admin):
    rows = query.get_employees(db, actor=admin, include_former=True)
    ada = next(r for r in rows if r["token"] == T_ADA)
    assert ada["status"] == "terminated"


def test_high_risk_filter_does_not_return_leavers(db, admin):
    rows = query.get_employees(db, actor=admin, risk_band="high")
    assert _tokens(rows) == {T_OPS_HEAD}


def test_tokens_without_a_canonical_row_count_as_current(conn, admin):
    # The shared seed has no employee_core rows at all; nobody should disappear.
    assert len(query.get_employees(conn, actor=admin)) == 5


def test_dashboard_counts_exclude_leavers(db, admin):
    head = query.get_dashboard_metrics(db, actor=admin)["headline"]
    assert head["total_scored"] == 4
    assert head["high_risk_count"] == 1          # T_OPS_HEAD only; T_ADA has left
    quadrant = {p["token"] for p in query.get_dashboard_metrics(db, actor=admin)["quadrant"]}
    assert T_ADA not in quadrant


def test_people_search_excludes_leavers_unless_status_requested(db, admin):
    assert T_ADA not in _tokens(query.search_people(db, actor=admin, criteria={})["matched"])
    hits = query.search_people(db, actor=admin, criteria={"status": "terminated"})["matched"]
    assert _tokens(hits) == {T_ADA}


def test_team_pulse_excludes_leavers(db, tech_manager):
    pulse = query.get_team_pulse(db, actor=tech_manager)
    assert pulse["team_size"] == 2               # tech head + Bo; Ada has left


def test_person_picker_excludes_leavers(db, admin):
    hits = identity.search_identities(db, "ada", actor=admin)
    assert hits == []
    # ...but a former employee's name still resolves where they appear historically.
    assert identity.resolve_names(db, [T_ADA], actor=admin) == {T_ADA: "Ada Lovelace"}


def test_survey_audience_excludes_leavers(db, admin):
    tokens = surveys.resolve_audience(db, actor=admin, audience={"division": "technology"})
    assert T_ADA not in tokens and T_BO in tokens


def test_import_division_headcount_excludes_leavers(db):
    counts = {d["name"]: d["headcount"] for d in manual_ingest.list_divisions(db)}
    assert counts["technology"] == 2


def test_turnover_keeps_leavers_but_headcount_is_current(db, admin):
    head = analytics.get_turnover(db, actor=admin)["headline"]
    assert head["leavers"] == 1
    assert head["cohort_size"] == 5
    assert head["headcount"] == 4
    assert head["attrition_rate"] == 0.2


def test_cost_and_forecast_exclude_leavers(db, admin):
    cost = analytics.get_cost(db, actor=admin)
    assert T_ADA not in {p["token"] for p in cost["scenario"]}
    # 4 current employees x 100k cost-to-lose x their flight risk (20, 55, 75, 30)
    assert cost["total_at_risk"] == pytest.approx(100000 * (0.20 + 0.55 + 0.75 + 0.30))
    forecast = analytics.get_forecast(db, actor=admin)
    assert forecast["expected_leavers"]["value"] == pytest.approx(0.20 + 0.55 + 0.75 + 0.30)


def test_drivers_compare_leavers_against_current_staff(db, admin):
    res = analytics.get_drivers(db, actor=admin)
    assert res["risk_vs_attrition"]["leaver_mean_flight_risk"] == 85
    # the risk distribution only counts the 4 current employees
    assert sum(b["count"] for b in res["distribution"]) == 4


def test_bias_audit_counts_current_employees_only(db, admin):
    cohorts = bias_audit.get_bias_audit_cohort(db, "gender", actor=admin)["cohorts"]
    by = {c["cohort"]: c["count"] for c in cohorts}
    assert by == {"female": 2, "male": 2}
