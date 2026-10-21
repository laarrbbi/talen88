"""Model-loading seam.

`load_model()` returns an object satisfying the `Scorer` protocol. Everyone
downstream (the service, the data layer, the UI) depends ONLY on
`score(rows) -> [ScoreResult]` and the {label, direction, weight} reason-code shape,
so the active backend is a config choice with no contract impact.

Backend selection (`SCORING_BACKEND`, default "v2"):
  * "v2"        -> `V2Scorer` (model_service/v2): peer-relative, multi-factor, calibrated
                   (default).
  * "heuristic" -> the original four-driver `HeuristicScorer` ("v1" is an alias).
  * "ml"        -> the trained `MLScorer` (model_service/ml). If its artifact is
                   missing, the optional ML deps are absent, or it raises while
                   loading, we LOG it and FALL BACK to the heuristic — the service
                   must never fail to start (or crash) because of the model.

Any other value resolves to v2.
"""
from __future__ import annotations

import logging
import os
from typing import Protocol, runtime_checkable

from .heuristic import HeuristicScorer, ScoreResult

logger = logging.getLogger("pulsescore.model")


@runtime_checkable
class Scorer(Protocol):
    name: str

    def score(self, rows: list[dict]) -> list[ScoreResult]:
        ...


def _load_ml_scorer() -> Scorer:
    """Build the trained MLScorer, or fall back to the heuristic on ANY problem.

    Kept in its own function so the heavy ML imports (lightgbm/shap) are only pulled
    in when SCORING_BACKEND=ml — the default path never imports them."""
    try:
        from pathlib import Path

        from .ml.config import MODEL_PATH as DEFAULT_MODEL_PATH

        # Path read live so a deployment (or test) can repoint it via env without a
        # config reload; defaults to the configured artifact location.
        env_path = os.environ.get("PULSESCORE_MODEL_PATH")
        model_path = Path(env_path) if env_path else DEFAULT_MODEL_PATH

        if not model_path.exists():
            logger.warning(
                "SCORING_BACKEND=ml but no model artifact at %s — falling back to "
                "heuristic. Train one with `python -m model_service.ml.train`.",
                model_path)
            return HeuristicScorer()

        from .ml.scorer import MLScorer

        scorer = MLScorer.from_artifact(model_path)
        logger.info("SCORING_BACKEND=ml — loaded trained model '%s'", scorer.name)
        return scorer
    except Exception as exc:  # missing deps, corrupt artifact, version skew, ...
        logger.error(
            "Failed to load ML scorer (%s) — falling back to heuristic.",
            type(exc).__name__)
        return HeuristicScorer()


def load_model(backend: str | None = None) -> Scorer:
    """Return the scorer for `backend`, or per SCORING_BACKEND (read live from the env,
    default 'v2'). Falls back to the heuristic whenever the ML backend cannot be loaded."""
    backend = (backend or os.environ.get("SCORING_BACKEND", "v2")).strip().lower()
    if backend == "ml":
        return _load_ml_scorer()
    if backend in ("heuristic", "v1"):
        return HeuristicScorer()
    from .v2 import V2Scorer
    return V2Scorer()
