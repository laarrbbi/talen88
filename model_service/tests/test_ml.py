"""Tests for the trained ML attrition backend (model_service/ml).

Covers: preprocessing + feature engineering, the bias wall (protected attrs absent
from the scoring features), probability->score bounds, reason-code schema, the saved
model's acceptance thresholds, the /score contract being identical in heuristic vs ml
mode, automatic fallback (missing / corrupt artifact, per-row inference error), and
edge cases. The whole module skips cleanly if the optional ML deps are not installed,
so the default (heuristic) test run is unaffected.
"""
from __future__ import annotations

import importlib

import numpy as np
import pytest

# Optional-dep gate: skip the ML suite entirely if the trained backend isn't installed.
pytest.importorskip("lightgbm")
pytest.importorskip("shap")
pytest.importorskip("sklearn")

from model_service.heuristic import HeuristicScorer  # noqa: E402
from model_service.ml import preprocess as P  # noqa: E402
from model_service.ml import train as T  # noqa: E402
from model_service.ml.config import (  # noqa: E402
    AUC_MIN,
    DATASET_PATH,
    LEAVER_RECALL_MIN,
    MODEL_PATH,
)
from model_service.ml.scorer import MLScorer  # noqa: E402

# --- Representative rows (same concepts the heuristic tests use) ----------------
HIGH = {"employee_token": "emp_high", "comp_gap": 0.30, "months_since_promotion": 40,
        "tenure_months": 22, "manager_changes_12mo": 3, "level": 2, "division": "technology",
        "perf_rating": 3.0, "engagement_pulse": 15, "consented_signals": 1,
        "internal_moves": 4, "learning_hours_12mo": 5}
LOW = {"employee_token": "emp_low", "comp_gap": 0.0, "months_since_promotion": 2,
       "tenure_months": 72, "manager_changes_12mo": 0, "level": 5, "division": "finance",
       "perf_rating": 4.5, "engagement_pulse": 92, "consented_signals": 1,
       "internal_moves": 1, "learning_hours_12mo": 40}
PROTECTED = {"Age", "AgeBand", "Gender", "MaritalStatus"}


# --- Fixtures ------------------------------------------------------------------
@pytest.fixture(scope="module")
def spec():
    raw = P.load_raw(DATASET_PATH)
    _, _, _, spec = P.build_training_frame(raw)
    return spec


@pytest.fixture(scope="module")
def trained():
    """Train once per module (stratified split); returns the full validation bundle."""
    return T.train_and_validate()


@pytest.fixture(scope="module")
def ml_scorer(trained):
    model, spec, _threshold, _metrics, _bias = trained
    return MLScorer(model, spec, "lgbm-attrition-v1")


@pytest.fixture(scope="module")
def saved_artifact(trained):
    """Ensure an on-disk artifact exists for the loader/contract/fallback tests."""
    if not MODEL_PATH.exists():
        import joblib
        from dataclasses import asdict
        model, sp, threshold, metrics, bias = trained
        MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump({"model": model, "spec": asdict(sp), "threshold": threshold,
                     "metrics": metrics, "bias": bias, "feature_columns": sp.columns,
                     "model_name": "lgbm-attrition-v1"}, MODEL_PATH)
    return MODEL_PATH


def _client_for(backend, monkeypatch, model_path=None):
    """Reload the service with a given backend and return a TestClient. The reload
    re-runs load_model() under the patched env. Caller's monkeypatch auto-reverts."""
    monkeypatch.setenv("SCORING_BACKEND", backend)
    if model_path is not None:
        monkeypatch.setenv("PULSESCORE_MODEL_PATH", str(model_path))
    import model_service.service as svc
    importlib.reload(svc)
    from fastapi.testclient import TestClient
    return svc, TestClient(svc.app)


# --- Preprocessing + feature engineering ---------------------------------------
def test_build_training_frame_shape_and_label():
    raw = P.load_raw(DATASET_PATH)
    X, y, protected, spec = P.build_training_frame(raw)
    assert list(X.columns) == spec.columns
    assert 0.10 < y.mean() < 0.25            # ~16% leavers
    assert set(y.unique()) <= {0, 1}
    for c in spec.categorical:
        assert str(X[c].dtype) == "category"
    assert {"Gender", "AgeBand"} <= set(protected.columns)


