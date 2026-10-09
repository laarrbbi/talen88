"""The ONLY read path to the walled bias-audit traits (§4 Table 1).

`gender`, `birth_year_band` and `marital_status` live in `employee_core` but are listed
in `canonical.SCORING_EXCLUDED_COLUMNS`: they may NEVER enter a scoring feature set. They
exist for one purpose — auditing the model for disparate impact — and this module is the
sole, audited gateway to them. It returns AGGREGATE cohort statistics (counts + mean
outcomes per protected group), never per-individual trait values, so a fairness analyst
can compare score distributions across groups without the trait ever flowing to a scorer.

Two structural guarantees:
  * `model_service` MUST NOT import this module (enforced by test). The wall is therefore
    physical: the scorer has no code path that can even reach a protected trait.
  * Every read is permission-scoped (bias audit is an admin/fairness function) and writes
    an `audit_log` row — protected-trait access is itself audited.

Only a name from `canonical.BIAS_AUDIT_COLUMNS` is ever interpolated into SQL; any other
trait is rejected before a query is built.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from . import canonical
from .audit import write_audit
from .auth import Actor

# Outcome columns a fairness audit may aggregate per cohort (whitelist — never user SQL).
_OUTCOME_COLUMNS = {"flight_risk": "s.flight_risk", "value_score": "s.value_score"}


def list_auditable_traits() -> list[str]:
    """The protected traits this gateway exposes — exactly the bias-audit wall set."""
    return sorted(canonical.BIAS_AUDIT_COLUMNS)


def get_bias_audit_cohort(
    conn: sqlite3.Connection,
    trait: str,
    *,
    actor: Actor,
    metric: str = "flight_risk",
) -> dict[str, Any]:
    """Aggregate the latest `metric` by protected `trait` cohort, for a fairness audit.

    Returns {trait, metric, cohorts: [{cohort, count, mean}]}. Admin-only: a non-admin
    caller receives an empty result and an audited denial. Raises ValueError if `trait`
    is not a walled bias-audit column or `metric` is not an auditable outcome.
    """
    if trait not in canonical.BIAS_AUDIT_COLUMNS:
        raise ValueError(
            f"{trait!r} is not a bias-audit trait; allowed: {sorted(canonical.BIAS_AUDIT_COLUMNS)}")
    if metric not in _OUTCOME_COLUMNS:
        raise ValueError(f"{metric!r} is not an auditable outcome; allowed: {sorted(_OUTCOME_COLUMNS)}")

    if not actor.is_admin:
        write_audit(conn, actor_email=actor.email, action="bias_audit_cohort",
                    filters={"trait": trait, "metric": metric, "denied": True}, result_count=0)
        return {"trait": trait, "metric": metric, "cohorts": []}

    metric_sql = _OUTCOME_COLUMNS[metric]
    # `trait` is whitelisted above; only the latest score per CURRENT employee is
    # aggregated (people who left are not part of today's score distribution).
    rows = conn.execute(
        f"""
        SELECT ec.{trait} AS cohort, COUNT(*) AS n, AVG({metric_sql}) AS mean
        FROM employee_core ec
        LEFT JOIN (
            SELECT sc.* FROM scores sc
            JOIN (SELECT employee_token, MAX(as_of_date) AS m
                  FROM scores GROUP BY employee_token) ls
              ON ls.employee_token = sc.employee_token AND ls.m = sc.as_of_date
        ) s ON s.employee_token = ec.employee_token
        WHERE ec.{trait} IS NOT NULL AND {canonical.current_employee_sql('ec')}
        GROUP BY ec.{trait}
        ORDER BY ec.{trait}
        """
    ).fetchall()

    cohorts = [{"cohort": r["cohort"], "count": r["n"],
                "mean": None if r["mean"] is None else round(r["mean"], 2)} for r in rows]
    write_audit(conn, actor_email=actor.email, action="bias_audit_cohort",
                filters={"trait": trait, "metric": metric}, result_count=len(cohorts))
    return {"trait": trait, "metric": metric, "cohorts": cohorts}
