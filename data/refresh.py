"""Score refresh — wires the scoring seam into the data layer.

Reads token-keyed feature snapshots, sends them to the model service's scoring
seam (POST /score), and persists the returned flight_risk / value_score /
reason_codes back into the data layer. `risk_trend` is computed HERE (the data
layer owns history): it is each period's flight_risk minus the prior period's.

Crossing the seam over HTTP keeps scoring fully decoupled — the data layer never
imports model code. For testing/offline use, `refresh_scores` accepts an injected
`score_batch` callable so the persistence logic can be exercised without a running
server.

    # ensure the model service is running on :8001, then:
    python -m data.refresh
"""
from __future__ import annotations

import argparse
import inspect
import json
import os
import statistics
import time
from datetime import date
from typing import Callable

import httpx

from .audit import write_audit
from .db import get_connection

# A scoring backend: (as_of_date, rows) -> list of result dicts matching the
# scoring-seam response (employee_token, flight_risk, value_score, reason_codes).
ScoreBatch = Callable[[str, list[dict]], list[dict]]

DEFAULT_MODEL_URL = os.environ.get("PULSESCORE_MODEL_URL", "http://localhost:8001")


def http_score_batch(url: str = DEFAULT_MODEL_URL, scorer: str | None = None) -> ScoreBatch:
    """Default backend: POST a batch to the model service scoring seam. `scorer` asks for
    a specific backend ("v1" / "v2"); None uses the service's configured one."""
    def _batch(as_of_date: str, rows: list[dict], base_rate: float | None = None) -> list[dict]:
        body: dict = {"as_of_date": as_of_date, "rows": rows}
        if base_rate is not None:
            body["base_rate"] = base_rate
        if scorer is not None:
            body["scorer"] = scorer
        resp = httpx.post(f"{url}/score", json=body, timeout=60.0)
        resp.raise_for_status()
        return resp.json()["results"]
    return _batch


# ---- feature extraction ------------------------------------------------------------------
# Raw facts only: dates become "months since", JSON lists become counts. All scoring logic
# (ranking against peers, weights, calibration) lives behind the scoring seam.

def _json(text: str | None, default):
    try:
        v = json.loads(text) if text else default
    except (TypeError, ValueError):
        return default
    return v if isinstance(v, type(default)) else default


def _months_between(earlier: str | None, later: str) -> float | None:
    """Whole-ish months from `earlier` to `later` (ISO dates); None if missing/after."""
    try:
        a, b = date.fromisoformat(earlier[:10]), date.fromisoformat(later[:10])
    except (TypeError, ValueError):
        return None
    days = (b - a).days
    return round(days / 30.44, 1) if days >= 0 else None


def _static_features(conn) -> dict[str, dict]:
    """Per-token facts from current-state canonical tables (no monthly history)."""
    out: dict[str, dict] = {}

    def put(token: str, **kw) -> None:
        out.setdefault(token, {}).update(kw)

    for r in conn.execute(
            "SELECT employee_token AS t, base_salary, currency, equity_deferred, vesting_dates, "
            "last_raise_date, percent_salary_hike, pay_percentile_in_role, comp_gap_vs_market "
            "FROM compensation"):
        equity = _json(r["equity_deferred"], {})
        put(r["t"], base_salary=r["base_salary"], salary_currency=r["currency"],
            pay_percentile_in_role=r["pay_percentile_in_role"],
            comp_gap_vs_market=r["comp_gap_vs_market"], last_raise_pct=r["percent_salary_hike"],
            unvested_equity_usd=equity.get("unvested_usd"),
            _last_raise_date=r["last_raise_date"],
            _vest_dates=[d for d in _json(r["vesting_dates"], []) if isinstance(d, str)])
    for r in conn.execute(
            "SELECT employee_token AS t, promotion_velocity, years_in_current_role, "
            "internal_application_history FROM career_mobility"):
        apps = [a.get("date") for a in _json(r["internal_application_history"], [])
                if isinstance(a, dict) and a.get("date")]
        put(r["t"], promotion_velocity=r["promotion_velocity"],
            years_in_current_role=r["years_in_current_role"], _application_dates=apps)
    for r in conn.execute(
            "SELECT employee_token AS t, review_ratings, goal_attainment, performance_trend "
            "FROM performance"):
        scores = [x.get("score") for x in _json(r["review_ratings"], [])
                  if isinstance(x, dict) and isinstance(x.get("score"), (int, float))]
        stdev = statistics.pstdev(scores) if len(scores) >= 2 else None
        put(r["t"], goal_attainment=r["goal_attainment"],
            performance_trend=r["performance_trend"], review_rating_stdev=stdev)
    for r in conn.execute(
            "SELECT employee_token AS t, certifications, training_times_last_year, "
            "skill_acquisition_date FROM skills_credentials"):
        acquired = _json(r["skill_acquisition_date"], {})
        put(r["t"], certifications_count=len(_json(r["certifications"], [])),
            training_times_last_year=r["training_times_last_year"],
            _skill_dates=[d for d in acquired.values() if isinstance(d, str)])
    for r in conn.execute(
            "SELECT employee_token AS t, median_prior_tenure, job_hopping_rate FROM cv_trajectory"):
        put(r["t"], median_prior_tenure=r["median_prior_tenure"],
            job_hopping_rate=r["job_hopping_rate"])
    # Team context comes from the employee's manager's row.
    for r in conn.execute(
            "SELECT e.token AS t, mt.team_turnover_rate, mt.peers_departed_recently, "
            "mt.manager_performance_rating, mt.time_since_manager_change "
            "FROM employees e JOIN manager_team mt ON mt.manager_token = e.manager_token"):
        put(r["t"], team_turnover_rate=r["team_turnover_rate"],
            peers_departed_recently=r["peers_departed_recently"],
            manager_rating=r["manager_performance_rating"],
            months_since_manager_change=r["time_since_manager_change"])
    for r in conn.execute("SELECT employee_token AS t, signal, value "
                          "FROM performance_division_signals"):
        out.setdefault(r["t"], {}).setdefault("division_signals", {})[r["signal"]] = r["value"]
    for r in conn.execute("SELECT employee_token AS t, monitoring_consent FROM engagement_prefs"):
        put(r["t"], monitoring_consent=r["monitoring_consent"])
    for r in conn.execute("SELECT ec.employee_token AS t, ec.status, la.term_date "
                          "FROM employee_core ec LEFT JOIN label_attrition la "
                          "ON la.employee_token = ec.employee_token"):
        put(r["t"], _status=r["status"], _term_date=r["term_date"])
    return out


