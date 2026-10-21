"""Scoring seam — POST /score (:8001).

Language-agnostic HTTP contract so a trained model swaps in behind the identical
endpoint. Operates on employee TOKENS + numeric features only; it has no database
access and never sees a name. Inputs are validated by pydantic with bounded ranges;
errors are generic (no internal detail leaks).

Request  : { "as_of_date": "YYYY-MM-DD", "rows": [FeatureRow, ...] }
Response : { "model": "<name>", "results": [ScoreResult, ...] }
"""
from __future__ import annotations

import inspect
import logging
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .model_loader import load_model

logger = logging.getLogger("pulsescore.model")

app = FastAPI(title="PulseScore Model Service", version="0.1.0")

# Loaded once at startup; swapping to a trained model changes only model_loader.
_SCORER = load_model()
# Named scorers a request may ask for explicitly (used by the v1-vs-v2 backtest).
_NAMED: dict[str, object] = {}


def _scorer_for(name: str | None):
    if name is None:
        return _SCORER
    if name not in _NAMED:
        _NAMED[name] = load_model(name)
    return _NAMED[name]


class FeatureRow(BaseModel):
    employee_token: str = Field(min_length=1, max_length=32)
    comp_gap: float = Field(ge=0.0, le=1.0)
    months_since_promotion: int = Field(ge=0, le=600)
    tenure_months: int = Field(ge=0, le=600)
    manager_changes_12mo: int = Field(ge=0, le=24)
    level: int = Field(ge=1, le=10)
    division: str = Field(min_length=1, max_length=64)
    # Employee-360 inputs for the broader panel. Optional with safe defaults so the
    # legacy retention-only request (and its tests) keep working unchanged.
    perf_rating: float = Field(default=3.0, ge=1.0, le=5.0)
    engagement_pulse: int | None = Field(default=None, ge=0, le=100)
    learning_hours_12mo: int = Field(default=0, ge=0, le=2000)
    internal_moves: int = Field(default=0, ge=0, le=50)
    span_of_control: int = Field(default=0, ge=0, le=1000)
    skill_count: int = Field(default=0, ge=0, le=100)
    skill_avg_prof: float = Field(default=0.0, ge=0.0, le=5.0)
    skill_family_breadth: int = Field(default=0, ge=0, le=20)
    consented_signals: int = Field(default=0, ge=0, le=1)
    # ---- scoring v2 inputs (all optional; missing means "unknown", never "bad") ----
    # False for people who have left: they are scored but excluded from peer groups and
    # calibration, so they never shape what "normal" looks like for current staff.
    reference: bool = True
    monitoring_consent: int = Field(default=0, ge=0, le=1)
    # pay & reward
    comp_gap_vs_market: float | None = Field(default=None, ge=-1.0, le=2.0)
    pay_percentile_in_role: float | None = Field(default=None, ge=0.0, le=1.0)
    months_since_raise: float | None = Field(default=None, ge=0, le=600)
    last_raise_pct: float | None = Field(default=None, ge=-50, le=200)
    base_salary: float | None = Field(default=None, ge=0, le=10_000_000_000)
    salary_currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    unvested_equity_usd: float | None = Field(default=None, ge=0, le=1_000_000_000)
    months_since_vest: float | None = Field(default=None, ge=0, le=600)
    # career & job search
    promotion_velocity: float | None = Field(default=None, ge=0, le=50)
    years_in_current_role: float | None = Field(default=None, ge=0, le=60)
    internal_applications_12mo: int | None = Field(default=None, ge=0, le=100)
    months_since_internal_application: float | None = Field(default=None, ge=0, le=600)
    # engagement (sent only for consented employees)
    engagement_score: float | None = Field(default=None, ge=0, le=100)
    engagement_trend: float | None = Field(default=None, ge=-100, le=100)
    enps_score: float | None = Field(default=None, ge=-100, le=100)
    job_satisfaction: float | None = Field(default=None, ge=1, le=5)
    work_life_balance: float | None = Field(default=None, ge=1, le=5)
    survey_sentiment: float | None = Field(default=None, ge=-1, le=1)
    recognition_count: float | None = Field(default=None, ge=0, le=10_000)
    manager_effectiveness_score: float | None = Field(default=None, ge=1, le=5)
    # manager & team
    team_turnover_rate: float | None = Field(default=None, ge=0, le=1)
    peers_departed_recently: int | None = Field(default=None, ge=0, le=1000)
    manager_rating: float | None = Field(default=None, ge=1, le=5)
    months_since_manager_change: float | None = Field(default=None, ge=0, le=600)
    # market pull
    skill_market_demand: float | None = Field(default=None, ge=0, le=1)
    industry_hiring_heat: float | None = Field(default=None, ge=0, le=1)
    skill_uniqueness: float | None = Field(default=None, ge=0, le=1)
    regional_comp_movement: float | None = Field(default=None, ge=-1, le=1)
    # ties & history
    median_prior_tenure: float | None = Field(default=None, ge=0, le=60)
    job_hopping_rate: float | None = Field(default=None, ge=0, le=10)
    # workload & wellbeing
    pto_used: float | None = Field(default=None, ge=0, le=366)
    overtime: float | None = Field(default=None, ge=0, le=1)
    commute_km: float | None = Field(default=None, ge=0, le=30_000)
    after_hours_activity: float | None = Field(default=None, ge=0, le=1)
    # value + panel
    goal_attainment: float | None = Field(default=None, ge=0, le=5)
    performance_trend: float | None = Field(default=None, ge=-5, le=5)
    review_rating_stdev: float | None = Field(default=None, ge=0, le=5)
    division_signals: dict[str, float] | None = Field(default=None, max_length=50)
    certifications_count: int | None = Field(default=None, ge=0, le=100)
    training_times_last_year: int | None = Field(default=None, ge=0, le=366)
    skills_acquired_12mo: int | None = Field(default=None, ge=0, le=200)
    bridge_score: float | None = Field(default=None, ge=0, le=1)
    betweenness: float | None = Field(default=None, ge=0, le=1)


