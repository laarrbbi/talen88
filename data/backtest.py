"""Backtest + fairness check for the scoring seam (v1 vs v2 side by side).

    # with the scoring service running on :8001
    python -m data.backtest                     # latest snapshot, prints a summary
    python -m data.backtest --json report.json  # also writes the full report

For each scorer it scores everyone on one snapshot and compares the scores with who
actually left (`label_attrition`):

  * AUC — chance a random leaver outranks a random stayer (0.5 = coin flip, 1.0 = perfect).
  * Average precision — how leaver-dense the top of the ranking is.
  * Top-10% capture — share of all leavers found in the riskiest 10%.
  * Brier score — mean squared error of the predicted chance of leaving (lower is better;
    v1 has no probability, so its 0-100 score / 100 stands in).
  * Calibration — predicted vs actual leave rate by decile.

Fairness (v2, current employees only, aggregate via data/bias_audit.py):
  * high-risk flag rate per protected cohort + the four-fifths adverse-impact ratio;
  * a proxy screen: factors whose cohort means differ by >= 0.3 SD need review.

CAVEAT printed with every report: the synthetic generator creates leavers from these same
drivers, so a strong result here shows the pipeline works, not that it predicts real
people. Real validation needs a client's leaver history with a time-based split.
"""
from __future__ import annotations

import argparse
import json
from typing import Any, Callable

from . import bias_audit
from .auth import Actor
from .db import get_connection
from .refresh import ScoreBatch, _feature_rows, http_score_batch

CAVEAT = ("Demo data: leavers were generated from these same drivers, so this shows the "
          "pipeline works, not that it predicts real people. Validate on a client's "
          "leaver history with a time-based split before relying on it.")
# Factors screened for acting as a stand-in for a protected trait.
PROXY_FACTORS = ("commute_km", "pto_used", "overtime", "after_hours_activity",
                 "unvested_equity_usd", "median_prior_tenure", "job_hopping_rate",
                 "comp_gap", "pay_percentile_in_role", "months_since_promotion")
_SYSTEM = Actor(email="system:backtest", name="Backtest", role="admin", division=None)


# ---- metrics (pure functions) --------------------------------------------------------------

def auc(scores: list[float], labels: list[int]) -> float | None:
    """Area under the ROC curve via ranks (ties count half)."""
    pairs = sorted(zip(scores, labels))
    n_pos = sum(labels)
    n_neg = len(labels) - n_pos
    if not n_pos or not n_neg:
        return None
    rank_sum, i = 0.0, 0
    while i < len(pairs):
        j = i
        while j < len(pairs) and pairs[j][0] == pairs[i][0]:
            j += 1
        avg_rank = (i + 1 + j) / 2          # ranks are 1-based
        rank_sum += avg_rank * sum(1 for k in range(i, j) if pairs[k][1] == 1)
        i = j
    return (rank_sum - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)


def average_precision(scores: list[float], labels: list[int]) -> float | None:
    n_pos = sum(labels)
    if not n_pos:
        return None
    hits, total = 0, 0.0
    for k, (_, y) in enumerate(sorted(zip(scores, labels), key=lambda p: -p[0]), start=1):
        if y:
            hits += 1
            total += hits / k
    return total / n_pos


def top_share_capture(scores: list[float], labels: list[int], share: float = 0.10) -> float | None:
    n_pos = sum(labels)
    if not n_pos:
        return None
    k = max(1, round(len(scores) * share))
    top = sorted(zip(scores, labels), key=lambda p: -p[0])[:k]
    return sum(y for _, y in top) / n_pos


def brier(probs: list[float], labels: list[int]) -> float | None:
    if not probs:
        return None
    return sum((p - y) ** 2 for p, y in zip(probs, labels)) / len(probs)