def _dated_features(conn, as_of_date: str) -> dict[str, dict]:
    """Per-token facts from the monthly snapshot tables for one date."""
    out: dict[str, dict] = {}
    for r in conn.execute(
            "SELECT employee_token AS t, engagement_score, engagement_trend, enps_score, "
            "job_satisfaction, work_life_balance, survey_text_sentiment, recognition_count, "
            "manager_effectiveness_survey_score FROM engagement WHERE as_of_date = ?",
            (as_of_date,)):
        out.setdefault(r["t"], {}).update(
            engagement_score=r["engagement_score"], engagement_trend=r["engagement_trend"],
            enps_score=r["enps_score"], job_satisfaction=r["job_satisfaction"],
            work_life_balance=r["work_life_balance"], survey_sentiment=r["survey_text_sentiment"],
            recognition_count=r["recognition_count"],
            manager_effectiveness_score=r["manager_effectiveness_survey_score"])
    for r in conn.execute(
            "SELECT employee_token AS t, skill_market_demand, industry_hiring_heat, "
            "regional_comp_movement FROM external_market WHERE as_of_date = ?", (as_of_date,)):
        out.setdefault(r["t"], {}).update(
            skill_market_demand=r["skill_market_demand"],
            industry_hiring_heat=r["industry_hiring_heat"],
            regional_comp_movement=r["regional_comp_movement"])
    for r in conn.execute(
            "SELECT employee_token AS t, pto_used, overtime_flag, distance_from_home "
            "FROM lifecycle WHERE as_of_date = ?", (as_of_date,)):
        out.setdefault(r["t"], {}).update(pto_used=r["pto_used"], overtime=r["overtime_flag"],
                                          commute_km=r["distance_from_home"])
    for r in conn.execute(
            "SELECT employee_token AS t, skill_uniqueness FROM features_engineered "
            "WHERE score_snapshot_date = ?", (as_of_date,)):
        out.setdefault(r["t"], {})["skill_uniqueness"] = r["skill_uniqueness"]
    for r in conn.execute(
            "SELECT employee_token AS t, after_hours_activity, bridge_score, "
            "betweenness_centrality FROM collaboration_metadata WHERE as_of_date = ?",
            (as_of_date,)):
        out.setdefault(r["t"], {}).update(after_hours_activity=r["after_hours_activity"],
                                          bridge_score=r["bridge_score"],
                                          betweenness=r["betweenness_centrality"])
    return out


_ENGAGEMENT_FIELDS = ("engagement_score", "engagement_trend", "enps_score", "job_satisfaction",
                      "work_life_balance", "survey_sentiment", "recognition_count",
                      "manager_effectiveness_score")
_MONITORING_FIELDS = ("after_hours_activity", "bridge_score", "betweenness")


