# `model_service/` — scoring seam

Computes the full **8-score intelligence panel + capital ("moneyball") metrics**, plus
the legacy flight-risk / value scores, all with structured reason codes — behind one
HTTP endpoint, `POST /score` (:8001). The default backend is a **transparent weighted
heuristic**; an optional **trained LightGBM + SHAP** model (`SCORING_BACKEND=ml`,
see [`ml/`](ml/README.md)) swaps in behind the same endpoint and the same response
shape, falling back to the heuristic if unavailable. This service operates on employee
**tokens + numeric features only** — it has no DB access and never sees a real name.

## Run

```bash
# from repo root, venv active, model_service/requirements.txt installed
uvicorn model_service.service:app --reload --port 8001
pytest model_service/tests -q
```

Quick check:
```bash
curl -s localhost:8001/score -H 'Content-Type: application/json' -d '{
  "as_of_date": "2026-06-01",
  "rows": [{"employee_token":"emp_demo","comp_gap":0.28,"months_since_promotion":34,
            "tenure_months":24,"manager_changes_12mo":3,"level":2,"division":"technology"}]
}' | python -m json.tool
```

## Contract
**Request** `{ as_of_date, rows: [FeatureRow] }`. `FeatureRow` is the four legacy
retention drivers — `employee_token, comp_gap, months_since_promotion, tenure_months,
manager_changes_12mo, level, division` — plus the **Employee-360 panel inputs**, all
optional with safe defaults so the legacy retention-only request still validates:
`perf_rating (1–5), engagement_pulse (0–100|null), learning_hours_12mo, internal_moves,
span_of_control, skill_count, skill_avg_prof (0–5), skill_family_breadth,
consented_signals (0|1)`. All ranges enforced by pydantic → bad input is a generic 422.

**Response** `{ model, results: [{ employee_token, flight_risk (0–100), risk_trend,
value_score (0–100), reason_codes: [{label, direction, weight}], metrics: [MetricScore],
capital: [CapitalMetric] }] }`
- `metrics[]` — the **8-score panel**, each `{ metric, score (0–100 | null),
  reason_codes: [{label, direction, weight}], note? }`.
- `capital[]` — the capital figures, each `{ metric, amount, unit, is_estimate }`.

`reason_codes` are structured (never formatted strings); `direction` is
`increases`/`decreases`, `weight` is 0–1 (renormalized across the top drivers). The
legacy `flight_risk`/`value_score`/`reason_codes` fields are unchanged for back-compat;
the panel is additive. `risk_trend` is returned as 0 here and filled by the data
layer's refresh step, which has the prior snapshot to diff against.

## Heuristic (`heuristic.py`)
Every score is a transparent weighted blend of 0..1 driver "pressures", scaled to
0–100. Reason codes come from each driver's signed contribution vs a neutral baseline
(`BASELINE`): sign → `direction`, magnitude (renormalized across the top drivers) →
`weight`. This is the same `{label, direction, weight}` shape SHAP will emit later.

**Flight risk** = blend of four drivers — comp gap (0.35), months since promotion
(0.25), tenure cliff ~24mo Gaussian (0.20), manager changes (0.20). **Value score**
blends seniority (level) and experience (tenure).

**The 8-score panel** (`_panel_scores`): Skills Depth & Adjacency, Learning Velocity,
Performance & Impact, Mobility Readiness, **Retention Risk** (= flight_risk),
Trust & Reliability, Leadership Influence, and **Engagement & Productivity** — the last
is produced **only when `consented_signals == 1`** and a pulse is present; otherwise its
`score` is `null` with a `note` (no covert monitoring).

**Capital metrics** (`_capital_metrics`): Value Score, Cost-to-Lose, Suggested Retention
Investment, Retention ROI, Compensation Efficiency. Every dollar/ratio figure is a
**modeled estimate** (`is_estimate=True`) — derived from a synthetic per-level salary
band — and must be shown with a caveat in the UI.

## Replacement seam
`model_loader.load_model()` returns any object implementing `Scorer.score(rows) ->
[ScoreResult]`, selected by the `SCORING_BACKEND` env flag:

| `SCORING_BACKEND` | Backend | Notes |
| ----------------- | ------- | ----- |
| `heuristic` (default, or any unknown value) | `HeuristicScorer` | transparent panel; no ML deps |
| `ml` | `model_service.ml.MLScorer` | trained LightGBM + SHAP; **auto-falls back to the heuristic** if the artifact is missing/corrupt or the optional deps are absent |

The trained backend is **additive and swaps only `flight_risk`** (+ its SHAP reason
codes and the `retention_risk` panel metric); `value_score`, the capital figures, and
the other seven scores stay heuristic. The `/score` response shape is byte-identical in
both modes, so the data layer and UI consume it unchanged. **Fail-closed everywhere:**
the service never crashes or fails to start because of the model.

See [`ml/README.md`](ml/README.md) for the full pipeline (preprocess → train → SHAP →
score), the trained backend's commands, and the **dev-data-only** caveat. The legacy
`train.py` placeholder is superseded by `python -m model_service.ml.train`.

### Run the trained backend
```bash
brew install libomp                                   # macOS: lightgbm runtime dep
pip install -r model_service/ml/requirements.txt      # lightgbm, scikit-learn, shap
python -m model_service.ml.train                      # train + validate + persist (prints metrics)
SCORING_BACKEND=ml uvicorn model_service.service:app --port 8001   # serve the trained model
```