def calibration(probs: list[float], labels: list[int], bins: int = 10) -> list[dict]:
    ordered = sorted(zip(probs, labels))
    out = []
    for b in range(bins):
        chunk = ordered[b * len(ordered) // bins:(b + 1) * len(ordered) // bins]
        if chunk:
            out.append({"decile": b + 1, "n": len(chunk),
                        "predicted": round(sum(p for p, _ in chunk) / len(chunk), 4),
                        "actual": round(sum(y for _, y in chunk) / len(chunk), 4)})
    return out


def _r(x: float | None, nd: int = 3) -> float | None:
    return None if x is None else round(x, nd)


# ---- the report -----------------------------------------------------------------------------

def run_backtest(conn, *, batches: dict[str, ScoreBatch], as_of_date: str | None = None,
                 actor: Actor = _SYSTEM) -> dict[str, Any]:
    """Score one snapshot with each named batch and compare against attrition labels."""
    if as_of_date is None:
        as_of_date = conn.execute("SELECT MAX(as_of_date) FROM feature_snapshots").fetchone()[0]
    rows = _feature_rows(conn, as_of_date)
    left = {r[0] for r in conn.execute(
        "SELECT employee_token FROM label_attrition WHERE attrition = 1")}
    tokens = [r["employee_token"] for r in rows]
    labels = [1 if t in left else 0 for t in tokens]

    report: dict[str, Any] = {"as_of_date": as_of_date, "people": len(tokens),
                              "leavers": sum(labels), "caveat": CAVEAT, "scorers": {}}
    v2_results: dict[str, dict] = {}
    for name, batch in batches.items():
        results = {r["employee_token"]: r for r in batch(as_of_date, rows)}
        scores = [float(results[t]["flight_risk"]) for t in tokens]
        probs = [results[t].get("leave_probability") for t in tokens]
        has_prob = all(p is not None for p in probs)
        probs = [float(p) if has_prob else s / 100.0 for p, s in zip(probs, scores)]
        report["scorers"][name] = {
            "auc": _r(auc(scores, labels)),
            "average_precision": _r(average_precision(scores, labels)),
            "top10_capture": _r(top_share_capture(scores, labels)),
            "brier": _r(brier(probs, labels), 4),
            "probability_is_calibrated": has_prob,
            "calibration": calibration(probs, labels),
        }
        if name == "v2":
            v2_results = results

    if v2_results:
        current = [r for r in rows if r.get("reference", True)]
        flagged = {r["employee_token"]: v2_results[r["employee_token"]]["flight_risk"] >= 70
                   for r in current}
        fairness = []
        for trait in bias_audit.list_auditable_traits():
            fairness.append(bias_audit.flag_rate_by_cohort(conn, trait, flagged, actor=actor))
        proxies = []
        for factor in PROXY_FACTORS:
            values = {r["employee_token"]: r.get(factor) for r in current}
            for trait in bias_audit.list_auditable_traits():
                res = bias_audit.factor_proxy_screen(conn, trait, values, actor=actor)
                if res["max_gap_sd"] is not None:
                    proxies.append({"factor": factor, "trait": trait,
                                    "max_gap_sd": res["max_gap_sd"], "p_value": res["p_value"],
                                    "needs_review": res["needs_review"]})
        report["fairness"] = {"high_risk_flag_rates": fairness, "proxy_screen": proxies}
    return report


def _summary(report: dict[str, Any]) -> str:
    lines = [f"Backtest on {report['as_of_date']}: {report['people']} people, "
             f"{report['leavers']} leavers", ""]
    lines.append(f"{'scorer':8} {'AUC':>6} {'AvgPrec':>8} {'Top10%':>7} {'Brier':>7}")
    for name, m in report["scorers"].items():
        lines.append(f"{name:8} {m['auc']:>6} {m['average_precision']:>8} "
                     f"{m['top10_capture']:>7} {m['brier']:>7}")
    fair = report.get("fairness")
    if fair:
        lines += ["", "Fairness (v2, current employees): high-risk flag rate by cohort"]
        for t in fair["high_risk_flag_rates"]:
            rates = ", ".join(f"{c['cohort']} {c['flag_rate']:.1%}" for c in t["cohorts"])
            lines.append(f"  {t['trait']:16} AIR={t['adverse_impact_ratio']} p={t['p_value']} "
                         f"-> {t['verdict']}  ({rates})")
        flagged = [p for p in fair["proxy_screen"] if p["needs_review"]]
        lines.append("  proxy screen: " + (
            "no factor differs meaningfully (>= 0.3 SD, p < 0.05) across cohorts" if not flagged
            else "review " + "; ".join(f"{p['factor']} vs {p['trait']} ({p['max_gap_sd']} SD, "
                                       f"p={p['p_value']})" for p in flagged)))
    lines += ["", "Note: " + report["caveat"]]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m data.backtest")
    parser.add_argument("--date", help="snapshot date (default: latest)")
    parser.add_argument("--json", help="write the full report to this file")
    args = parser.parse_args(argv)
    batches: dict[str, Callable] = {"v1": http_score_batch(scorer="v1"),
                                    "v2": http_score_batch(scorer="v2")}
    conn = get_connection()
    try:
        report = run_backtest(conn, batches=batches, as_of_date=args.date)
    finally:
        conn.close()
    print(_summary(report))
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(report, fh, indent=2)


if __name__ == "__main__":
    main()
