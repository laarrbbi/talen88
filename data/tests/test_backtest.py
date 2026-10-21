"""Backtest metrics, the fairness helpers, and the v2 feature rows the refresh builds."""
from __future__ import annotations

import dataclasses
import json

import pytest

from data import backtest, bias_audit, refresh
from data.tests.conftest import T_ADA, T_BO, T_CY, T_OPS_HEAD, T_TECH_HEAD
from model_service.model_loader import load_model


# ---- metrics -----------------------------------------------------------------------------

def test_auc_known_values():
    assert backtest.auc([0.9, 0.8, 0.2, 0.1], [1, 1, 0, 0]) == 1.0
    assert backtest.auc([0.1, 0.2, 0.8, 0.9], [1, 1, 0, 0]) == 0.0
    assert backtest.auc([0.5, 0.5, 0.5, 0.5], [1, 0, 1, 0]) == 0.5
    assert backtest.auc([0.3, 0.2], [0, 0]) is None


def test_ranking_metrics():
    scores, labels = [0.9, 0.7, 0.6, 0.2, 0.1], [1, 0, 1, 0, 0]
    assert backtest.average_precision(scores, labels) == pytest.approx((1 + 2 / 3) / 2)
    assert backtest.top_share_capture(scores, labels, share=0.2) == 0.5
    assert backtest.brier([1.0, 0.0], [1, 0]) == 0.0
    cal = backtest.calibration([0.1] * 10 + [0.9] * 10, [0] * 10 + [1] * 10, bins=2)
    assert cal == [{"decile": 1, "n": 10, "predicted": 0.1, "actual": 0.0},
                   {"decile": 2, "n": 10, "predicted": 0.9, "actual": 1.0}]


# ---- fairness helpers (aggregate-only, admin-only, suppressed) ----------------------------

@pytest.fixture()
def cohort_db(conn):
    """12 women, 12 men, 3 non-binary (suppressed: < 5)."""
    rows = [(f"emp_f{i:02d}", "female") for i in range(12)] + \
           [(f"emp_m{i:02d}", "male") for i in range(12)] + \
           [(f"emp_n{i:02d}", "non_binary") for i in range(3)]
    for token, gender in rows:
        conn.execute("INSERT INTO employees (token, role, level, division, team, location, "
                     "hire_date, employment_type) VALUES (?,?,?,?,?,?,?,?)",
                     (token, "Analyst", 2, "technology", "T", "NY", "2020-01-01", "full_time"))
        conn.execute("INSERT INTO employee_core (employee_token, role, level, division, team, "
                     "location, employment_type, hire_date, status, gender) "
                     "VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (token, "Analyst", 2, "technology", "T", "NY", "full_time", "2020-01-01",
                      "active", gender))
    conn.commit()
    return conn, [t for t, _ in rows]


def test_flag_rate_is_aggregate_and_suppresses_small_cohorts(cohort_db, admin):
    conn, tokens = cohort_db
    flagged = {t: t.startswith("emp_f") and int(t[-2:]) < 6 for t in tokens}   # 6/12 women
    res = bias_audit.flag_rate_by_cohort(conn, "gender", flagged, actor=admin)
    assert res["cohorts"] == [{"cohort": "female", "count": 12, "flag_rate": 0.5},
                              {"cohort": "male", "count": 12, "flag_rate": 0.0}]
    assert res["adverse_impact_ratio"] == 0.0 and res["verdict"] == "review"
    assert all(set(c) == {"cohort", "count", "flag_rate"} for c in res["cohorts"])


def test_small_gaps_are_not_called_out(cohort_db, admin):
    conn, tokens = cohort_db
    flagged = {t: t in ("emp_f00", "emp_f01", "emp_m00") for t in tokens}   # 2/12 vs 1/12
    res = bias_audit.flag_rate_by_cohort(conn, "gender", flagged, actor=admin)
    assert res["four_fifths_pass"] is False and res["verdict"] == "gap not significant"


def test_proxy_screen_flags_a_factor_that_tracks_the_trait(cohort_db, admin):
    conn, tokens = cohort_db
    tracks = {t: (10.0 if "_f" in t else 0.0) + int(t[-2:]) * 0.1 for t in tokens}
    noise = {t: float(int(t[-2:]) % 4) for t in tokens}
    assert bias_audit.factor_proxy_screen(conn, "gender", tracks, actor=admin)["needs_review"]
    assert not bias_audit.factor_proxy_screen(conn, "gender", noise, actor=admin)["needs_review"]