class ScoreRequest(BaseModel):
    as_of_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    rows: list[FeatureRow] = Field(min_length=1, max_length=5000)
    # The org's observed 12-month leave rate; v2 calibrates to it (default: configured).
    base_rate: float | None = Field(default=None, gt=0.0, lt=1.0)
    # Score with a specific backend instead of the configured one (v1-vs-v2 backtest).
    scorer: Literal["v1", "v2"] | None = None


class ReasonCode(BaseModel):
    label: str
    direction: str
    weight: float
    detail: str | None = None


class MetricScore(BaseModel):
    metric: str
    score: int | None
    reason_codes: list[ReasonCode]
    note: str | None = None


class CapitalMetric(BaseModel):
    metric: str
    amount: float
    unit: str
    is_estimate: bool


class ScoreRow(BaseModel):
    employee_token: str
    flight_risk: int
    risk_trend: int
    value_score: int
    reason_codes: list[ReasonCode]
    metrics: list[MetricScore] = []
    capital: list[CapitalMetric] = []
    leave_probability: float | None = None
    risk_percentile: int | None = None
    confidence: int | None = None
    scorer_version: str | None = None


class ScoreResponse(BaseModel):
    model: str
    results: list[ScoreRow]


@app.exception_handler(Exception)
async def _unhandled(_req: Request, exc: Exception):
    logger.error("unhandled error: %s", type(exc).__name__)  # type only, no payload
    return JSONResponse(status_code=500, content={"detail": "internal error"})


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "model": _SCORER.name}


@app.post("/score", response_model=ScoreResponse)
def score(req: ScoreRequest) -> ScoreResponse:
    scorer = _scorer_for(req.scorer)
    rows = [row.model_dump() for row in req.rows]
    if "base_rate" in inspect.signature(scorer.score).parameters:
        results = scorer.score(rows, base_rate=req.base_rate)
    else:
        results = scorer.score(rows)
    return ScoreResponse(
        model=scorer.name,
        results=[
            ScoreRow(
                employee_token=r.employee_token, flight_risk=r.flight_risk,
                risk_trend=r.risk_trend, value_score=r.value_score,
                leave_probability=r.leave_probability, risk_percentile=r.risk_percentile,
                confidence=r.confidence, scorer_version=r.scorer_version,
                reason_codes=[ReasonCode(**rc) for rc in r.reason_codes],
                metrics=[
                    MetricScore(metric=m["metric"], score=m["score"],
                                reason_codes=[ReasonCode(**rc) for rc in m["reason_codes"]],
                                note=m.get("note"))
                    for m in r.metrics
                ],
                capital=[CapitalMetric(**cm) for cm in r.capital],
            )
            for r in results
        ],
    )