def _feature_rows(conn, as_of_date: str, static: dict[str, dict] | None = None) -> list[dict]:
    """Build scoring-seam feature rows for one snapshot date (tokens + numeric
    features only — no names ever cross the seam). Joins the Employee-360 attributes,
    aggregates the skills graph, and adds the canonical factors scoring v2 reads.

    Consent is enforced HERE as well as in the scorer: engagement/survey fields are sent
    only for employees with consented_signals = 1, collaboration fields only with
    monitoring_consent = 1. People who had left by `as_of_date` are sent with
    reference = False (scored, but never part of anyone's peer group)."""
    static = _static_features(conn) if static is None else static
    dated = _dated_features(conn, as_of_date)
    rows = conn.execute(
        """
        SELECT f.employee_token, f.comp_gap, f.months_since_promotion, f.tenure_months,
               f.manager_changes_12mo, e.level, e.division,
               COALESCE(a.perf_rating, 3.0)        AS perf_rating,
               CASE WHEN a.consented_signals = 1 THEN a.engagement_pulse ELSE NULL END
                                                   AS engagement_pulse,
               COALESCE(a.learning_hours_12mo, 0)  AS learning_hours_12mo,
               COALESCE(a.internal_moves, 0)       AS internal_moves,
               COALESCE(a.span_of_control, 0)      AS span_of_control,
               COALESCE(a.consented_signals, 0)    AS consented_signals,
               (SELECT COUNT(*) FROM employee_skills es
                  WHERE es.employee_token = f.employee_token) AS skill_count,
               (SELECT COALESCE(AVG(es.proficiency), 0.0) FROM employee_skills es
                  WHERE es.employee_token = f.employee_token) AS skill_avg_prof,
               (SELECT COUNT(DISTINCT sk.family) FROM employee_skills es
                  JOIN skills sk ON sk.id = es.skill_id
                  WHERE es.employee_token = f.employee_token) AS skill_family_breadth
        FROM feature_snapshots f
        JOIN employees e ON e.token = f.employee_token
        LEFT JOIN employee_attributes a
          ON a.employee_token = f.employee_token AND a.as_of_date = f.as_of_date
        WHERE f.as_of_date = ?
        """,
        (as_of_date,),
    ).fetchall()

    out = []
    for r in rows:
        row = dict(r)
        token = row["employee_token"]
        facts = {**static.get(token, {}), **dated.get(token, {})}
        consented = row["consented_signals"] == 1
        monitored = int(facts.get("monitoring_consent") or 0) == 1
        for k, v in facts.items():
            if k.startswith("_"):
                continue
            if k in _ENGAGEMENT_FIELDS and not consented:
                continue
            if k in _MONITORING_FIELDS and not monitored:
                continue
            row[k] = v
        row["months_since_raise"] = _months_between(facts.get("_last_raise_date"), as_of_date)
        past_vests = [m for m in (_months_between(d, as_of_date)
                                  for d in facts.get("_vest_dates", [])) if m is not None]
        row["months_since_vest"] = min(past_vests) if past_vests else None
        app_ages = [m for m in (_months_between(d, as_of_date)
                                for d in facts.get("_application_dates", [])) if m is not None]
        row["internal_applications_12mo"] = sum(1 for m in app_ages if m <= 12)
        row["months_since_internal_application"] = min(app_ages) if app_ages else None
        if "_skill_dates" in facts:
            row["skills_acquired_12mo"] = sum(
                1 for m in (_months_between(d, as_of_date) for d in facts["_skill_dates"])
                if m is not None and m <= 12)
        term = facts.get("_term_date")
        left = facts.get("_status") == "terminated" and (term is None or term[:10] <= as_of_date)
        row["reference"] = not left
        out.append(row)
    return out


def _observed_base_rate(conn, as_of_date: str, current: int, min_leavers: int = 20) -> float | None:
    """The org's leave rate over the 12 months to `as_of_date`, when there are enough
    leavers for it to mean something; otherwise None (the scorer uses its default)."""
    try:
        end = date.fromisoformat(as_of_date)
    except ValueError:
        return None
    try:
        start = end.replace(year=end.year - 1).isoformat()
    except ValueError:                      # 29 February
        start = end.replace(year=end.year - 1, day=28).isoformat()
    n = conn.execute("SELECT COUNT(*) FROM label_attrition WHERE attrition = 1 "
                     "AND term_date > ? AND term_date <= ?", (start, as_of_date)).fetchone()[0]
    if n < min_leavers or current + n == 0:
        return None
    return n / (current + n)


