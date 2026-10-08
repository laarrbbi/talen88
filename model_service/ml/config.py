"""ML submodule configuration — paths, thresholds, and the backend toggle.

Pure constants + env reads. No heavy imports here, so the main service can read
`SCORING_BACKEND` without pulling in lightgbm/shap.
"""
from __future__ import annotations

import os
from pathlib import Path

# --- Backend toggle (read by model_service.model_loader) -----------------------
# "heuristic" (default) keeps the transparent scorer; "ml" activates MLScorer.
# Anything else, or a failed ML load, falls back to the heuristic.
SCORING_BACKEND = os.environ.get("SCORING_BACKEND", "heuristic").strip().lower()

# --- Paths ---------------------------------------------------------------------
_HERE = Path(__file__).resolve().parent
DATA_DIR = _HERE.parent / "data_samples"
DATASET_PATH = Path(os.environ.get("PULSESCORE_ML_DATASET", DATA_DIR / "ibm_hr_attrition.csv"))
ARTIFACT_DIR = _HERE / "artifacts"
# Single artifact bundle (model + medians + feature spec + metrics), joblib-pickled.
MODEL_PATH = Path(os.environ.get("PULSESCORE_MODEL_PATH", ARTIFACT_DIR / "attrition_lgbm.joblib"))

# --- Reproducibility -----------------------------------------------------------
RANDOM_STATE = 42
TEST_SIZE = 0.20

# --- Acceptance thresholds (asserted by tests + train) -------------------------
# Healthy range for this dataset is AUC 0.75-0.85. Recall on the leaver (positive)
# class must beat chance: the model must actually catch leavers, not predict "stays".
AUC_MIN = 0.75
LEAVER_RECALL_MIN = 0.50

# Decision threshold for converting probability -> hard label. Tuned below the
# default 0.5 because the positive class is rare (~16%); the exact value is chosen
# at train time to meet LEAVER_RECALL_MIN and stored in the artifact.
DEFAULT_DECISION_THRESHOLD = 0.30

# Score scale (matches the heuristic's flight_risk).
SCORE_MIN = 0
SCORE_MAX = 100
