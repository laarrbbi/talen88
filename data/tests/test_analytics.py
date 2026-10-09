"""Analytics read-layer seam: the 8 aggregate, exploratory GOLD-layer views.

`data/analytics.py` is the additive sibling of the query seam behind the new Analytics
section. These tests assert the cross-cutting guarantees the whole layer rides on —
permission scope (a manager never widens past their own division), minimum-segment-size
suppression (any cohort/segment < 5 is dropped server-side), token-only output (no
name/email crosses the boundary), an audit row per call — plus the honest data-maturity
ladder: a ⚠️ source-gated view only renders live when its source actually has rows for the
scoped cohort, and the 🔧 capability-gated items (intervention-ROI, seasonal forecast,
compa-ratio) return a gated status and NEVER a fabricated number.

The shared conftest fixture seeds only the legacy operational tables; the canonical GOLD
tables this layer reads (`employee_core`, `compensation`, `manager_team`, `engagement`,
`label_attrition`, `career_mobility`, `external_market`) are seeded here deterministically.
"""
from __future__ import annotations

import pytest

from data import analytics
from data.query import QueryValidationError

# Deterministic analytics cohort — 6 technology + 4 operations (operations < 5 so it is
# suppressed from any division segment; gender splits 5 female / 5 male company-wide).
_CAST = [
    # token,         level, division,      manager,        gender
    ("emp_ant01", 3, "technology", None,         "female"),
    ("emp_ant02", 3, "technology", "emp_ant01",  "female"),
    ("emp_ant03", 3, "technology", "emp_ant01",  "female"),
    ("emp_ant04", 3, "technology", "emp_ant01",  "female"),
    ("emp_ant05", 3, "technology", "emp_ant01",  "female"),
    ("emp_ant06", 3, "technology", "emp_ant01",  "male"),
    ("emp_ano01", 1, "operations", None,         "male"),
    ("emp_ano02", 1, "operations", "emp_ano01",  "male"),
    ("emp_ano03", 1, "operations", "emp_ano01",  "male"),
    ("emp_ano04", 1, "operations", "emp_ano01",  "male"),
]
_TECH = [c[0] for c in _CAST if c[2] == "technology"]
_OPS = [c[0] for c in _CAST if c[2] == "operations"]

# Latest score per token (flight_risk, value_score).
_SCORES = {
    "emp_ant01": (20, 90), "emp_ant02": (85, 70), "emp_ant03": (60, 50),
    "emp_ant04": (30, 60), "emp_ant05": (75, 80), "emp_ant06": (40, 55),
    "emp_ano01": (25, 85), "emp_ano02": (80, 40), "emp_ano03": (35, 50), "emp_ano04": (50, 45),
}
_COST = {  # cost_to_lose, USD
    "emp_ant01": 380000, "emp_ant02": 320000, "emp_ant03": 250000, "emp_ant04": 210000,
    "emp_ant05": 300000, "emp_ant06": 190000, "emp_ano01": 120000, "emp_ano02": 95000,
    "emp_ano03": 88000, "emp_ano04": 81000,
}
_SALARY = {
    "emp_ant01": 162000, "emp_ant02": 158000, "emp_ant03": 151000, "emp_ant04": 149000,
    "emp_ant05": 160000, "emp_ant06": 145000, "emp_ano01": 99000, "emp_ano02": 92000,
    "emp_ano03": 90000, "emp_ano04": 88000,
}
# Leavers: 3 across 3 distinct term-date months (admin trend -> ok; tech-only -> 2 months).
_LEAVERS = {
    "emp_ant02": ("2026-03-15", 1, 1, 1.5, "career_growth"),
    "emp_ant03": ("2026-04-10", 0, 0, 4.0, "performance"),
    "emp_ano02": ("2026-05-20", 1, 0, 2.0, "compensation"),
}
_TENURE = {  # active tenure (years)
    "emp_ant01": 8.0, "emp_ant02": 1.5, "emp_ant03": 4.0, "emp_ant04": 3.0, "emp_ant05": 6.0,
    "emp_ant06": 2.0, "emp_ano01": 7.0, "emp_ano02": 2.0, "emp_ano03": 5.0, "emp_ano04": 1.0,
}
_SNAP_MONTHS = ["2026-01-01", "2026-02-01", "2026-03-01", "2026-04-01", "2026-05-01", "2026-06-01"]
_LATEST = "2026-06-01"


