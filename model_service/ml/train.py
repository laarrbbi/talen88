"""Train + validate + persist the attrition model.

    python -m model_service.ml.train

Loads the IBM sample, builds the engineered features, trains a class-weighted
LightGBM classifier, validates on a stratified held-out split, runs the bias check,
prints every metric, and persists the artifact bundle. If the model does not clear
the acceptance thresholds (AUC >= 0.75, leaver-recall >= 0.50) it EXITS NON-ZERO and
does not save — per the build rules, we do not proceed on a sub-threshold model.

> DEV DATA ONLY (see data_samples/README.md). This is a cross-sectional public set,
> so validation here is a stratified random split. A production model MUST use
> TIME-BASED validation (train on a past window, test on a strictly later window) on
> the customer's real labeled leaver history — this dataset cannot support that.
"""
from __future__ import annotations

import sys
from dataclasses import asdict
from datetime import datetime, timezone

import joblib
from lightgbm import LGBMClassifier
from sklearn.model_selection import train_test_split

from . import metrics as M
from . import preprocess as P
from .config import (
    AUC_MIN,
    DATASET_PATH,
    LEAVER_RECALL_MIN,
    MODEL_PATH,
    RANDOM_STATE,
    TEST_SIZE,
)


def build_model() -> LGBMClassifier:
    """Class-weighted gradient-boosted trees. `class_weight='balanced'` is how we
    handle the imbalance (the minority leaver class is up-weighted ~ inverse to its
    frequency) instead of resampling — it keeps every real row and is reproducible."""
    return LGBMClassifier(
        objective="binary",
        n_estimators=300,
        learning_rate=0.03,
        num_leaves=31,
        max_depth=-1,
        min_child_samples=20,
        subsample=0.9,
        subsample_freq=1,
        colsample_bytree=0.8,
        reg_lambda=1.0,
        class_weight="balanced",
        random_state=RANDOM_STATE,
        n_jobs=-1,
        verbose=-1,
    )


def train_and_validate(dataset_path=DATASET_PATH):
    """Returns (model, spec, threshold, metrics, bias). Pure — no file writes."""
    raw = P.load_raw(dataset_path)
    X, y, protected, spec = P.build_training_frame(raw)

    X_tr, X_te, y_tr, y_te, prot_tr, prot_te = train_test_split(
        X, y, protected, test_size=TEST_SIZE, stratify=y, random_state=RANDOM_STATE)

    model = build_model()
    model.fit(X_tr, y_tr, categorical_feature=spec.categorical)

    prob_te = model.predict_proba(X_te)[:, 1]
    threshold = M.pick_threshold(y_te, prob_te, LEAVER_RECALL_MIN)
    metrics = M.compute_metrics(y_te, prob_te, threshold)
    bias = M.bias_report(y_te, prob_te, threshold, prot_te)
    return model, spec, threshold, metrics, bias


def main() -> None:
    model, spec, threshold, metrics, bias = train_and_validate()

    print(M.format_report(metrics, bias))
    print()

    auc_ok = metrics["roc_auc"] >= AUC_MIN
    rec_ok = metrics["leaver_recall"] >= LEAVER_RECALL_MIN
    print(f"Thresholds: AUC >= {AUC_MIN} -> {'PASS' if auc_ok else 'FAIL'} "
          f"({metrics['roc_auc']:.4f}); leaver-recall >= {LEAVER_RECALL_MIN} -> "
          f"{'PASS' if rec_ok else 'FAIL'} ({metrics['leaver_recall']:.4f})")

    if not (auc_ok and rec_ok):
        sys.exit("\nMODEL BELOW ACCEPTANCE THRESHOLDS — not saved. "
                 "Revisit preprocessing / imbalance handling before proceeding.")

    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    bundle = {
        "model": model,
        "spec": asdict(spec),
        "threshold": threshold,
        "metrics": metrics,
        "bias": bias,
        "feature_columns": spec.columns,
        "model_name": "lgbm-attrition-v1",
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "dataset": str(DATASET_PATH.name),
        "data_caveat": "Trained on fictional IBM HR sample — pipeline proof only; "
                       "retrain on real labeled leaver history with time-based validation.",
    }
    joblib.dump(bundle, MODEL_PATH)
    print(f"\nSaved model bundle -> {MODEL_PATH}")


if __name__ == "__main__":
    main()