def test_comp_gap_engineering(spec):
    # Below the per-level median -> positive gap; at/above -> clamped to 0.
    med = spec.level_median_income[2]
    import pandas as pd
    df = pd.DataFrame({"MonthlyIncome": [med * 0.7, med, med * 1.5], "JobLevel": [2, 2, 2],
                       "JobSatisfaction": [3, 3, 3], "EnvironmentSatisfaction": [3, 3, 3],
                       "RelationshipSatisfaction": [3, 3, 3], "WorkLifeBalance": [3, 3, 3]})
    out = P.engineer(df, spec.level_median_income, spec.overall_median_income)
    assert out["comp_gap"].iloc[0] > 0.25
    assert out["comp_gap"].iloc[1] == 0.0
    assert out["comp_gap"].iloc[2] == 0.0      # above median clamped, never negative
    assert abs(out["satisfaction_mean"].iloc[0] - 3.0) < 1e-9


def test_row_to_features_missing_fields(spec):
    # An almost-empty row must not raise and must impute (no NaN in numerics).
    fr = P.row_to_features({"employee_token": "e"}, spec)
    assert list(fr.columns) == spec.columns
    assert int(fr[P.NUMERIC_FEATURES].isnull().sum().sum()) == 0


def test_row_to_features_unseen_category(spec):
    # Unseen categorical values become NaN (LightGBM's native 'missing'), not errors.
    import pandas as pd
    X = pd.DataFrame([{**{c: spec.modes[c] for c in spec.categorical},
                       **{n: spec.medians[n] for n in P.NUMERIC_FEATURES}}])
    X["JobRole"] = "Astronaut"               # never in training categories
    aligned = P.align_categoricals(X, spec)
    assert pd.isna(aligned["JobRole"].iloc[0])
    assert list(aligned.columns) == spec.columns


# --- Bias wall (REQUIRED): protected attributes never enter the feature set -----
def test_protected_attrs_absent_from_feature_columns():
    assert PROTECTED.isdisjoint(P.FEATURE_COLUMNS)
    assert PROTECTED.isdisjoint(P.NUMERIC_RAW)
    assert PROTECTED.isdisjoint(P.CATEGORICAL_RAW)


def test_protected_attrs_absent_from_spec_and_inference(spec):
    assert PROTECTED.isdisjoint(spec.columns)
    fr = P.row_to_features(LOW, spec)
    assert PROTECTED.isdisjoint(fr.columns)


def test_saved_artifact_has_no_protected_features(saved_artifact):
    import joblib
    bundle = joblib.load(saved_artifact)
    assert PROTECTED.isdisjoint(bundle["feature_columns"])


# --- Probability -> score mapping (bounds 0-100) -------------------------------
def test_flight_risk_bounds(ml_scorer):
    rows = [HIGH, LOW, dict(HIGH, comp_gap=1.0, level=1), dict(LOW, comp_gap=0.0, level=10)]
    for res in ml_scorer.score(rows):
        assert isinstance(res.flight_risk, int)
        assert 0 <= res.flight_risk <= 100


def test_score_mapping_clips_extreme_probabilities(ml_scorer, monkeypatch):
    # Force out-of-[0,1] "probabilities" -> score must still clamp to [0,100].
    monkeypatch.setattr(ml_scorer.model, "predict_proba",
                        lambda X: np.array([[0.0, 1.5]]))
    assert ml_scorer.score([HIGH])[0].flight_risk == 100
    monkeypatch.setattr(ml_scorer.model, "predict_proba",
                        lambda X: np.array([[1.0, -0.2]]))
    assert ml_scorer.score([HIGH])[0].flight_risk == 0


# --- Reason-code schema (matches the heuristic's {label,direction,weight}) ------
def test_ml_reason_codes_match_schema(ml_scorer):
    res = ml_scorer.score([HIGH])[0]
    assert 1 <= len(res.reason_codes) <= 3
    for rc in res.reason_codes:
        assert isinstance(rc["label"], str) and rc["label"]
        assert rc["direction"] in ("increases", "decreases")
        assert 0.0 <= rc["weight"] <= 1.0
    assert abs(sum(rc["weight"] for rc in res.reason_codes) - 1.0) < 0.01


def test_retention_metric_consistent_with_flight_risk(ml_scorer):
    res = ml_scorer.score([HIGH])[0]
    rr = next(m for m in res.metrics if m["metric"] == "retention_risk")
    assert rr["score"] == res.flight_risk
    assert rr["reason_codes"] == res.reason_codes


def test_ml_only_swaps_flight_risk(ml_scorer):
    """value_score, capital, and the other seven scores stay byte-for-byte heuristic."""
    row = HIGH
    h = HeuristicScorer().score([row])[0]
    m = ml_scorer.score([row])[0]
    assert m.value_score == h.value_score
    assert m.capital == h.capital
    for metric in m.metrics:
        if metric["metric"] == "retention_risk":
            continue
        hm = next(x for x in h.metrics if x["metric"] == metric["metric"])
        assert metric == hm


