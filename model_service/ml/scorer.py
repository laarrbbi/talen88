"""MLScorer — the trained attrition model as a drop-in `Scorer`.

Implements the SAME `score(rows) -> [ScoreResult]` interface the heuristic does, so it
plugs in behind `/score` with zero contract changes. It swaps ONLY the flight-risk
signal:

  * top-level `flight_risk`  <- trained-model probability * 100
  * top-level `reason_codes` <- per-employee SHAP top contributors ({label,direction,weight})
  * the `retention_risk` panel metric (= flight risk) is updated to match

Everything else — `value_score`, the capital/moneyball figures, and the other seven
panel scores — is produced by the heuristic exactly as before (only flight_risk swaps).

FAIL-CLOSED by construction:
  * It delegates to the HeuristicScorer FIRST to build the full result, then overlays
    the ML flight-risk. If ML inference raises on a given row, that row simply keeps
    its heuristic flight-risk (logged) — one bad record can never crash a batch.
  * Construction (`from_artifact`) raises if the artifact is missing/corrupt; the
    model_loader catches that and falls back to the heuristic for the whole service.
"""
from __future__ import annotations

import logging

import joblib
import numpy as np
import shap

from ..heuristic import HeuristicScorer, ScoreResult
from . import preprocess as P
from .config import SCORE_MAX, SCORE_MIN

logger = logging.getLogger("pulsescore.model.ml")

TOP_REASONS = 3   # match the heuristic's reason-code count

# Flight-risk-framed labels per feature: (label when the feature PUSHES risk UP,
# label when it pushes risk DOWN). SHAP sign picks the direction. Features not listed
# fall back to a generic snake-case label with the sign-derived direction.
_FEATURE_LABELS: dict[str, tuple[str, str]] = {
    "comp_gap": ("comp_below_band", "competitive_pay"),
    "MonthlyIncome": ("low_pay", "strong_pay"),
    "YearsSinceLastPromotion": ("overdue_promotion", "recently_promoted"),
    "YearsAtCompany": ("tenure_risk", "established_tenure"),
    "TotalWorkingYears": ("early_career", "experienced"),
    "YearsInCurrentRole": ("role_stagnation", "role_settled"),
    "YearsWithCurrManager": ("manager_instability", "stable_manager"),
    "NumCompaniesWorked": ("job_hopping_history", "stable_history"),
    "OverTime": ("overtime_burden", "balanced_hours"),
    "JobSatisfaction": ("low_job_satisfaction", "high_job_satisfaction"),
    "EnvironmentSatisfaction": ("poor_environment", "good_environment"),
    "RelationshipSatisfaction": ("weak_relationships", "strong_relationships"),
    "JobInvolvement": ("low_involvement", "high_involvement"),
    "WorkLifeBalance": ("poor_work_life_balance", "good_work_life_balance"),
    "satisfaction_mean": ("low_overall_satisfaction", "high_overall_satisfaction"),
    "PerformanceRating": ("performance_concern", "strong_performance"),
    "JobLevel": ("junior_level", "senior_level"),
    "StockOptionLevel": ("low_equity", "meaningful_equity"),
    "TrainingTimesLastYear": ("limited_training", "well_trained"),
    "PercentSalaryHike": ("small_raise", "strong_raise"),
    "DistanceFromHome": ("long_commute", "short_commute"),
    "BusinessTravel": ("heavy_travel", "light_travel"),
    "JobRole": ("role_attrition_pattern", "role_retention_pattern"),
    "Department": ("dept_attrition_pattern", "dept_retention_pattern"),
    "EducationField": ("field_attrition_pattern", "field_retention_pattern"),
}


class MLScorer:
    """Trained-model scorer. Built via `from_artifact`, never bare."""

    def __init__(self, model, spec: P.FeatureSpec, name: str):
        self.model = model
        self.spec = spec
        self.name = name
        self._heuristic = HeuristicScorer()
        # TreeExplainer on the fitted booster — cheap, deterministic, no background data.
        self._explainer = shap.TreeExplainer(model)

    @classmethod
    def from_artifact(cls, path) -> "MLScorer":
        """Load a persisted bundle. Raises if missing/corrupt (caller falls back)."""
        bundle = joblib.load(path)
        spec = P.FeatureSpec(**bundle["spec"])
        return cls(bundle["model"], spec, bundle.get("model_name", "lgbm-attrition-v1"))

    def score(self, rows: list[dict]) -> list[ScoreResult]:
        # Heuristic builds the full result first; ML overlays only flight risk.
        results = self._heuristic.score(rows)
        for res, row in zip(results, rows):
            try:
                self._apply_ml(res, row)
            except Exception as exc:  # per-row fail-closed: keep heuristic flight risk
                logger.warning("ML inference failed for one row (%s); kept heuristic "
                               "flight_risk", type(exc).__name__)
        return results

    def _apply_ml(self, res: ScoreResult, row: dict) -> None:
        X = P.row_to_features(row, self.spec)
        prob = float(self.model.predict_proba(X)[0, 1])
        flight_risk = int(np.clip(round(prob * 100), SCORE_MIN, SCORE_MAX))
        codes = self._reason_codes(X)

        res.flight_risk = flight_risk
        res.reason_codes = codes
        # Keep the retention_risk panel metric consistent with the headline.
        for m in res.metrics:
            if m.get("metric") == "retention_risk":
                m["score"] = flight_risk
                m["reason_codes"] = codes
                break

    def _reason_codes(self, X) -> list[dict]:
        """Per-employee SHAP top contributors -> [{label, direction, weight}], the same
        structured shape the heuristic emits. Sign of the SHAP value gives `direction`
        (positive pushes flight risk UP = 'increases'); |value| renormalized across the
        top drivers gives `weight`."""
        contribs = self._shap_contributions(X)        # list[(feature, signed_value)]
        ranked = sorted(contribs, key=lambda c: abs(c[1]), reverse=True)[:TOP_REASONS]
        total = sum(abs(v) for _, v in ranked) or 1.0
        out = []
        for feat, val in ranked:
            direction = "increases" if val >= 0 else "decreases"
            labels = _FEATURE_LABELS.get(feat)
            label = labels[0] if (labels and val >= 0) else (
                labels[1] if labels else feat.lower())
            out.append({"label": label, "direction": direction,
                        "weight": round(abs(val) / total, 3)})
        return out

    def _shap_contributions(self, X) -> list[tuple[str, float]]:
        """SHAP values for the positive (attrition) class for a single-row frame.
        Normalizes across the shap-version return shapes (array vs per-class list)."""
        import warnings
        with warnings.catch_warnings():
            # We explicitly handle the list-of-ndarray output below; silence the
            # informational shap/lightgbm shape-change warning.
            warnings.simplefilter("ignore", category=UserWarning)
            sv = self._explainer.shap_values(X)
        arr = np.asarray(sv)
        if isinstance(sv, list):          # [class0, class1] -> take positive class
            arr = np.asarray(sv[1])
        elif arr.ndim == 3:               # (n, features, classes)
            arr = arr[:, :, -1]
        row_vals = arr[0]
        return list(zip(self.spec.columns, [float(v) for v in row_vals]))