def _seed_analytics(conn) -> None:
    for token, level, division, manager, gender in _CAST:
        conn.execute(
            "INSERT INTO employees (token, role, level, division, team, manager_token, "
            "location, hire_date, employment_type) VALUES (?,?,?,?,?,?,?,?,?)",
            (token, "Analyst", level, division, "Team", manager, "New York", "2018-01-01", "full_time"),
        )
        conn.execute(
            "INSERT INTO employee_core (employee_token, role, title, level, division, team, "
            "manager_token, location, employment_type, hire_date, status, gender) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (token, "Analyst", "Analyst", level, division, "Team", manager, "New York",
             "full_time", "2018-01-01", "active", gender),
        )
        fr, vs = _SCORES[token]
        conn.execute(
            "INSERT INTO scores (employee_token, as_of_date, flight_risk, value_score, risk_trend) "
            "VALUES (?,?,?,?,?)", (token, _LATEST, fr, vs, 2))
        for label, direction, weight in [("comp_below_band", "increases", 0.40),
                                         ("tenure_cliff", "increases", 0.25),
                                         ("strong_engagement", "decreases", 0.18)]:
            conn.execute(
                "INSERT INTO reason_codes (employee_token, as_of_date, metric, label, direction, "
                "weight) VALUES (?,?,?,?,?,?)",
                (token, _LATEST, "retention_risk", label, direction, weight))
        conn.execute(
            "INSERT INTO capital_metrics (employee_token, as_of_date, metric, amount, unit, "
            "is_estimate) VALUES (?,?,?,?,?,?)",
            (token, _LATEST, "cost_to_lose", _COST[token], "USD", 1))
        conn.execute(
            "INSERT INTO compensation (employee_token, base_salary, currency, pay_band, "
            "pay_percentile_in_role, comp_gap_vs_market) VALUES (?,?,?,?,?,?)",
            (token, _SALARY[token], "USD", "B3", 55.0, 0.12))
        tenure = _TENURE[token]
        exit_tenure = _LEAVERS[token][3] if token in _LEAVERS else None
        conn.execute(
            "INSERT INTO career_mobility (employee_token, tenure, tenure_at_exit) VALUES (?,?,?)",
            (token, tenure, exit_tenure))

    for token, (term, vol, reg, _exit, reason) in _LEAVERS.items():
        conn.execute(
            "INSERT INTO label_attrition (employee_token, attrition, term_date, voluntary, "
            "regretted, leaver_reason) VALUES (?,?,?,?,?,?)",
            (token, 1, term, vol, reg, reason))

    # manager_team: one healthy span (kept), one small span (suppressed, < 5).
    conn.execute(
        "INSERT INTO manager_team (manager_token, manager_tenure, manager_performance_rating, "
        "span_of_control, team_turnover_rate, peers_departed_recently) VALUES (?,?,?,?,?,?)",
        ("emp_ant01", 6.0, 4.2, 6, 0.20, 1))
    conn.execute(
        "INSERT INTO manager_team (manager_token, manager_tenure, manager_performance_rating, "
        "span_of_control, team_turnover_rate, peers_departed_recently) VALUES (?,?,?,?,?,?)",
        ("emp_ano01", 5.0, 3.0, 3, 0.50, 2))

    # Source-gated tables: seed ONLY for the technology cohort, so technology renders live
    # and operations stays locked (needs_source) — the honest data-maturity ladder.
    for token in _TECH:
        conn.execute(
            "INSERT INTO external_market (employee_token, as_of_date, role_comp_benchmark) "
            "VALUES (?,?,?)", (token, _LATEST, 1.0))
        for i, month in enumerate(_SNAP_MONTHS):
            conn.execute(
                "INSERT INTO engagement (employee_token, as_of_date, engagement_score, "
                "enps_score, survey_response_rate) VALUES (?,?,?,?,?)",
                (token, month, 70.0 + i, 10 + i, 0.80))
    conn.commit()


# ---- scope -------------------------------------------------------------------
def test_admin_sees_whole_cohort(conn, admin):
    _seed_analytics(conn)
    res = analytics.get_turnover(conn, actor=admin)
    assert res["headline"]["headcount"] == 10
    assert res["headline"]["leavers"] == 3
    assert res["scope"]["division"] is None


def test_manager_scoped_to_own_division(conn, tech_manager):
    _seed_analytics(conn)
    res = analytics.get_turnover(conn, actor=tech_manager)
    assert res["headline"]["headcount"] == 6           # technology only
    assert res["scope"]["division"] == "technology"