# --- Saved-model acceptance thresholds (REQUIRED) ------------------------------
def test_trained_model_meets_thresholds(trained):
    _model, _spec, _threshold, metrics, _bias = trained
    assert metrics["roc_auc"] >= AUC_MIN, f"AUC {metrics['roc_auc']:.3f} < {AUC_MIN}"
    assert metrics["leaver_recall"] >= LEAVER_RECALL_MIN, \
        f"leaver recall {metrics['leaver_recall']:.3f} < {LEAVER_RECALL_MIN}"


def test_saved_artifact_metrics_meet_thresholds(saved_artifact):
    import joblib
    m = joblib.load(saved_artifact)["metrics"]
    assert m["roc_auc"] >= AUC_MIN
    assert m["leaver_recall"] >= LEAVER_RECALL_MIN


# --- Seam / contract: identical JSON shape in BOTH modes -----------------------
def _shape(result_row):
    return {
        "top": sorted(result_row.keys()),
        "reason_codes": sorted(result_row["reason_codes"][0].keys()),
        "metric": sorted(result_row["metrics"][0].keys()),
        "capital": sorted(result_row["capital"][0].keys()),
        "n_metrics": len(result_row["metrics"]),
        "metric_names": sorted(m["metric"] for m in result_row["metrics"]),
    }


def test_score_contract_identical_across_modes(saved_artifact, monkeypatch):
    payload = {"as_of_date": "2026-06-01", "rows": [dict(HIGH), dict(LOW)]}
    _svc_h, ch = _client_for("heuristic", monkeypatch)
    bh = ch.post("/score", json=payload).json()
    _svc_m, cm = _client_for("ml", monkeypatch, model_path=saved_artifact)
    bm = cm.post("/score", json=payload).json()

    assert bh["model"] == "heuristic-v1"
    assert bm["model"] == "lgbm-attrition-v1"
    assert len(bh["results"]) == len(bm["results"]) == 2
    for rh, rm in zip(bh["results"], bm["results"]):
        assert _shape(rh) == _shape(rm)       # byte-identical structure


# --- Fallback: the service never crashes because of the model ------------------
def test_missing_artifact_falls_back_to_heuristic(monkeypatch):
    svc, _c = _client_for("ml", monkeypatch, model_path="/tmp/talent88_no_such_model.joblib")
    assert svc._SCORER.name == "heuristic-v1"


def test_corrupt_artifact_falls_back_to_heuristic(tmp_path, monkeypatch):
    bad = tmp_path / "corrupt.joblib"
    bad.write_text("not a real joblib bundle")
    svc, _c = _client_for("ml", monkeypatch, model_path=bad)
    assert svc._SCORER.name == "heuristic-v1"


def test_unknown_backend_uses_heuristic(monkeypatch):
    svc, _c = _client_for("totally-bogus", monkeypatch)
    assert svc._SCORER.name == "heuristic-v1"


def test_per_row_inference_error_keeps_heuristic_value(ml_scorer, monkeypatch):
    """An inference error on a record degrades to that row's heuristic flight_risk
    rather than crashing the batch."""
    expected = HeuristicScorer().score([HIGH])[0].flight_risk

    def boom(_X):
        raise RuntimeError("simulated inference failure")

    monkeypatch.setattr(ml_scorer.model, "predict_proba", boom)
    out = ml_scorer.score([HIGH])
    assert len(out) == 1
    assert out[0].flight_risk == expected     # fell back, did not raise


# --- Edge cases ----------------------------------------------------------------
def test_empty_input(ml_scorer):
    assert ml_scorer.score([]) == []


def test_single_employee(ml_scorer):
    out = ml_scorer.score([LOW])
    assert len(out) == 1 and 0 <= out[0].flight_risk <= 100


def test_missing_fields_row_scores(ml_scorer):
    out = ml_scorer.score([{"employee_token": "emp_sparse", "comp_gap": 0.1,
                            "months_since_promotion": 0, "tenure_months": 12,
                            "manager_changes_12mo": 0, "level": 1, "division": "x"}])
    assert 0 <= out[0].flight_risk <= 100


def test_outlier_and_unseen_category_values(ml_scorer):
    # Extreme magnitudes + an unseen division must not raise; bounds hold.
    weird = {"employee_token": "emp_weird", "comp_gap": 1.0, "months_since_promotion": 600,
             "tenure_months": 600, "manager_changes_12mo": 24, "level": 10,
             "division": "atlantis", "perf_rating": 5.0, "engagement_pulse": 0,
             "consented_signals": 1, "internal_moves": 50, "learning_hours_12mo": 2000}
    out = ml_scorer.score([weird])
    assert 0 <= out[0].flight_risk <= 100


def test_batch_high_and_low_profiles_valid(ml_scorer):
    out = ml_scorer.score([HIGH, LOW])
    assert all(0 <= r.flight_risk <= 100 for r in out)
    assert all(len(r.metrics) == 8 for r in out)
