"""`model_service.ml` — the trained-attrition-model submodule.

A SELF-CONTAINED, OPTIONAL backend that plugs in behind the existing `/score` seam
as a swappable `Scorer`. It is additive only: the transparent `HeuristicScorer`
remains the default and the automatic fallback. Nothing in this package is imported
unless `SCORING_BACKEND="ml"` (see `model_service.config`), so the heavy ML
dependencies (lightgbm, scikit-learn, shap) stay optional.

Pipeline (all in this package):
    preprocess.py -> features + label from the IBM sample CSV (dev data only)
    train.py      -> LightGBM classifier + SHAP, validated, persisted to artifacts/
    metrics.py    -> AUC/precision/recall/F1/confusion + bias report
    scorer.py     -> MLScorer: loads the artifact, scores rows, SHAP reason codes,
                     fails CLOSED to the heuristic on any error

See `README.md` for the data caveat and the production-hardening checklist.
"""
