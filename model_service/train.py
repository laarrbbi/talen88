"""Training placeholder (NOT run in the MVP).

This documents where a real training pipeline lands. The MVP ships the transparent
heuristic (see heuristic.py); there is no ML training. When a labeled dataset of
historical attrition exists, this script would:

    1. Pull historical feature snapshots + attrition outcomes from the data layer
       (tokens only — no PII ever enters training).
    2. Build a feature matrix (pandas) and train a gradient-boosted model
       (LightGBM) to predict flight risk.
    3. Fit a SHAP explainer so per-employee feature attributions can be emitted as
       reason codes in the SAME {label, direction, weight} shape the heuristic uses.
    4. Persist the model artifact; point PULSESCORE_MODEL_PATH at it.
    5. Swap `model_loader.load_model()` to return the trained scorer.

Nothing downstream (the /score contract, the data layer, the UI) changes — that is
the whole point of the scoring seam.

Future dependencies (kept OUT of the MVP to keep it minimal): lightgbm, shap,
pandas, scikit-learn.
"""
from __future__ import annotations


def main() -> None:
    raise SystemExit(
        "train.py is a documented placeholder. The MVP uses the transparent "
        "heuristic in heuristic.py; no training is performed. See the module "
        "docstring for the intended LightGBM + SHAP pipeline."
    )


if __name__ == "__main__":
    main()
