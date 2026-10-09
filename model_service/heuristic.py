"""Transparent heuristic scorer (MVP stand-in for a trained model).

Flight risk is a weighted blend of four well-understood retention drivers:
  - comp_gap              (underpaid vs band midpoint) -> raises risk
  - months_since_promotion (career stagnation)         -> raises risk
  - tenure_months          (the ~2-year "tenure cliff") -> raises risk near the cliff
  - manager_changes_12mo   (managerial instability)     -> raises risk

Each driver is normalized to a 0..1 "pressure", combined with fixed weights, and
scaled to 0..100. Reason codes are derived directly from each driver's signed
contribution relative to a neutral baseline: the SIGN gives `direction`
(increases/decreases) and the magnitude (renormalized across the top drivers)
gives `weight`. This is the same {label, direction, weight} shape that SHAP values
will produce for the trained model — see model_loader.py.

Everything here is pure and deterministic; it depends only on the feature row, so
it is trivially testable and swappable.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

# Driver weights (sum to 1.0). These are the transparent "model coefficients".
W_COMP = 0.35
W_PROMO = 0.25
W_CLIFF = 0.20
W_MGR = 0.20

# Normalization anchors (where a driver reaches maximum pressure).
COMP_GAP_MAX = 0.30        # a 30% gap below midpoint = full comp pressure
PROMO_MAX_MONTHS = 36.0    # 3 years with no promotion = full stagnation pressure
MGR_CHANGES_MAX = 3.0      # 3+ manager changes in 12mo = full instability pressure
CLIFF_CENTER = 24.0        # tenure cliff peaks around 24 months
CLIFF_SIGMA = 9.0

# Baseline pressure: drivers above this push risk UP (increases), below push DOWN.
BASELINE = 0.40

# Value score weighting (transparent): seniority + experience.
VW_LEVEL = 0.65
VW_TENURE = 0.35
LEVEL_MAX = 6.0
TENURE_VALUE_MAX = 120.0   # ~10 years caps the experience contribution

TOP_REASONS = 3            # number of reason codes reported per employee

# Human-stable labels per driver, keyed by direction.
_LABELS = {
    "comp": {"increases": "comp_below_band", "decreases": "competitive_pay"},
    "promo": {"increases": "overdue_promotion", "decreases": "recently_promoted"},
    "cliff": {"increases": "tenure_cliff", "decreases": "tenure_stable"},
    "mgr": {"increases": "manager_instability", "decreases": "stable_manager"},
}

# ---- Layer 2 panel: anchors for the broader scores + capital metrics ----------
SKILL_COUNT_MAX = 8.0
SKILL_FAMILY_MAX = 4.0
LEARN_HOURS_MAX = 60.0
SPAN_MAX = 8.0
TENURE_TRUST_MAX = 60.0    # ~5 years tenure = full reliability credit
MOVES_MAX = 3.0

# Synthetic comp bands by level (MODELED ESTIMATES — surfaced with a caveat).
SALARY_BAND = {1: 70_000, 2: 95_000, 3: 130_000, 4: 180_000, 5: 240_000, 6: 350_000}
RETENTION_INVEST_FRACTION = 0.20   # of expected loss we'd put toward retention


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _cliff_pressure(tenure_months: float) -> float:
    """Gaussian bump centered on the ~2-year tenure cliff."""
    return _clamp01(math.exp(-((tenure_months - CLIFF_CENTER) ** 2) / (2 * CLIFF_SIGMA ** 2)))


@dataclass
class ScoreResult:
    employee_token: str
    flight_risk: int
    risk_trend: int
    value_score: int
    reason_codes: list[dict]
    # Layer 2 panel (additive; the legacy fields above stay for back-compat).
    metrics: list[dict] = field(default_factory=list)   # 8 scores, each w/ reason codes
    capital: list[dict] = field(default_factory=list)   # capital/moneyball figures


class HeuristicScorer:
    """The MVP scorer. Implements the same `score()` interface a trained model will."""

    name = "heuristic-v1"

    def score(self, rows: list[dict]) -> list[ScoreResult]:
        return [self._score_row(r) for r in rows]

    def _score_row(self, r: dict) -> ScoreResult:
        p_comp = _clamp01(float(r["comp_gap"]) / COMP_GAP_MAX)
        p_promo = _clamp01(float(r["months_since_promotion"]) / PROMO_MAX_MONTHS)
        p_cliff = _cliff_pressure(float(r["tenure_months"]))
        p_mgr = _clamp01(float(r["manager_changes_12mo"]) / MGR_CHANGES_MAX)

        raw = W_COMP * p_comp + W_PROMO * p_promo + W_CLIFF * p_cliff + W_MGR * p_mgr
        flight_risk = int(round(_clamp01(raw) * 100))

        value_score = self._value_score(float(r["level"]), float(r["tenure_months"]))
        reason_codes = self._reason_codes(
            {"comp": (p_comp, W_COMP), "promo": (p_promo, W_PROMO),
             "cliff": (p_cliff, W_CLIFF), "mgr": (p_mgr, W_MGR)}
        )
        metrics = self._panel_scores(r, flight_risk, reason_codes)
        capital = self._capital_metrics(r, flight_risk, value_score)
        # risk_trend is filled by the data layer (it has the prior snapshot). The
        # pure scorer is stateless, so it reports 0 here.
        return ScoreResult(
            employee_token=r["employee_token"], flight_risk=flight_risk,
            risk_trend=0, value_score=value_score, reason_codes=reason_codes,
            metrics=metrics, capital=capital,
        )

    def _panel_scores(self, r: dict, flight_risk: int, retention_reasons: list[dict]) -> list[dict]:
        """The 8-score intelligence panel. Each score is a transparent weighted blend
        of 0..1 driver pressures; reason codes come from each driver's signed
        contribution vs the neutral baseline (same shape as flight-risk reasons)."""
        perf = _clamp01((float(r.get("perf_rating", 3.0)) - 1.0) / 4.0)
        learn = _clamp01(float(r.get("learning_hours_12mo", 0)) / LEARN_HOURS_MAX)
        prof = _clamp01(float(r.get("skill_avg_prof", 0.0)) / 5.0)
        count = _clamp01(float(r.get("skill_count", 0)) / SKILL_COUNT_MAX)
        breadth = _clamp01(float(r.get("skill_family_breadth", 0)) / SKILL_FAMILY_MAX)
        moves = _clamp01(float(r.get("internal_moves", 0)) / MOVES_MAX)
        tenure = _clamp01(float(r["tenure_months"]) / TENURE_TRUST_MAX)
        mgr_stability = 1.0 - _clamp01(float(r["manager_changes_12mo"]) / MGR_CHANGES_MAX)
        level = _clamp01(float(r["level"]) / LEVEL_MAX)
        span = _clamp01(float(r.get("span_of_control", 0)) / SPAN_MAX)

        panel = [
            self._metric("skills_depth", [
                ("deep_expertise", "shallow_expertise", prof, 0.40),
                ("broad_skill_set", "narrow_skill_set", count, 0.35),
                ("cross_domain_adjacency", "single_domain", breadth, 0.25)]),
            self._metric("learning_velocity", [
                ("high_learning_investment", "low_learning_activity", learn, 1.0)]),
            self._metric("performance_impact", [
                ("strong_performance", "below_expectations", perf, 1.0)]),
            self._metric("mobility_readiness", [
                ("cross_domain_adjacency", "narrow_skill_set", breadth, 0.40),
                ("proven_performer", "performance_gap", perf, 0.30),
                ("history_of_mobility", "static_role_history", moves, 0.30)]),
            {"metric": "retention_risk", "score": flight_risk, "reason_codes": retention_reasons},
            self._metric("trust_reliability", [
                ("established_tenure", "short_tenure", tenure, 0.40),
                ("stable_reporting_line", "frequent_manager_changes", mgr_stability, 0.30),
                ("consistent_performance", "inconsistent_performance", perf, 0.30)]),
            self._metric("leadership_influence", [
                ("senior_scope", "junior_scope", level, 0.40),
                ("broad_span_of_control", "individual_contributor", span, 0.40),
                ("strong_performance", "performance_gap", perf, 0.20)]),
        ]
        # Engagement & Productivity ONLY when the employee consented (no covert
        # monitoring). Otherwise score is None and nothing is persisted/surfaced.
        if int(r.get("consented_signals", 0)) == 1 and r.get("engagement_pulse") is not None:
            eng = _clamp01(float(r["engagement_pulse"]) / 100.0)
            panel.append(self._metric("engagement_productivity", [
                ("high_engagement", "disengagement_signal", eng, 0.75),
                ("productive_output", "output_dip", perf, 0.25)]))
        else:
            panel.append({"metric": "engagement_productivity", "score": None,
                          "reason_codes": [], "note": "no consented engagement signal"})
        return panel

    def _capital_metrics(self, r: dict, flight_risk: int, value_score: int) -> list[dict]:
        """Capital / 'moneyball' figures. Dollar + ratio values are MODELED ESTIMATES
        (is_estimate=True) and must be shown with a caveat."""
        salary = float(SALARY_BAND.get(int(r["level"]), 95_000))
        comp_gap = float(r["comp_gap"])
        # Cost-to-lose scales replacement+ramp cost with how valuable the person is.
        replacement_multiplier = 0.5 + (value_score / 100.0) * 1.5    # 0.5x..2.0x salary
        cost_to_lose = salary * replacement_multiplier
        expected_loss = cost_to_lose * (flight_risk / 100.0)
        invest = expected_loss * RETENTION_INVEST_FRACTION
        # Mitigation: underpaid people respond strongly to a comp fix -> higher ROI.
        mitigation = _clamp01(0.30 + comp_gap * 1.5)
        avoided = expected_loss * mitigation
        roi = round((avoided - invest) / invest, 2) if invest >= 1.0 else 0.0
        comp_efficiency = round(_clamp01((value_score / 100.0) * (0.6 + comp_gap)) * 100)
        return [
            {"metric": "value_score", "amount": float(value_score),
             "unit": "score_0_100", "is_estimate": False},
            {"metric": "cost_to_lose", "amount": round(cost_to_lose, 0),
             "unit": "USD", "is_estimate": True},
            {"metric": "suggested_retention_investment", "amount": round(invest, 0),
             "unit": "USD", "is_estimate": True},
            {"metric": "retention_roi", "amount": roi, "unit": "ratio", "is_estimate": True},
            {"metric": "compensation_efficiency", "amount": float(comp_efficiency),
             "unit": "score_0_100", "is_estimate": True},
        ]

    @staticmethod
    def _metric(key: str, drivers: list[tuple]) -> dict:
        """Build one panel score (0..100) + its reason codes from labelled drivers.

        drivers: list of (positive_label, negative_label, pressure 0..1, weight). The
        score is the weighted pressure blend; reason codes are the top signed
        contributions vs BASELINE, renormalized — identical shape to flight-risk codes.
        """
        score = int(round(_clamp01(sum(p * w for _, _, p, w in drivers)) * 100))
        contribs = []
        for pos, neg, pressure, weight in drivers:
            signed = (pressure - BASELINE) * weight
            direction = "increases" if signed >= 0 else "decreases"
            contribs.append((pos if signed >= 0 else neg, direction, abs(signed)))
        contribs.sort(key=lambda c: c[2], reverse=True)
        top = contribs[:TOP_REASONS]
        total = sum(c[2] for c in top) or 1.0
        codes = [{"label": lbl, "direction": d, "weight": round(m / total, 3)}
                 for lbl, d, m in top]
        return {"metric": key, "score": score, "reason_codes": codes}

    @staticmethod
    def _value_score(level: float, tenure_months: float) -> int:
        v = VW_LEVEL * _clamp01(level / LEVEL_MAX) + VW_TENURE * _clamp01(
            tenure_months / TENURE_VALUE_MAX)
        return int(round(_clamp01(v) * 100))

    @staticmethod
    def _reason_codes(drivers: dict[str, tuple[float, float]]) -> list[dict]:
        """Signed contribution vs baseline -> direction; |contribution| -> weight."""
        contribs = []
        for key, (pressure, weight) in drivers.items():
            signed = (pressure - BASELINE) * weight
            direction = "increases" if signed >= 0 else "decreases"
            contribs.append((_LABELS[key][direction], direction, abs(signed)))

        contribs.sort(key=lambda c: c[2], reverse=True)
        top = contribs[:TOP_REASONS]
        total = sum(c[2] for c in top) or 1.0
        return [
            {"label": label, "direction": direction, "weight": round(mag / total, 3)}
            for label, direction, mag in top
        ]