def refresh_scores(conn, *, score_batch: ScoreBatch | None = None) -> int:
    """(Re)compute and persist scores + reason codes for every snapshot date.

    Idempotent: clears existing scores/reason_codes first. Returns the number of
    (employee, date) score rows written.
    """
    batch = score_batch or http_score_batch()

    dates = [r[0] for r in conn.execute(
        "SELECT DISTINCT as_of_date FROM feature_snapshots ORDER BY as_of_date ASC"
    ).fetchall()]

    conn.execute("DELETE FROM scores")
    conn.execute("DELETE FROM reason_codes")
    conn.execute("DELETE FROM metric_scores")
    conn.execute("DELETE FROM capital_metrics")

    # token -> ordered list of (date, flight_risk) for trend computation
    history: dict[str, list[tuple[str, int]]] = {}
    # buffer results so we can compute risk_trend before inserting
    score_buffer: list[tuple] = []
    reason_buffer: list[tuple] = []
    metric_buffer: list[tuple] = []
    capital_buffer: list[tuple] = []

    static = _static_features(conn)
    takes_rate = "base_rate" in inspect.signature(batch).parameters
    for as_of in dates:
        rows = _feature_rows(conn, as_of, static)
        if not rows:
            continue
        if takes_rate:
            current = sum(1 for r in rows if r["reference"])
            results = batch(as_of, rows, base_rate=_observed_base_rate(conn, as_of, current))
        else:
            results = batch(as_of, rows)
        for res in results:
            token = res["employee_token"]
            prior = history.get(token)
            prev_risk = prior[-1][1] if prior else None
            trend = 0 if prev_risk is None else int(res["flight_risk"]) - prev_risk
            history.setdefault(token, []).append((as_of, int(res["flight_risk"])))

            score_buffer.append((token, as_of, int(res["flight_risk"]),
                                 int(res["value_score"]), trend,
                                 res.get("leave_probability"), res.get("risk_percentile"),
                                 res.get("confidence"), res.get("scorer_version")))
            # Panel scores + per-metric reason codes (long-format). retention_risk's
            # codes live here too, tagged by metric -> single source of reason codes.
            for m in res.get("metrics", []):
                if m["score"] is None:        # e.g. engagement w/o consent
                    continue
                metric_buffer.append((token, as_of, m["metric"], int(m["score"])))
                for rc in m["reason_codes"]:
                    reason_buffer.append((token, as_of, m["metric"], rc["label"],
                                          rc["direction"], float(rc["weight"]),
                                          rc.get("detail")))
            for cm in res.get("capital", []):
                capital_buffer.append((token, as_of, cm["metric"], float(cm["amount"]),
                                       cm["unit"], 1 if cm["is_estimate"] else 0))

    conn.executemany(
        "INSERT INTO scores (employee_token, as_of_date, flight_risk, value_score, "
        "risk_trend, leave_probability, risk_percentile, confidence, scorer_version) "
        "VALUES (?,?,?,?,?,?,?,?,?)", score_buffer,
    )
    conn.executemany(
        "INSERT INTO reason_codes (employee_token, as_of_date, metric, label, direction, "
        "weight, detail) VALUES (?,?,?,?,?,?,?)", reason_buffer,
    )
    conn.executemany(
        "INSERT INTO metric_scores (employee_token, as_of_date, metric, score) "
        "VALUES (?,?,?,?)", metric_buffer,
    )
    conn.executemany(
        "INSERT INTO capital_metrics (employee_token, as_of_date, metric, amount, unit, "
        "is_estimate) VALUES (?,?,?,?,?,?)", capital_buffer,
    )
    conn.commit()

    write_audit(conn, actor_email="system", action="refresh_scores",
                filters={"dates": dates}, result_count=len(score_buffer))
    return len(score_buffer)


def wait_for_model(url: str = DEFAULT_MODEL_URL, timeout: float = 60.0) -> None:
    """Block until the scoring service answers /health, or raise TimeoutError."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            if httpx.get(f"{url}/health", timeout=2.0).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        if time.monotonic() >= deadline:
            raise TimeoutError(f"scoring service at {url} not ready after {timeout:.0f}s")
        time.sleep(1.0)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m data.refresh")
    parser.add_argument("--wait", type=float, default=0.0, metavar="SECONDS",
                        help="wait up to SECONDS for the scoring service to come up first")
    args = parser.parse_args(argv)
    if args.wait > 0:
        wait_for_model(timeout=args.wait)
    conn = get_connection()
    try:
        n = refresh_scores(conn)
    finally:
        conn.close()
    print(f"Refreshed {n} score rows across all snapshots via {DEFAULT_MODEL_URL}.")
    print("Scores + reason codes are now populated; the dashboard/watchlist are live.")


if __name__ == "__main__":
    main()
