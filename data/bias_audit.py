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

import math
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


# Cohorts smaller than this are dropped from every aggregate (de-anonymization guard).
MIN_COHORT = 5
# A gap is only called out when it is also unlikely to be chance.
SIGNIFICANCE = 0.05


def _fisher_exact(a: int, b: int, c: int, d: int) -> float:
    """Two-sided Fisher exact p-value for the 2x2 table [[a, b], [c, d]]."""
    row1, row2, col1, n = a + b, c + d, a + c, a + b + c + d

    def prob(x: int) -> float:
        return math.comb(col1, x) * math.comb(n - col1, row1 - x) / math.comb(n, row1)

    observed = prob(a)
    lo, hi = max(0, row1 - (n - col1)), min(row1, col1)
    return min(1.0, sum(p for x in range(lo, hi + 1) if (p := prob(x)) <= observed * (1 + 1e-9)))


def _welch_p(m1: float, v1: float, n1: int, m2: float, v2: float, n2: int) -> float:
    """Two-sided p-value for a difference in means (Welch, normal approximation)."""
    se = math.sqrt(v1 / n1 + v2 / n2)
    if se == 0:
        return 1.0 if m1 == m2 else 0.0
    return math.erfc(abs(m1 - m2) / se / math.sqrt(2))


def _check_trait(trait: str) -> None:
    if trait not in canonical.BIAS_AUDIT_COLUMNS:
        raise ValueError(
            f"{trait!r} is not a bias-audit trait; allowed: {sorted(canonical.BIAS_AUDIT_COLUMNS)}")


def _cohorts_for(conn: sqlite3.Connection, trait: str, tokens: list[str]) -> dict[str, list[str]]:
    """trait value -> tokens, for the given tokens only. Internal: never returned as-is."""
    _check_trait(trait)
    groups: dict[str, list[str]] = {}
    if not tokens:
        return groups
    placeholders = ",".join("?" for _ in tokens)
    for r in conn.execute(
            f"SELECT employee_token AS t, {trait} AS v FROM employee_core "
            f"WHERE {trait} IS NOT NULL AND employee_token IN ({placeholders})", tokens):
        groups.setdefault(r["v"], []).append(r["t"])
    return groups


def flag_rate_by_cohort(
    conn: sqlite3.Connection, trait: str, flagged: dict[str, bool], *, actor: Actor,
) -> dict[str, Any]:
    """Share of each protected cohort that a scorer flagged (e.g. high risk), plus the
    adverse-impact ratio (lowest rate / highest rate; < 0.8 fails the four-fifths rule).

    `flagged` maps token -> flag for the population being audited. Returns aggregates
    only; cohorts under MIN_COHORT people are suppressed. Admin-only and audited.
    """
    _check_trait(trait)
    if not actor.is_admin:
        write_audit(conn, actor_email=actor.email, action="bias_audit_flag_rate",
                    filters={"trait": trait, "denied": True}, result_count=0)
        return {"trait": trait, "cohorts": [], "adverse_impact_ratio": None}
    groups = _cohorts_for(conn, trait, list(flagged))
    cohorts, counts = [], []
    for value, toks in sorted(groups.items()):
        if len(toks) < MIN_COHORT:
            continue
        n_flagged = sum(1 for t in toks if flagged[t])
        counts.append((n_flagged, len(toks)))
        cohorts.append({"cohort": value, "count": len(toks),
                        "flag_rate": round(n_flagged / len(toks), 4)})
    rates = [c["flag_rate"] for c in cohorts]
    air = round(min(rates) / max(rates), 3) if rates and max(rates) > 0 else None
    # Compare the most- and least-flagged cohorts; the ratio alone is noisy on small counts.
    p_value = None
    if len(counts) >= 2 and air is not None:
        hi = max(range(len(counts)), key=lambda k: rates[k])
        lo = min(range(len(counts)), key=lambda k: rates[k])
        (fa, na), (fb, nb) = counts[hi], counts[lo]
        # Picking the extreme pair out of k cohorts: Bonferroni-adjust for k(k-1)/2 pairs.
        pairs = len(counts) * (len(counts) - 1) // 2
        p_value = round(min(1.0, _fisher_exact(fa, na - fa, fb, nb - fb) * pairs), 4)
    if air is None:
        verdict = "insufficient data"
    elif air >= 0.8:
        verdict = "pass"
    elif p_value is not None and p_value < SIGNIFICANCE:
        verdict = "review"
    else:
        verdict = "gap not significant"
    write_audit(conn, actor_email=actor.email, action="bias_audit_flag_rate",
                filters={"trait": trait, "population": len(flagged)}, result_count=len(cohorts))
    return {"trait": trait, "cohorts": cohorts, "adverse_impact_ratio": air,
            "four_fifths_pass": None if air is None else air >= 0.8,
            "p_value": p_value, "verdict": verdict}


def factor_proxy_screen(
    conn: sqlite3.Connection, trait: str, values: dict[str, float], *, actor: Actor,
) -> dict[str, Any]:
    """How strongly a scoring factor differs across protected cohorts: the largest gap
    between cohort means, in standard deviations of the factor. A large gap (>= 0.3)
    means the factor may act as a stand-in for the protected trait and needs review.

    Returns aggregates only (per-cohort count + mean); cohorts under MIN_COHORT people
    are suppressed. Admin-only and audited.
    """
    _check_trait(trait)
    if not actor.is_admin:
        write_audit(conn, actor_email=actor.email, action="bias_audit_proxy_screen",
                    filters={"trait": trait, "denied": True}, result_count=0)
        return {"trait": trait, "cohorts": [], "max_gap_sd": None}
    present = {t: float(v) for t, v in values.items() if v is not None}
    groups = _cohorts_for(conn, trait, list(present))
    cohorts = []
    for value, toks in sorted(groups.items()):
        if len(toks) < MIN_COHORT:
            continue
        cohorts.append({"cohort": value, "count": len(toks),
                        "mean": round(sum(present[t] for t in toks) / len(toks), 4)})
    all_vals = list(present.values())
    gap = p_value = None
    if len(cohorts) >= 2 and len(all_vals) >= 2:
        mean = sum(all_vals) / len(all_vals)
        sd = (sum((v - mean) ** 2 for v in all_vals) / len(all_vals)) ** 0.5
        means = [c["mean"] for c in cohorts]
        gap = round((max(means) - min(means)) / sd, 3) if sd > 0 else 0.0
        # Is the largest gap more than chance? (Welch test, highest vs lowest cohort.)
        stats = []
        for value, toks in sorted(groups.items()):
            if len(toks) < MIN_COHORT:
                continue
            vals = [present[t] for t in toks]
            m = sum(vals) / len(vals)
            stats.append((m, sum((v - m) ** 2 for v in vals) / max(1, len(vals) - 1), len(vals)))
        hi, lo = max(stats), min(stats)
        pairs = len(stats) * (len(stats) - 1) // 2      # Bonferroni, as above
        p_value = round(min(1.0, _welch_p(*hi, *lo) * pairs), 4)
    write_audit(conn, actor_email=actor.email, action="bias_audit_proxy_screen",
                filters={"trait": trait, "population": len(present)}, result_count=len(cohorts))
    return {"trait": trait, "cohorts": cohorts, "max_gap_sd": gap, "p_value": p_value,
            "needs_review": None if gap is None else (gap >= 0.3 and p_value < SIGNIFICANCE)}
