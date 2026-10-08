"""Scoring seam — POST /score (:8001).

Language-agnostic HTTP contract so a trained model swaps in behind the identical
endpoint. Operates on employee TOKENS + numeric features only; it has no database
access and never sees a name. Inputs are validated by pydantic with bounded ranges;
errors are generic (no internal detail leaks).

Request  : { "as_of_date": "YYYY-MM-DD", "rows": [FeatureRow, ...] }
Response : { "model": "<name>", "results": [ScoreResult, ...] }
"""
from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .model_loader import load_model

logger = logging.getLogger("pulsescore.model")

app = FastAPI(title="PulseScore Model Service", version="0.1.0")

# Loaded once at startup; swapping to a trained model changes only model_loader.
_SCORER = load_model()


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


class ScoreRequest(BaseModel):
    as_of_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    rows: list[FeatureRow] = Field(min_length=1, max_length=5000)


class ReasonCode(BaseModel):
    label: str
    direction: str
    weight: float


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
    results = _SCORER.score([row.model_dump() for row in req.rows])
    return ScoreResponse(
        model=_SCORER.name,
        results=[
            ScoreRow(
                employee_token=r.employee_token, flight_risk=r.flight_risk,
                risk_trend=r.risk_trend, value_score=r.value_score,
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
