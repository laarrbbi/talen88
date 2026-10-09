"""Load + preprocess + feature-engineer the IBM HR sample, and build the matching
inference feature-vector from a live service `FeatureRow`.

This module is the single source of truth for the model's feature space. It is used
in two directions:

  TRAINING  (build_training_frame): IBM CSV  -> engineered feature DataFrame + label
  INFERENCE (row_to_features):      service FeatureRow dict -> one aligned feature row

so the columns, dtypes, and encodings the model trains on are exactly the ones it
sees at /score time. Anything the live service cannot supply is imputed from training
statistics stored in the `FeatureSpec` (medians for numerics, modes for categoricals),
which is also why preprocessing must tolerate missing values and unseen categories.

> DEV DATA ONLY. The IBM set is fictional public data; see data_samples/README.md.
> Several inference features below are necessarily imputed because the demo service
> does not carry them (OverTime, DistanceFromHome, marital status, ...). That is a
> documented limitation of scaffolding on a public cross-sectional set, NOT a property
> of a production model trained on a customer's own labeled fields.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# --- Columns we never feed the model ------------------------------------------
# Constant (no signal) + the row identifier.
DROP_COLS = ["EmployeeCount", "Over18", "StandardHours", "EmployeeNumber"]

# Protected / bias-audit-ONLY attributes. These are NEVER in the scoring feature
# matrix — they are held out and used solely for the fairness audit. Per the canonical
# schema's bias-audit-only bucket:
#   - Gender         : protected characteristic.
#   - Age            : protected characteristic (and any age-derived field, e.g. AgeBand).
#   - MaritalStatus  : a marital/family-status proxy for protected characteristics.
# A bias-walling test asserts none of these (or AgeBand) ever appear in FEATURE_COLUMNS.
PROTECTED_COLS = ["Gender", "Age", "MaritalStatus"]
TARGET = "Attrition"

# --- Raw IBM columns used as features (NO protected attributes) ----------------
NUMERIC_RAW = [
    "JobLevel", "MonthlyIncome", "DistanceFromHome",
    "YearsAtCompany", "TotalWorkingYears", "YearsSinceLastPromotion",
    "YearsInCurrentRole", "YearsWithCurrManager", "NumCompaniesWorked",
    "JobSatisfaction", "EnvironmentSatisfaction", "JobInvolvement",
    "WorkLifeBalance", "RelationshipSatisfaction", "PerformanceRating",
    "StockOptionLevel", "TrainingTimesLastYear", "PercentSalaryHike",
]
CATEGORICAL_RAW = [
    "OverTime", "BusinessTravel", "JobRole",
    "Department", "EducationField",
]
# --- Engineered numeric features (added on top of the raw ones) ----------------
# comp_gap          : the heuristic's core concept — pay below the role/level median.
# satisfaction_mean : a composite engagement proxy (the four 1-4 satisfaction fields).
ENGINEERED = ["comp_gap", "satisfaction_mean"]

FEATURE_COLUMNS = NUMERIC_RAW + ENGINEERED + CATEGORICAL_RAW
NUMERIC_FEATURES = NUMERIC_RAW + ENGINEERED


@dataclass
class FeatureSpec:
    """Everything needed to turn a live FeatureRow into the model's feature vector:
    column order, which columns are categorical + their known categories, imputation
    stats, and the per-level median income used to invert comp_gap -> income."""
    columns: list[str]
    categorical: list[str]
    categories: dict[str, list[str]] = field(default_factory=dict)
    medians: dict[str, float] = field(default_factory=dict)
    modes: dict[str, str] = field(default_factory=dict)
    level_median_income: dict[int, float] = field(default_factory=dict)
    overall_median_income: float = 0.0


def load_raw(path) -> pd.DataFrame:
    """Read the CSV. (utf-8-sig strips the BOM the IBM file ships with.)"""
    return pd.read_csv(path, encoding="utf-8-sig")


def _level_income_medians(df: pd.DataFrame) -> dict[int, float]:
    return {int(k): float(v) for k, v in df.groupby("JobLevel")["MonthlyIncome"].median().items()}


def _comp_gap_from_income(income: float, level: int, level_med: dict[int, float],
                          overall_med: float) -> float:
    med = level_med.get(int(level), overall_med) or overall_med or 1.0
    return float(np.clip((med - income) / med, 0.0, 1.0))


def engineer(df: pd.DataFrame, level_med: dict[int, float], overall_med: float) -> pd.DataFrame:
    """Add the engineered columns to a frame that already has the raw IBM columns."""
    out = df.copy()
    out["comp_gap"] = [
        _comp_gap_from_income(inc, lvl, level_med, overall_med)
        for inc, lvl in zip(out["MonthlyIncome"], out["JobLevel"])
    ]
    sat_cols = ["JobSatisfaction", "EnvironmentSatisfaction",
                "RelationshipSatisfaction", "WorkLifeBalance"]
    out["satisfaction_mean"] = out[sat_cols].mean(axis=1)
    return out


def build_training_frame(df: pd.DataFrame):
    """IBM raw frame -> (X, y, protected, spec).

    X has FEATURE_COLUMNS (categoricals as pandas `category`); y is 1 for leavers.
    `protected` is the held-out Gender column (+ an age band) for the bias audit.
    `spec` captures the stats needed to reproduce this transform at inference.
    """
    df = df.drop(columns=[c for c in DROP_COLS if c in df.columns], errors="ignore")
    y = (df[TARGET].astype(str).str.strip().str.lower() == "yes").astype(int)

    level_med = _level_income_medians(df)
    overall_med = float(df["MonthlyIncome"].median())
    feats = engineer(df, level_med, overall_med)

    X = feats[FEATURE_COLUMNS].copy()
    for c in CATEGORICAL_RAW:
        X[c] = X[c].astype("category")

    spec = FeatureSpec(
        columns=FEATURE_COLUMNS,
        categorical=CATEGORICAL_RAW,
        categories={c: [str(v) for v in X[c].cat.categories] for c in CATEGORICAL_RAW},
        medians={c: float(X[c].median()) for c in NUMERIC_FEATURES},
        modes={c: str(X[c].mode(dropna=True).iloc[0]) for c in CATEGORICAL_RAW},
        level_median_income=level_med,
        overall_median_income=overall_med,
    )

    protected = pd.DataFrame({
        "Gender": df["Gender"].astype(str).values if "Gender" in df else "Unknown",
        "AgeBand": pd.cut(df["Age"], bins=[0, 30, 40, 50, 200],
                          labels=["<30", "30-39", "40-49", "50+"]).astype(str).values,
    })
    return X, y, protected, spec


def align_categoricals(X: pd.DataFrame, spec: FeatureSpec) -> pd.DataFrame:
    """Force each categorical column to the training categories. Unseen values become
    NaN, which LightGBM handles natively as 'missing' — that is our unseen-category
    policy. Idempotent; safe on both training and inference frames."""
    out = X.copy()
    for c in spec.categorical:
        out[c] = pd.Categorical(out[c].astype(str), categories=spec.categories[c])
    return out[spec.columns]


# --- Inference: service FeatureRow -> one model feature row ---------------------

def row_to_features(row: dict, spec: FeatureSpec) -> pd.DataFrame:
    """Map ONE live `FeatureRow` (the /score contract) to a single-row DataFrame with
    exactly `spec.columns`. Fields the demo service carries are mapped to the matching
    concept; everything else is imputed from training stats. Missing/out-of-domain
    inputs degrade gracefully to the imputed value rather than raising.

    Heuristic-concept mapping (the overlap that makes the two backends comparable):
      level                 -> JobLevel
      comp_gap              -> comp_gap (same concept) + inverts to MonthlyIncome
      tenure_months         -> YearsAtCompany / TotalWorkingYears
      months_since_promotion-> YearsSinceLastPromotion
      manager_changes_12mo  -> YearsWithCurrManager (instability proxy)
      perf_rating           -> PerformanceRating
      engagement_pulse      -> JobSatisfaction / EnvironmentSatisfaction (consented only)
      internal_moves        -> NumCompaniesWorked
      learning_hours_12mo   -> TrainingTimesLastYear
    """
    g = row.get
    med = spec.medians
    f: dict[str, object] = {}

    level = _int(g("level"), int(med.get("JobLevel", 2)))
    comp_gap = _float(g("comp_gap"), med.get("comp_gap", 0.0))
    comp_gap = float(np.clip(comp_gap, 0.0, 1.0))
    tenure_y = _float(g("tenure_months"), med.get("YearsAtCompany", 5.0) * 12) / 12.0
    promo_y = _float(g("months_since_promotion"), med.get("YearsSinceLastPromotion", 1.0) * 12) / 12.0
    mgr_changes = _float(g("manager_changes_12mo"), 0.0)

    level_med = spec.level_median_income.get(level, spec.overall_median_income or 1.0)
    income = level_med * (1.0 - comp_gap)

    # Numerics: mapped where possible, else the training median.
    # (Age / MaritalStatus are NOT features — protected, bias-audit-only.)
    f["JobLevel"] = level
    f["MonthlyIncome"] = income
    f["DistanceFromHome"] = med.get("DistanceFromHome", 9.0)
    f["YearsAtCompany"] = tenure_y
    f["TotalWorkingYears"] = max(tenure_y, med.get("TotalWorkingYears", tenure_y))
    f["YearsSinceLastPromotion"] = promo_y
    f["YearsInCurrentRole"] = min(tenure_y, med.get("YearsInCurrentRole", tenure_y))
    # More manager changes -> shorter tenure with the current manager (instability).
    f["YearsWithCurrManager"] = float(np.clip(tenure_y / (mgr_changes + 1.0), 0.0, tenure_y))
    f["NumCompaniesWorked"] = _float(g("internal_moves"), med.get("NumCompaniesWorked", 2.0))

    # Engagement: only when the employee consented to signals (no covert monitoring).
    consented = _int(g("consented_signals"), 0) == 1
    pulse = g("engagement_pulse")
    if consented and pulse is not None:
        sat = float(np.clip(1 + round(_float(pulse, 50.0) / 100.0 * 3), 1, 4))
        f["JobSatisfaction"] = sat
        f["EnvironmentSatisfaction"] = sat
    else:
        f["JobSatisfaction"] = med.get("JobSatisfaction", 3.0)
        f["EnvironmentSatisfaction"] = med.get("EnvironmentSatisfaction", 3.0)
    f["JobInvolvement"] = med.get("JobInvolvement", 3.0)
    f["WorkLifeBalance"] = med.get("WorkLifeBalance", 3.0)
    f["RelationshipSatisfaction"] = med.get("RelationshipSatisfaction", 3.0)

    perf = _float(g("perf_rating"), 3.0)
    f["PerformanceRating"] = 4.0 if perf >= 4.0 else 3.0   # IBM scale is {3,4}
    f["StockOptionLevel"] = med.get("StockOptionLevel", 1.0)
    f["TrainingTimesLastYear"] = float(np.clip(round(_float(g("learning_hours_12mo"), 0) / 20.0), 0, 6)) \
        if g("learning_hours_12mo") is not None else med.get("TrainingTimesLastYear", 3.0)
    f["PercentSalaryHike"] = med.get("PercentSalaryHike", 14.0)

    f["comp_gap"] = comp_gap
    f["satisfaction_mean"] = np.mean([
        f["JobSatisfaction"], f["EnvironmentSatisfaction"],
        f["RelationshipSatisfaction"], f["WorkLifeBalance"]])

    # Categoricals: the demo service does not carry them -> training mode.
    for c in spec.categorical:
        f[c] = spec.modes.get(c)

    X = pd.DataFrame([f])
    return align_categoricals(X, spec)


def _float(v, default: float) -> float:
    try:
        if v is None:
            return float(default)
        return float(v)
    except (TypeError, ValueError):
        return float(default)


def _int(v, default: int) -> int:
    try:
        if v is None:
            return int(default)
        return int(v)
    except (TypeError, ValueError):
        return int(default)
