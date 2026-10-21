"""Scoring v2: peer-relative factors, consent gating, calibration, explanations."""
from __future__ import annotations

import random

import pytest
from fastapi.testclient import TestClient

from data import canonical
from model_service.service import app
from model_service.v2 import V2Scorer, load_weights
from model_service.v2.catalog import F, INPUT_FIELDS, PANEL

ENGAGEMENT = ("engagement_score", "engagement_trend", "enps_score", "job_satisfaction",
              "work_life_balance", "survey_sentiment", "recognition_count",
              "manager_effectiveness_score")


def _person(i: int, rng: random.Random, **over) -> dict:
    row = {
        "employee_token": f"emp_{i:04d}", "level": rng.choice([2, 3]), "division": "technology",
        "comp_gap": rng.uniform(0, 0.3), "months_since_promotion": rng.randint(1, 48),
        "tenure_months": rng.randint(6, 120), "manager_changes_12mo": rng.randint(0, 2),
        "perf_rating": rng.uniform(2.5, 4.8), "consented_signals": 1, "monitoring_consent": 1,
        "pay_percentile_in_role": rng.uniform(0.05, 0.95), "months_since_raise": rng.randint(1, 30),
        "last_raise_pct": rng.uniform(1, 15), "promotion_velocity": rng.uniform(0.1, 1.5),
        "years_in_current_role": rng.uniform(0.5, 6), "internal_applications_12mo": 0,
        "engagement_score": rng.uniform(30, 90), "engagement_trend": rng.uniform(-3, 3),
        "enps_score": rng.uniform(-50, 60), "job_satisfaction": rng.randint(1, 5),
        "work_life_balance": rng.randint(1, 5), "survey_sentiment": rng.uniform(-0.5, 0.8),
        "recognition_count": rng.randint(0, 8), "manager_effectiveness_score": rng.uniform(2, 5),
        "team_turnover_rate": rng.uniform(0, 0.2), "peers_departed_recently": rng.randint(0, 1),
        "manager_rating": rng.uniform(3, 5), "skill_market_demand": rng.random(),
        "industry_hiring_heat": rng.random(), "skill_uniqueness": rng.random(),
        "unvested_equity_usd": rng.uniform(0, 200_000), "pto_used": rng.uniform(5, 25),
        "overtime": rng.random(), "commute_km": rng.uniform(1, 50),
        "goal_attainment": rng.uniform(0.6, 1.2), "skill_count": rng.randint(2, 9),
        "skill_avg_prof": rng.uniform(1, 5), "skill_family_breadth": rng.randint(1, 4),
        "learning_hours_12mo": rng.randint(0, 60), "span_of_control": 0, "reference": True,
    }
    row.update(over)
    return row


@pytest.fixture()
def cohort() -> list[dict]:
    rng = random.Random(3)
    return [_person(i, rng) for i in range(120)]


def _by_token(results):
    return {r.employee_token: r for r in results}


def test_outputs_in_range(cohort):
    for r in V2Scorer().score(cohort):
        assert 0 <= r.flight_risk <= 100 and 0 <= r.value_score <= 100
        assert 0 < r.leave_probability < 1
        assert 0 <= r.risk_percentile <= 100 and 0 <= r.confidence <= 100
        assert r.scorer_version == "talent88-v2.0"


def test_calibrates_to_the_base_rate(cohort):
    for rate in (0.08, 0.15):
        res = V2Scorer().score(cohort, base_rate=rate)
        assert sum(r.leave_probability for r in res) / len(res) == pytest.approx(rate, abs=1e-3)


def test_leavers_do_not_shape_the_calibration(cohort):
    base = V2Scorer().score(cohort)
    extra = [_person(1000 + i, random.Random(i), comp_gap=0.3, reference=False) for i in range(40)]
    with_leavers = _by_token(V2Scorer().score(cohort + extra))
    for r in base:
        assert with_leavers[r.employee_token].leave_probability == pytest.approx(r.leave_probability)


def test_worse_pay_means_more_risk(cohort):
    target = dict(cohort[0], employee_token="emp_target")
    low = _by_token(V2Scorer().score(cohort + [dict(target, comp_gap=0.0, pay_percentile_in_role=0.9)]))
    high = _by_token(V2Scorer().score(cohort + [dict(target, comp_gap=0.3, pay_percentile_in_role=0.1)]))
    assert high["emp_target"].leave_probability > low["emp_target"].leave_probability


def test_index_bands_follow_probability():
    s = V2Scorer()
    anchors = load_weights()["flight_risk"]["index_anchors"]
    assert s._index(0.35, anchors) == 70 and s._index(0.15, anchors) == 40
    assert s._index(0.0, anchors) == 0 and s._index(1.0, anchors) == 100
    probs = [i / 100 for i in range(101)]
    idx = [s._index(p, anchors) for p in probs]
    assert idx == sorted(idx)


