"""Validation metrics + bias report.

Deliberately reports AUC / precision / recall / F1 / confusion as the headline —
NOT accuracy, which is misleading on this imbalanced set (predicting "stays" for
everyone scores ~84% accuracy while catching zero leavers).
"""
from __future__ import annotations

import numpy as np
from sklearn.metrics import (
    confusion_matrix,
    f1_score,
    precision_recall_curve,
    precision_score,
    recall_score,
    roc_auc_score,
)


def compute_metrics(y_true, y_prob, threshold: float) -> dict:
    """All headline classification metrics at a given decision threshold.
    `leaver_*` are the positive (Attrition=Yes) class — the class we care about."""
    y_true = np.asarray(y_true).astype(int)
    y_prob = np.asarray(y_prob, dtype=float)
    y_pred = (y_prob >= threshold).astype(int)

    tn, fp, fn, tp = confusion_matrix(y_true, y_pred, labels=[0, 1]).ravel()
    return {
        "roc_auc": float(roc_auc_score(y_true, y_prob)),
        "threshold": float(threshold),
        "leaver_precision": float(precision_score(y_true, y_pred, zero_division=0)),
        "leaver_recall": float(recall_score(y_true, y_pred, zero_division=0)),
        "leaver_f1": float(f1_score(y_true, y_pred, zero_division=0)),
        "stayer_precision": float(precision_score(y_true, y_pred, pos_label=0, zero_division=0)),
        "stayer_recall": float(recall_score(y_true, y_pred, pos_label=0, zero_division=0)),
        "confusion": {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)},
        "support": {"leavers": int(y_true.sum()), "stayers": int((1 - y_true).sum())},
    }


def pick_threshold(y_true, y_prob, recall_target: float) -> float:
    """Lowest-precision-cost threshold that still hits the leaver-recall target.

    Among thresholds achieving recall >= target, pick the one with the highest F1
    (best precision/recall balance). Falls back to the recall-target-maximizing point
    if none reach it. This is how we keep recall above chance on the rare class.
    """
    y_true = np.asarray(y_true).astype(int)
    y_prob = np.asarray(y_prob, dtype=float)
    precision, recall, thresholds = precision_recall_curve(y_true, y_prob)
    # precision_recall_curve returns one more precision/recall point than thresholds.
    best_t, best_f1 = 0.5, -1.0
    for p, r, t in zip(precision[:-1], recall[:-1], thresholds):
        if r >= recall_target:
            f1 = 0.0 if (p + r) == 0 else 2 * p * r / (p + r)
            if f1 > best_f1:
                best_f1, best_t = f1, float(t)
    if best_f1 >= 0:
        return best_t
    # Nothing reached the target -> use the highest-recall threshold available.
    return float(thresholds[int(np.argmax(recall[:-1]))]) if len(thresholds) else 0.5


def bias_report(y_true, y_prob, threshold: float, protected) -> dict:
    """Per-group score/error distribution across each protected attribute.

    For each attribute (e.g. Gender, AgeBand) report, per group: n, mean predicted
    score (prob*100), selection rate (flagged high-risk), and the leaver recall + FPR.
    `disparity` is max-min of the selection rate across groups — a quick parity gap.
    A REAL deployment needs a full fairness audit (equalized odds, calibration, legal
    review); this is a smoke check only.
    """
    y_true = np.asarray(y_true).astype(int)
    y_prob = np.asarray(y_prob, dtype=float)
    y_pred = (y_prob >= threshold).astype(int)

    report: dict = {}
    for attr in protected.columns:
        groups = {}
        col = np.asarray(protected[attr].astype(str))
        for g in sorted(set(col)):
            m = col == g
            yt, yp, sc = y_true[m], y_pred[m], y_prob[m]
            pos = yt == 1
            neg = yt == 0
            groups[g] = {
                "n": int(m.sum()),
                "mean_score": float(sc.mean() * 100) if m.any() else 0.0,
                "selection_rate": float(yp.mean()) if m.any() else 0.0,
                "leaver_recall": float(yp[pos].mean()) if pos.any() else None,
                "false_positive_rate": float(yp[neg].mean()) if neg.any() else None,
            }
        rates = [v["selection_rate"] for v in groups.values()]
        report[attr] = {
            "groups": groups,
            "selection_rate_disparity": float(max(rates) - min(rates)) if rates else 0.0,
        }
    return report


def format_report(metrics: dict, bias: dict) -> str:
    """Human-readable block for the train script / metrics test output."""
    c = metrics["confusion"]
    lines = [
        "=== Validation metrics (held-out test split) ===",
        f"  ROC-AUC            : {metrics['roc_auc']:.4f}   (threshold-independent)",
        f"  Decision threshold : {metrics['threshold']:.3f}   (tuned for leaver recall)",
        f"  Leaver precision   : {metrics['leaver_precision']:.4f}",
        f"  Leaver recall      : {metrics['leaver_recall']:.4f}   <- catches actual leavers",
        f"  Leaver F1          : {metrics['leaver_f1']:.4f}",
        f"  Stayer precision   : {metrics['stayer_precision']:.4f}",
        f"  Stayer recall      : {metrics['stayer_recall']:.4f}",
        "  Confusion matrix (rows=actual, cols=pred):",
        "                 pred_stay  pred_leave",
        f"      actual_stay   {c['tn']:>6}     {c['fp']:>6}",
        f"      actual_leave  {c['fn']:>6}     {c['tp']:>6}",
        "  (Accuracy is intentionally NOT the headline — it is misleading on ~16% positives.)",
        "",
        "=== Bias check (smoke test — full fairness audit required pre-deploy) ===",
    ]
    for attr, rep in bias.items():
        lines.append(f"  {attr}: selection-rate disparity = {rep['selection_rate_disparity']:.4f}")
        for g, v in rep["groups"].items():
            rec = "n/a" if v["leaver_recall"] is None else f"{v['leaver_recall']:.2f}"
            fpr = "n/a" if v["false_positive_rate"] is None else f"{v['false_positive_rate']:.2f}"
            lines.append(
                f"      {g:<8} n={v['n']:<5} mean_score={v['mean_score']:5.1f} "
                f"sel_rate={v['selection_rate']:.2f} recall={rec} fpr={fpr}")
    return "\n".join(lines)
