"""Phase 2 — the SCORING WALL (§4 Table 1 bias traits + §4 Table 9 identity proxies).

These canonical columns must NEVER reach any scoring feature set. This test asserts
that against the model_service feature space directly: neither the canonical names
(gender / birth_year_band / marital_status / school_prestige / named_employer) nor
their IBM-cased equivalents (Gender / Age / AgeBand / MaritalStatus) appear in the
model's FEATURE_COLUMNS, and that the protected attributes are explicitly held out.
"""
from __future__ import annotations

from data import canonical
from model_service.ml import preprocess as P

# Canonical walled column -> the model-side name(s) that would represent the same trait.
EQUIVALENTS = {
    "gender": {"Gender", "gender"},
    "marital_status": {"MaritalStatus", "marital_status"},
    "birth_year_band": {"Age", "AgeBand", "birth_year_band", "age"},
    "school_prestige": {"school_prestige", "SchoolPrestige"},
    "named_employer": {"named_employer", "NamedEmployer"},
}


def test_no_walled_column_is_a_scoring_feature():
    feature_set = set(P.FEATURE_COLUMNS)
    for canon_col in canonical.SCORING_EXCLUDED_COLUMNS:
        for name in EQUIVALENTS[canon_col]:
            assert name not in feature_set, (
                f"walled column {canon_col!r} (as {name!r}) leaked into the scoring "
                f"feature set")


def test_protected_attributes_are_explicitly_held_out():
    # The model must name Gender / Age / MaritalStatus as protected (bias-audit only).
    assert {"Gender", "Age", "MaritalStatus"} <= set(P.PROTECTED_COLS)
    # And none of them (or an age band) may be in the feature columns.
    assert not (set(P.PROTECTED_COLS) & set(P.FEATURE_COLUMNS))
    assert "AgeBand" not in set(P.FEATURE_COLUMNS)


def test_wall_list_is_bias_plus_identity_proxy():
    assert canonical.SCORING_EXCLUDED_COLUMNS == (
        canonical.BIAS_AUDIT_COLUMNS | canonical.IDENTITY_PROXY_COLUMNS)
    assert canonical.BIAS_AUDIT_COLUMNS == {"gender", "birth_year_band", "marital_status"}
    assert canonical.IDENTITY_PROXY_COLUMNS == {"school_prestige", "named_employer"}