def test_engagement_ignored_without_consent(cohort):
    """Without consent, engagement values must not change the score, and the explanation
    must not mention engagement."""
    target = dict(cohort[0], employee_token="emp_t", consented_signals=0)
    a = _by_token(V2Scorer().score(cohort + [dict(target, engagement_score=5, engagement_trend=-5)]))
    b = _by_token(V2Scorer().score(cohort + [dict(target, engagement_score=95, engagement_trend=5)]))
    assert a["emp_t"].leave_probability == b["emp_t"].leave_probability
    assert all("engagement" not in rc["label"] for rc in a["emp_t"].reason_codes)
    panel = {m["metric"]: m for m in a["emp_t"].metrics}
    assert panel["engagement_productivity"]["score"] is None


def test_engagement_explanations_never_reveal_answers(cohort):
    for r in V2Scorer().score(cohort):
        for rc in r.reason_codes:
            if rc["label"] in ("low_engagement_signals", "strong_engagement"):
                assert rc["detail"].endswith("(from data this person agreed to share)")
                assert not any(ch.isdigit() for ch in rc["detail"])


def test_missing_data_is_not_scored_as_bad(cohort):
    """Dropping a whole family (no engagement data) must not push the score toward risk
    on its own: the remaining families are re-weighted instead."""
    target = dict(cohort[0], employee_token="emp_t")
    for k in ENGAGEMENT:
        target[k] = None
    res = _by_token(V2Scorer().score(cohort + [target]))["emp_t"]
    full = _by_token(V2Scorer().score(cohort))[cohort[0]["employee_token"]]
    assert res.confidence < full.confidence
    assert 0 < res.leave_probability < 1


def test_reason_codes_are_top_three_and_sum_to_one(cohort):
    for r in V2Scorer().score(cohort):
        assert 1 <= len(r.reason_codes) <= 3
        assert sum(rc["weight"] for rc in r.reason_codes) == pytest.approx(1.0, abs=0.01)
        for rc in r.reason_codes:
            assert rc["direction"] in ("increases", "decreases") and rc["detail"]


def test_recent_events_add_risk(cohort):
    target = dict(cohort[0], employee_token="emp_t")
    calm = _by_token(V2Scorer().score(cohort + [target]))["emp_t"]
    shaken = _by_token(V2Scorer().score(cohort + [dict(target, months_since_manager_change=1,
                                                        months_since_internal_application=0)]))["emp_t"]
    assert shaken.leave_probability > calm.leave_probability
    assert any(rc["label"] in ("new_manager", "recent_internal_application")
               for rc in shaken.reason_codes)


def test_small_batch_falls_back_to_fixed_scales():
    rng = random.Random(1)
    res = V2Scorer().score([_person(1, rng), _person(2, rng)])
    assert len(res) == 2 and all(r.risk_percentile is None for r in res)


def test_capital_uses_salary_and_reports_dollars_at_risk(cohort):
    target = dict(cohort[0], employee_token="emp_t", base_salary=100_000, salary_currency="GBP")
    r = _by_token(V2Scorer().score(cohort + [target]))["emp_t"]
    cap = {c["metric"]: c for c in r.capital}
    assert cap["expected_loss"]["amount"] == pytest.approx(
        cap["cost_to_lose"]["amount"] * r.leave_probability, rel=0.01)
    assert cap["cost_to_lose"]["amount"] >= 0.5 * 127_000 - 1    # GBP converted to USD
    assert all(c["is_estimate"] for k, c in cap.items() if k != "value_score")


def test_panel_has_all_eight_scores(cohort):
    r = V2Scorer().score(cohort)[0]
    assert {m["metric"] for m in r.metrics} == set(PANEL) | {"retention_risk"}


def test_weights_are_consistent():
    w = load_weights()
    fams = w["flight_risk"]["families"]
    assert sum(f["weight"] for f in fams.values()) == pytest.approx(1.0)
    for name, fam in fams.items():
        assert sum(fam["factors"].values()) == pytest.approx(1.0), name
        assert set(fam["factors"]) <= set(F), name
    assert sum(f["weight"] for f in w["value"].values()) == pytest.approx(1.0)
    for fam in w["value"].values():
        assert set(fam["factors"]) <= set(F)


def test_scoring_wall_holds_for_v2():
    walled = canonical.SCORING_EXCLUDED_COLUMNS | {"age", "Age", "Gender", "MaritalStatus"}
    assert not (INPUT_FIELDS & walled)
    from model_service.service import FeatureRow
    assert not (set(FeatureRow.model_fields) & walled)


def test_service_returns_v2_fields(cohort):
    client = TestClient(app)
    body = client.post("/score", json={"as_of_date": "2026-06-01", "rows": cohort[:30],
                                       "base_rate": 0.1}).json()
    r = body["results"][0]
    assert body["model"] == "talent88-v2.0"
    assert {"leave_probability", "risk_percentile", "confidence", "scorer_version"} <= set(r)
    assert r["reason_codes"][0]["detail"]
