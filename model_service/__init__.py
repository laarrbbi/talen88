"""PulseScore model service.

Computes flight-risk / value scores and structured reason codes behind a single
HTTP endpoint (the scoring seam, POST /score). The MVP uses a transparent weighted
heuristic; a trained model (LightGBM + SHAP) swaps in behind the same endpoint and
the same response shape via model_loader.load_model(). This service operates on
employee TOKENS + numeric features only — it never sees or needs a real name.
"""