def test_manager_cannot_widen_scope(conn, tech_manager):
    _seed_analytics(conn)
    res = analytics.get_turnover(conn, actor=tech_manager, division="operations")
    assert res["scope"]["division"] == "technology" and res["headline"]["headcount"] == 6


# ---- turnover ----------------------------------------------------------------
def test_turnover_segments_suppress_small_division(conn, admin):
    _seed_analytics(conn)
    res = analytics.get_turnover(conn, actor=admin)
    segs = {s["segment"] for s in res["by_segment"]}
    assert "technology" in segs           # 6 -> kept
    assert "operations" not in segs       # 4 -> suppressed
    assert res["suppressed_segments"] >= 1


def test_turnover_trend_history_gate(conn, admin, tech_manager):
    _seed_analytics(conn)
    # Admin spans 3 distinct term-date months -> ok.
    assert analytics.get_turnover(conn, actor=admin)["trend"]["data_status"] == "ok"
    # Technology has only 2 leaver months -> insufficient_history.
    assert analytics.get_turnover(conn, actor=tech_manager)["trend"]["data_status"] == "insufficient_history"


def test_turnover_survival_curve_present(conn, admin):
    _seed_analytics(conn)
    res = analytics.get_turnover(conn, actor=admin)
    assert res["survival"] and res["survival"][0] == {"t": 0.0, "survival": 1.0}
    assert all(0.0 <= p["survival"] <= 1.0 for p in res["survival"])


def test_turnover_voluntary_split(conn, admin):
    _seed_analytics(conn)
    h = analytics.get_turnover(conn, actor=admin)["headline"]
    assert h["voluntary"] == 2 and h["involuntary"] == 1 and h["regretted"] == 1


# ---- drivers -----------------------------------------------------------------
def test_drivers_top_and_distribution(conn, admin):
    _seed_analytics(conn)
    res = analytics.get_drivers(conn, actor=admin)
    assert res["top_drivers"] and res["top_drivers"][0]["label"] == "comp_below_band"
    assert sum(b["count"] for b in res["distribution"]) == 10
    rv = res["risk_vs_attrition"]
    assert rv["leaver_mean_flight_risk"] is not None and rv["stayer_mean_flight_risk"] is not None


# ---- managers ----------------------------------------------------------------
def test_managers_suppress_small_span(conn, admin):
    _seed_analytics(conn)
    res = analytics.get_managers(conn, actor=admin)
    tokens = {m["manager_token"] for m in res["by_manager"]}
    assert tokens == {"emp_ant01"}                 # span 6 kept; emp_ano01 span 3 suppressed
    assert res["suppressed_managers"] >= 1
    assert any(c["manager_token"] == "emp_ant01" for c in res["contagion"])


def test_managers_scoped(conn, tech_manager):
    _seed_analytics(conn)
    res = analytics.get_managers(conn, actor=tech_manager)
    assert all(m["manager_token"] == "emp_ant01" for m in res["by_manager"])


# ---- fairness ----------------------------------------------------------------
def test_fairness_admin_aggregate_with_ratio(conn, admin):
    _seed_analytics(conn)
    res = analytics.get_fairness(conn, actor=admin)
    assert res["restricted"] is False
    gender = next(t for t in res["traits"] if t["trait"] == "gender")
    cohorts = {c["cohort"] for c in gender["cohorts"]}
    assert cohorts == {"female", "male"}           # 5 each -> both kept
    assert gender["adverse_impact_ratio"] is not None
    assert gender["four_fifths_pass"] in (True, False)


def test_fairness_non_admin_restricted(conn, tech_manager):
    _seed_analytics(conn)
    res = analytics.get_fairness(conn, actor=tech_manager)
    assert res["restricted"] is True
    assert all(t["cohorts"] == [] for t in res["traits"])


def test_fairness_never_exposes_raw_trait_values(conn, admin):
    _seed_analytics(conn)
    res = analytics.get_fairness(conn, actor=admin)
    for trait in res["traits"]:
        for cohort in trait["cohorts"]:
            assert set(cohort.keys()) <= {"cohort", "count", "mean"}