def test_fairness_helpers_are_admin_only(cohort_db, tech_manager):
    conn, tokens = cohort_db
    assert bias_audit.flag_rate_by_cohort(conn, "gender", {t: True for t in tokens},
                                          actor=tech_manager)["cohorts"] == []
    assert bias_audit.factor_proxy_screen(conn, "gender", {t: 1.0 for t in tokens},
                                          actor=tech_manager)["cohorts"] == []
    with pytest.raises(ValueError):
        bias_audit.flag_rate_by_cohort(conn, "salary", {}, actor=tech_manager)


# ---- v2 feature rows --------------------------------------------------------------------------

@pytest.fixture()
def canonical_db(conn):
    """Conftest cast + canonical rows: T_BO has not consented (conftest), T_ADA has left."""
    core = [(T_TECH_HEAD, "active"), (T_ADA, "terminated"), (T_BO, "active"),
            (T_OPS_HEAD, "active"), (T_CY, "active")]
    for token, status in core:
        conn.execute("INSERT INTO employee_core (employee_token, role, level, division, team, "
                     "location, employment_type, hire_date, status) VALUES (?,?,?,?,?,?,?,?,?)",
                     (token, "Analyst", 2, "technology", "T", "NY", "full_time", "2020-01-01", status))
        conn.execute("INSERT INTO engagement (employee_token, as_of_date, engagement_score, "
                     "engagement_trend) VALUES (?,?,?,?)", (token, "2026-06-01", 55.0, -2.0))
        conn.execute("INSERT INTO compensation (employee_token, base_salary, currency, "
                     "last_raise_date, vesting_dates, equity_deferred) VALUES (?,?,?,?,?,?)",
                     (token, 120000, "USD", "2025-06-01", json.dumps(["2026-05-01", "2027-05-01"]),
                      json.dumps({"unvested_usd": 50000})))
        conn.execute("INSERT INTO career_mobility (employee_token, internal_application_history) "
                     "VALUES (?,?)", (token, json.dumps([{"date": "2026-04-01"}, {"date": "2024-01-01"}])))
    conn.execute("INSERT INTO label_attrition (employee_token, attrition, term_date) VALUES (?,?,?)",
                 (T_ADA, 1, "2026-03-15"))
    conn.commit()
    return conn


def test_feature_rows_gate_engagement_on_consent(canonical_db):
    rows = {r["employee_token"]: r for r in refresh._feature_rows(canonical_db, "2026-06-01")}
    assert rows[T_CY]["engagement_score"] == 55.0
    assert "engagement_score" not in rows[T_BO]          # T_BO withheld consent


def test_feature_rows_derive_dates_and_leaver_flag(canonical_db):
    rows = {r["employee_token"]: r for r in refresh._feature_rows(canonical_db, "2026-06-01")}
    cy = rows[T_CY]
    assert cy["months_since_raise"] == pytest.approx(12.0, abs=0.2)
    assert cy["months_since_vest"] == pytest.approx(1.0, abs=0.1)
    assert cy["internal_applications_12mo"] == 1
    assert cy["months_since_internal_application"] == pytest.approx(2.0, abs=0.1)
    assert cy["base_salary"] == 120000 and cy["salary_currency"] == "USD"
    assert cy["reference"] is True
    assert rows[T_ADA]["reference"] is False              # left in March
    # Before she left, she was still part of everyone's peer group.
    canonical_db.execute("INSERT INTO feature_snapshots VALUES (?,?,?,?,?,?)",
                         (T_ADA, "2026-01-01", 0.2, 12, 30, 0))
    early = {r["employee_token"]: r for r in refresh._feature_rows(canonical_db, "2026-01-01")}
    assert early[T_ADA]["reference"] is True


def test_refresh_stores_v2_outputs(canonical_db):
    scorer = load_model("v2")

    def batch(as_of, rows, base_rate=None):
        return [dataclasses.asdict(r) for r in scorer.score(rows, base_rate=base_rate)]

    refresh.refresh_scores(canonical_db, score_batch=batch)
    row = canonical_db.execute("SELECT * FROM scores WHERE employee_token = ?", (T_CY,)).fetchone()
    assert row["scorer_version"] == "talent88-v2.0"
    assert 0 < row["leave_probability"] < 1 and row["confidence"] is not None
    detail = canonical_db.execute("SELECT detail FROM reason_codes WHERE employee_token = ? "
                                  "AND metric = 'retention_risk'", (T_CY,)).fetchall()
    assert detail and all(d["detail"] for d in detail)
    loss = canonical_db.execute("SELECT amount FROM capital_metrics WHERE employee_token = ? "
                                "AND metric = 'expected_loss'", (T_CY,)).fetchone()
    assert loss is not None
