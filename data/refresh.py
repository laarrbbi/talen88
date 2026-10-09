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

import os
from typing import Callable

import httpx

from .audit import write_audit
from .db import get_connection

# A scoring backend: (as_of_date, rows) -> list of result dicts matching the
# scoring-seam response (employee_token, flight_risk, value_score, reason_codes).
ScoreBatch = Callable[[str, list[dict]], list[dict]]

DEFAULT_MODEL_URL = os.environ.get("PULSESCORE_MODEL_URL", "http://localhost:8001")


def http_score_batch(url: str = DEFAULT_MODEL_URL) -> ScoreBatch:
    """Default backend: POST a batch to the model service scoring seam."""
    def _batch(as_of_date: str, rows: list[dict]) -> list[dict]:
        resp = httpx.post(f"{url}/score", json={"as_of_date": as_of_date, "rows": rows},
                          timeout=30.0)
        resp.raise_for_status()
        return resp.json()["results"]
    return _batch


def _feature_rows(conn, as_of_date: str) -> list[dict]:
    """Build scoring-seam feature rows for one snapshot date (tokens + numeric
    features only — no names ever cross the seam). Joins the Employee-360 attributes
    and aggregates the skills graph into numeric features. Engagement is sent ONLY
    when the employee consented (no covert signal crosses the seam)."""
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
    return [dict(r) for r in rows]


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

    for date in dates:
        rows = _feature_rows(conn, date)
        if not rows:
            continue
        results = batch(date, rows)
        for res in results:
            token = res["employee_token"]
            prior = history.get(token)
            prev_risk = prior[-1][1] if prior else None
            trend = 0 if prev_risk is None else int(res["flight_risk"]) - prev_risk
            history.setdefault(token, []).append((date, int(res["flight_risk"])))

            score_buffer.append((token, date, int(res["flight_risk"]),
                                 int(res["value_score"]), trend))
            # Panel scores + per-metric reason codes (long-format). retention_risk's
            # codes live here too, tagged by metric -> single source of reason codes.
            for m in res.get("metrics", []):
                if m["score"] is None:        # e.g. engagement w/o consent
                    continue
                metric_buffer.append((token, date, m["metric"], int(m["score"])))
                for rc in m["reason_codes"]:
                    reason_buffer.append((token, date, m["metric"], rc["label"],
                                          rc["direction"], float(rc["weight"])))
            for cm in res.get("capital", []):
                capital_buffer.append((token, date, cm["metric"], float(cm["amount"]),
                                       cm["unit"], 1 if cm["is_estimate"] else 0))

    conn.executemany(
        "INSERT INTO scores (employee_token, as_of_date, flight_risk, value_score, "
        "risk_trend) VALUES (?,?,?,?,?)", score_buffer,
    )
    conn.executemany(
        "INSERT INTO reason_codes (employee_token, as_of_date, metric, label, direction, "
        "weight) VALUES (?,?,?,?,?,?)", reason_buffer,
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


def main() -> None:
    conn = get_connection()
    try:
        n = refresh_scores(conn)
    finally:
        conn.close()
    print(f"Refreshed {n} score rows across all snapshots via {DEFAULT_MODEL_URL}.")
    print("Scores + reason codes are now populated; the dashboard/watchlist are live.")


if __name__ == "__main__":
    main()
