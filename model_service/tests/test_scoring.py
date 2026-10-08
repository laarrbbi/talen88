"""Tests for the scoring seam: heuristic behavior, reason-code invariants, /score."""
from __future__ import annotations

from fastapi.testclient import TestClient

from model_service.heuristic import HeuristicScorer
from model_service.model_loader import Scorer, load_model
from model_service.service import app

client = TestClient(app)

# A high-risk profile (underpaid, overdue promo, on the tenure cliff, churny mgr)
HIGH = {"employee_token": "emp_high", "comp_gap": 0.30, "months_since_promotion": 36,
        "tenure_months": 24, "manager_changes_12mo": 3, "level": 2, "division": "technology"}
# A low-risk profile (paid well, recently promoted, long tenure, stable mgr)
LOW = {"employee_token": "emp_low", "comp_gap": 0.0, "months_since_promotion": 1,
       "tenure_months": 96, "manager_changes_12mo": 0, "level": 5, "division": "technology"}


def test_loader_returns_scorer():
    assert isinstance(load_model(), Scorer)


def test_scores_in_range():
    for r in HeuristicScorer().score([HIGH, LOW]):
        assert 0 <= r.flight_risk <= 100
        assert 0 <= r.value_score <= 100


def test_high_profile_riskier_than_low():
    high, low = HeuristicScorer().score([HIGH, LOW])
    assert high.flight_risk > low.flight_risk
    assert high.flight_risk >= 70 and low.flight_risk < 40


def test_reason_code_invariants():
    res = HeuristicScorer().score([HIGH])[0]
    assert 1 <= len(res.reason_codes) <= 3
    for rc in res.reason_codes:
        assert rc["direction"] in ("increases", "decreases")
        assert 0.0 <= rc["weight"] <= 1.0
        assert isinstance(rc["label"], str) and rc["label"]
    # weights renormalize to ~1 across reported codes
    assert abs(sum(rc["weight"] for rc in res.reason_codes) - 1.0) < 1e-6


def test_high_risk_drivers_point_up():
    res = HeuristicScorer().score([HIGH])[0]
    labels = {rc["label"]: rc["direction"] for rc in res.reason_codes}
    # comp gap is the dominant driver; it should be present and increasing risk.
    assert labels.get("comp_below_band") == "increases"


def test_value_score_tracks_seniority():
    junior = dict(LOW, level=1, tenure_months=6)
    senior = dict(LOW, level=6, tenure_months=120)
    j, s = HeuristicScorer().score([junior, senior])
    assert s.value_score > j.value_score


def test_score_endpoint_happy_path():
    resp = client.post("/score", json={"as_of_date": "2026-06-01", "rows": [HIGH, LOW]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["model"] == "heuristic-v1"
    assert len(body["results"]) == 2
    assert body["results"][0]["employee_token"] == "emp_high"
    assert "reason_codes" in body["results"][0]


def test_score_endpoint_rejects_bad_input():
    # comp_gap out of range -> 422 from pydantic, no internal leak
    bad = dict(HIGH, comp_gap=9.0)
    resp = client.post("/score", json={"as_of_date": "2026-06-01", "rows": [bad]})
    assert resp.status_code == 422
    # bad date format
    resp = client.post("/score", json={"as_of_date": "june", "rows": [HIGH]})
    assert resp.status_code == 422


def test_health():
    resp = client.get("/health")
    assert resp.status_code == 200 and resp.json()["status"] == "ok"


# ---- Layer 2: the 8-score + capital panel ------------------------------------
# A consented profile carrying the Employee-360 inputs.
PANEL = dict(LOW, perf_rating=4.5, engagement_pulse=82, learning_hours_12mo=40,
             internal_moves=2, span_of_control=6, skill_count=7, skill_avg_prof=4.2,
             skill_family_breadth=3, consented_signals=1)

EXPECTED_SCORES = {"skills_depth", "learning_velocity", "performance_impact",
                   "engagement_productivity", "mobility_readiness", "retention_risk",
                   "trust_reliability", "leadership_influence"}


def test_panel_has_all_eight_scores():
    res = HeuristicScorer().score([PANEL])[0]
    assert {m["metric"] for m in res.metrics} == EXPECTED_SCORES


def test_panel_scores_in_range_and_reasons_valid():
    res = HeuristicScorer().score([PANEL])[0]
    for m in res.metrics:
        if m["score"] is None:
            continue
        assert 0 <= m["score"] <= 100
        assert m["reason_codes"]
        for rc in m["reason_codes"]:
            assert rc["direction"] in ("increases", "decreases")
            assert 0.0 <= rc["weight"] <= 1.0
        # weights renormalize to ~1 (3-decimal rounding tolerance).
        assert abs(sum(rc["weight"] for rc in m["reason_codes"]) - 1.0) < 0.01


def test_engagement_gated_without_consent():
    eng = lambda r: next(m for m in r.metrics if m["metric"] == "engagement_productivity")
    consented = HeuristicScorer().score([PANEL])[0]
    assert eng(consented)["score"] is not None
    withheld = HeuristicScorer().score([dict(PANEL, consented_signals=0)])[0]
    assert eng(withheld)["score"] is None  # no covert engagement signal


def test_capital_metrics_present_and_estimates_flagged():
    res = HeuristicScorer().score([PANEL])[0]
    cap = {c["metric"]: c for c in res.capital}
    assert {"value_score", "cost_to_lose", "suggested_retention_investment",
            "retention_roi", "compensation_efficiency"} <= set(cap)
    assert cap["cost_to_lose"]["unit"] == "USD" and cap["cost_to_lose"]["is_estimate"]
    assert cap["value_score"]["is_estimate"] is False  # an actual score, not a $ estimate


def test_score_endpoint_returns_panel():
    resp = client.post("/score", json={"as_of_date": "2026-06-01", "rows": [PANEL]})
    assert resp.status_code == 200
    row = resp.json()["results"][0]
    assert {m["metric"] for m in row["metrics"]} == EXPECTED_SCORES
    assert any(c["metric"] == "cost_to_lose" for c in row["capital"])
