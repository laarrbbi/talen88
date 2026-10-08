# `model_service/ml/` — trained attrition model (optional, swappable backend)

A real, trained **attrition / flight-risk** model that plugs in behind the existing
`POST /score` seam as a drop-in `Scorer`. It is **additive and optional**: the
transparent `HeuristicScorer` stays the default and the **automatic fallback**. The
trained model is selected by config only (`SCORING_BACKEND=ml`); if its artifact is
missing, fails to load, or errors at inference, the service logs it and falls back to
the heuristic — it never crashes because of the model.

> **Dev data only.** This pipeline is trained on the fictional public
> [IBM HR Analytics](../data_samples/README.md) sample. It exists to **prove the
> pipeline**, not to ship. A production model must be retrained on a design partner's
> **real, labeled leaver history** with **time-based validation** (train past → test a
> strictly later window). See the data README for the full caveat.

## Layout
| File | Role |
| ---- | ---- |
| `config.py` | paths, thresholds (`AUC_MIN=0.75`, `LEAVER_RECALL_MIN=0.50`), the `SCORING_BACKEND` toggle |
| `preprocess.py` | load CSV, drop constant/ID cols, **feature-engineer**, encode categoricals; also builds an inference feature-vector from a service `FeatureRow` |
| `metrics.py` | classification metrics (AUC/precision/recall/F1/confusion) + bias report |
| `train.py` | train LightGBM (class-weighted), validate vs thresholds, SHAP, **persist** the artifact |
| `scorer.py` | `MLScorer` — loads the artifact, scores rows, SHAP reason codes, prob→0–100, **fails closed to the heuristic** |
| `artifacts/` | persisted model bundle (gitignored) |

## Commands
```bash
# 0. optional deps (default heuristic backend needs none of this)
brew install libomp                                   # macOS: lightgbm runtime dep
pip install -r model_service/ml/requirements.txt

# 1. train + validate + persist (prints all metrics + bias check, STOPS if below thresholds)
python -m model_service.ml.train

# 2. serve the trained model behind the SAME /score contract
SCORING_BACKEND=ml uvicorn model_service.service:app --port 8001
#    (default — heuristic — needs no env:)  uvicorn model_service.service:app --port 8001

# 3. tests
pytest model_service/tests -q
```

## Why this is safe to add
- **No existing code paths change** unless `SCORING_BACKEND=ml` is set.
- **Same response shape** — `flight_risk` (0–100) + `{label, direction, weight}` reason
  codes — so the data layer and the app consume it with zero changes.
- **Only `flight_risk` swaps.** value_score, cost-to-lose, and the other panel scores
  keep their existing heuristic/formula logic.
- **Fail-closed:** any model problem → heuristic, logged.