# ---- cost --------------------------------------------------------------------
def test_cost_at_risk_and_scenario(conn, admin):
    _seed_analytics(conn)
    res = analytics.get_cost(conn, actor=admin)
    assert res["total_at_risk"] > 0
    assert res["scenario"] and res["scenario"][0]["cost_at_risk"] >= res["scenario"][-1]["cost_at_risk"]
    segs = {s["segment"] for s in res["by_division"]}
    assert "technology" in segs and "operations" not in segs   # operations suppressed


def test_cost_intervention_roi_locked_never_numeric(conn, admin):
    _seed_analytics(conn)
    roi = analytics.get_cost(conn, actor=admin)["intervention_roi"]
    assert roi == {"data_status": "needs_source:interventions"}
    assert "value" not in roi and "amount" not in roi and "roi" not in roi


# ---- compensation ------------------------------------------------------------
def test_compensation_market_gate_by_source(conn, admin, tech_manager):
    _seed_analytics(conn)
    # Technology cohort HAS an external_market feed -> live.
    assert analytics.get_compensation(conn, actor=tech_manager)["market"]["data_status"] == "ok"
    # Operations cohort has no benchmark feed -> locked.
    ops = analytics.get_compensation(conn, actor=admin, division="operations")
    assert ops["market"]["data_status"] == "needs_source:external_market"


def test_compensation_compa_ratio_needs_field(conn, admin):
    _seed_analytics(conn)
    cr = analytics.get_compensation(conn, actor=admin)["compa_ratio"]
    assert cr["data_status"] == "needs_field:compa_ratio"
    assert cr["fallback"] == "pay_percentile_in_role"


def test_compensation_by_level_suppression(conn, admin):
    _seed_analytics(conn)
    res = analytics.get_compensation(conn, actor=admin)
    levels = {s["segment"] for s in res["by_level"]}
    assert "L3" in levels                  # 6 technology at level 3 -> kept
    assert "L1" not in levels              # 4 operations at level 1 -> suppressed


# ---- engagement --------------------------------------------------------------
def test_engagement_live_when_source_present(conn, tech_manager):
    _seed_analytics(conn)
    res = analytics.get_engagement(conn, actor=tech_manager)
    assert res["data_status"] == "ok"
    assert res["trend"]["data_status"] == "ok"     # 6 monthly snapshots
    assert res["headline"]["enps_score"] is not None


def test_engagement_locked_without_source(conn, admin):
    _seed_analytics(conn)
    res = analytics.get_engagement(conn, actor=admin, division="operations")
    assert res["data_status"] == "needs_source:engagement"


# ---- forecast ----------------------------------------------------------------
def test_forecast_expected_leavers_estimate(conn, admin):
    _seed_analytics(conn)
    res = analytics.get_forecast(conn, actor=admin)
    assert res["expected_leavers"]["is_estimate"] is True
    assert res["expected_leavers"]["value"] > 0
    assert res["seasonal"]["data_status"] == "insufficient_history"


# ---- token-only + audit + validation ----------------------------------------
def test_no_names_cross_the_boundary(conn, admin):
    _seed_analytics(conn)
    res = {
        "turnover": analytics.get_turnover(conn, actor=admin),
        "drivers": analytics.get_drivers(conn, actor=admin),
        "managers": analytics.get_managers(conn, actor=admin),
        "cost": analytics.get_cost(conn, actor=admin),
    }
    import json
    blob = json.dumps(res)
    assert "full_name" not in blob and "email" not in blob


def test_every_endpoint_is_audited(conn, admin):
    _seed_analytics(conn)
    for fn in (analytics.get_turnover, analytics.get_drivers, analytics.get_managers,
               analytics.get_fairness, analytics.get_cost, analytics.get_compensation,
               analytics.get_engagement, analytics.get_forecast):
        fn(conn, actor=admin)
    actions = {r["action"] for r in conn.execute(
        "SELECT action FROM audit_log WHERE action LIKE 'analytics_%'").fetchall()}
    assert actions == {f"analytics_{n}" for n in
                       ("turnover", "drivers", "managers", "fairness", "cost",
                        "compensation", "engagement", "forecast")}


@pytest.mark.parametrize("kwargs", [
    {"level": 0}, {"level": 99}, {"tenure_band": "nope"}, {"date_range": "bad"},
    {"date_range": "2026-06-01:2026-01-01"},
])
def test_invalid_filters_rejected(conn, admin, kwargs):
    _seed_analytics(conn)
    with pytest.raises(QueryValidationError):
        analytics.get_turnover(conn, actor=admin, **kwargs)
