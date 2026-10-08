"""ANALYTICS READ LAYER — aggregate, exploratory reads over the GOLD layer (token-only).

Powers the Analytics section (8 tabbed views) the way `query.py` powers the watchlist
and dashboard. It is a strictly ADDITIVE sibling of the query seam: it never writes, never
touches raw/operational tables beyond the computed score layer, and reuses the SAME
cross-cutting guarantees enforced there —

  1. Permission scope — `query._scope_division` pins a manager to their own division.
  2. Min-segment suppression — any cohort/segment smaller than `SUPPRESS_N` (5) is dropped
     server-side, so a small group can never be de-anonymized off an aggregate.
  3. Token-only output — no name/email ever crosses this boundary (managers are returned
     as a token; the UI resolves names at its own audited boundary).
  4. Audit — every call writes one tamper-evident `audit_log` row.

Each reader returns aggregated series + a `data_status` field so the UI renders the right
state instead of faking data:
    ok | needs_source:<name> | insufficient_history | needs_field:<name>

The data-maturity ladder is honored honestly: a ⚠️ source-gated view (engagement,
comp-vs-market) renders live only when its source table actually has rows for the scoped
cohort, otherwise it returns a `needs_source:*` status the UI turns into a "connect a
source" card. The 🔧 capability-gated items (intervention-ROI, seasonal forecast,
compa-ratio) return their gated status and NEVER a fabricated number.

GOLD reads only: `employee_core`, `label_attrition`, `manager_team`, `compensation`,
`engagement`, `career_mobility`, `external_market` + the computed score tables
(`scores`, `capital_metrics`, `reason_codes`). Fairness reads protected traits ONLY
through `data/bias_audit.py` (the sole audited gateway), never the columns directly.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from . import bias_audit
from .audit import write_audit
from .auth import Actor
from .query import QueryValidationError, _scope_division

# Minimum segment size — any cohort/segment below this is suppressed server-side.
SUPPRESS_N = 5
# Trends need at least this many distinct monthly buckets to be meaningful.
_MIN_TREND_BUCKETS = 3

# Tenure-band filter -> [low, high) in years (high=None means open-ended).
_TENURE_BANDS: dict[str, tuple[float, float | None]] = {
    "<2": (0.0, 2.0),
    "2-5": (2.0, 5.0),
    "5-10": (5.0, 10.0),
    "10+": (10.0, None),
}


# ============================================================================
# Shared helpers — cohort selection, suppression, small numeric utilities.
# ============================================================================
def _ph(n: int) -> str:
    """A run of `n` bound-parameter placeholders for a safe IN (...) clause."""
    return ",".join("?" * n)


def _validate(level: int | None, tenure_band: str | None, date_range: str | None) -> tuple[int | None, str | None]:
    """Validate the filter params common to every endpoint. Fail closed."""
    if level is not None and not (1 <= int(level) <= 10):
        raise QueryValidationError("level must be between 1 and 10")
    if tenure_band is not None and tenure_band not in _TENURE_BANDS:
        raise QueryValidationError(f"tenure_band must be one of: {sorted(_TENURE_BANDS)}")
    start = end = None
    if date_range:
        try:
            start, end = date_range.split(":", 1)
        except ValueError as exc:
            raise QueryValidationError("date_range must be 'START:END' (ISO dates)") from exc
        if start > end:
            raise QueryValidationError("date_range start must be <= end")
    return start, end


def _cohort_filter(
    actor: Actor,
    *,
    division: str | None,
    manager: str | None,
    location: str | None,
    level: int | None,
    tenure_band: str | None,
) -> tuple[list[str], list[Any], str | None]:
    """Build the scoped WHERE for `employee_core ec`. Scope is enforced regardless of
    any division the caller asked for (a manager can never widen past their own)."""
    eff = _scope_division(actor, division)
    where: list[str] = []
    params: list[Any] = []
    if eff is not None:
        where.append("ec.division = ?")
        params.append(eff)
    if manager:
        where.append("ec.manager_token = ?")
        params.append(manager)
    if location:
        where.append("LOWER(ec.location) = ?")
        params.append(location.lower())
    if level is not None:
        where.append("ec.level = ?")
        params.append(int(level))
    if tenure_band:
        lo, hi = _TENURE_BANDS[tenure_band]
        sub = "EXISTS (SELECT 1 FROM career_mobility cm WHERE cm.employee_token = ec.employee_token AND cm.tenure >= ?"
        params.append(lo)
        if hi is not None:
            sub += " AND cm.tenure < ?"
            params.append(hi)
        sub += ")"
        where.append(sub)
    return where, params, eff


def _cohort_rows(conn: sqlite3.Connection, where: list[str], params: list[Any]) -> list[sqlite3.Row]:
    """The scoped cohort (token-only core fields) the endpoint aggregates over."""
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    return conn.execute(
        f"""SELECT ec.employee_token AS token, ec.division AS division, ec.level AS level,
                   ec.location AS location, ec.manager_token AS manager_token,
                   ec.hire_date AS hire_date
            FROM employee_core ec {where_sql}""",
        params,
    ).fetchall()


def _suppress_small(
    segments: list[dict[str, Any]], *, count_key: str = "headcount", n: int = SUPPRESS_N
) -> tuple[list[dict[str, Any]], int]:
    """Drop any segment whose count is below `n` (de-anonymization guard)."""
    kept = [s for s in segments if int(s.get(count_key, 0) or 0) >= n]
    return kept, len(segments) - len(kept)


def _histogram(values: list[float], edges: list[float]) -> list[dict[str, Any]]:
    """Bucket `values` into [edges[i], edges[i+1]) bins; the last bin includes its top."""
    bins = [{"lo": edges[i], "hi": edges[i + 1], "count": 0} for i in range(len(edges) - 1)]
    last = len(bins) - 1
    for v in values:
        if v is None:
            continue
        for i in range(len(bins)):
            lo, hi = edges[i], edges[i + 1]
            if (lo <= v < hi) or (i == last and v == hi):
                bins[i]["count"] += 1
                break
    return bins


def _box(values: list[float]) -> dict[str, Any] | None:
    """Five-number summary (box-plot stats) for a distribution; None if empty."""
    vs = sorted(v for v in values if v is not None)
    if not vs:
        return None

    def q(p: float) -> float:
        if len(vs) == 1:
            return float(vs[0])
        idx = p * (len(vs) - 1)
        lo = int(idx)
        frac = idx - lo
        if lo + 1 >= len(vs):
            return float(vs[lo])
        return float(vs[lo] + (vs[lo + 1] - vs[lo]) * frac)

    return {"min": round(float(vs[0]), 2), "q1": round(q(0.25), 2), "median": round(q(0.5), 2),
            "q3": round(q(0.75), 2), "max": round(float(vs[-1]), 2), "n": len(vs)}


def _km_survival(observations: list[tuple[float, int]]) -> list[dict[str, Any]]:
    """Kaplan-Meier survival curve from (duration_years, event) — event=1 is a departure,
    0 is a still-active (censored) observation. Returns step points {t, survival}."""
    if not observations:
        return []
    event_times = sorted({d for d, e in observations if e == 1})
    points = [{"t": 0.0, "survival": 1.0}]
    s = 1.0
    for t in event_times:
        n_at_risk = sum(1 for d, _ in observations if d >= t)
        d_events = sum(1 for d, e in observations if d == t and e == 1)
        if n_at_risk > 0:
            s *= 1 - d_events / n_at_risk
        points.append({"t": round(float(t), 2), "survival": round(s, 4)})
    return points


def _latest_scores(conn: sqlite3.Connection, tokens: list[str]) -> dict[str, dict[str, Any]]:
    """Latest flight_risk / value_score per token in the cohort."""
    if not tokens:
        return {}
    ph = _ph(len(tokens))
    rows = conn.execute(
        f"""SELECT sc.employee_token AS t, sc.flight_risk AS fr, sc.value_score AS vs
            FROM scores sc
            JOIN (SELECT employee_token, MAX(as_of_date) AS m FROM scores
                  WHERE employee_token IN ({ph}) GROUP BY employee_token) ls
              ON ls.employee_token = sc.employee_token AND ls.m = sc.as_of_date""",
        tokens,
    ).fetchall()
    return {r["t"]: {"flight_risk": r["fr"], "value_score": r["vs"]} for r in rows}


def _scope_dict(eff: str | None, manager: str | None, location: str | None,
                level: int | None, tenure_band: str | None) -> dict[str, Any]:
    return {"division": eff, "manager": manager, "location": location,
            "level": level, "tenure_band": tenure_band}


def _audit(conn: sqlite3.Connection, actor: Actor, name: str,
           scope: dict[str, Any], count: int) -> None:
    write_audit(conn, actor_email=actor.email, action=f"analytics_{name}",
                filters=scope, result_count=count)


# ============================================================================
# Tab 1 — Turnover & Retention  ✅ (trend ⚠️ until ≥3 months of term dates)
# ============================================================================
def get_turnover(
    conn: sqlite3.Connection, *, actor: Actor, division: str | None = None,
    manager: str | None = None, location: str | None = None, level: int | None = None,
    tenure_band: str | None = None, date_range: str | None = None,
) -> dict[str, Any]:
    start, end = _validate(level, tenure_band, date_range)
    where, params, eff = _cohort_filter(actor, division=division, manager=manager,
                                        location=location, level=level, tenure_band=tenure_band)
    scope = _scope_dict(eff, manager, location, level, tenure_band)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    rows = conn.execute(
        f"""SELECT ec.employee_token AS token, ec.division AS division,
                   la.attrition AS attrition, la.voluntary AS voluntary, la.regretted AS regretted,
                   la.term_date AS term_date, cm.tenure AS tenure, cm.tenure_at_exit AS tenure_at_exit
            FROM employee_core ec
            LEFT JOIN label_attrition la ON la.employee_token = ec.employee_token
            LEFT JOIN career_mobility cm ON cm.employee_token = ec.employee_token
            {where_sql}""",
        params,
    ).fetchall()

    headcount = len(rows)
    leavers = [r for r in rows if r["attrition"] == 1]
    voluntary = sum(1 for r in leavers if r["voluntary"] == 1)
    regretted = sum(1 for r in leavers if r["regretted"] == 1)
    headline = {
        "headcount": headcount,
        "leavers": len(leavers),
        "attrition_rate": round(len(leavers) / headcount, 4) if headcount else 0.0,
        "voluntary": voluntary,
        "involuntary": len(leavers) - voluntary,
        "regretted": regretted,
    }

    # Attrition by division segment (suppress segments < N).
    seg: dict[str, dict[str, Any]] = {}
    for r in rows:
        d = seg.setdefault(r["division"], {"segment": r["division"], "headcount": 0, "leavers": 0})
        d["headcount"] += 1
        if r["attrition"] == 1:
            d["leavers"] += 1
    for d in seg.values():
        d["rate"] = round(d["leavers"] / d["headcount"], 4) if d["headcount"] else 0.0
    by_segment, suppressed = _suppress_small(sorted(seg.values(), key=lambda x: -x["rate"]))

    # Survival curve (KM) from tenure (active = censored) / tenure_at_exit (leaver = event).
    observations: list[tuple[float, int]] = []
    for r in rows:
        if r["attrition"] == 1 and r["tenure_at_exit"] is not None:
            observations.append((float(r["tenure_at_exit"]), 1))
        elif r["tenure"] is not None:
            observations.append((float(r["tenure"]), 0))
    survival = _km_survival(observations)

    # Monthly attrition trend across term_date (optionally clamped to date_range).
    months: dict[str, int] = {}
    for r in leavers:
        td = r["term_date"]
        if not td:
            continue
        if start and td < start:
            continue
        if end and td > end:
            continue
        months[td[:7]] = months.get(td[:7], 0) + 1
    if len(months) >= _MIN_TREND_BUCKETS:
        trend = {"data_status": "ok",
                 "points": [{"month": m, "leavers": months[m]} for m in sorted(months)]}
    else:
        trend = {"data_status": "insufficient_history", "points": []}

    _audit(conn, actor, "turnover", scope, headcount)
    return {"headline": headline, "by_segment": by_segment, "suppressed_segments": suppressed,
            "survival": survival, "trend": trend, "data_status": "ok", "scope": scope}


# ============================================================================
# Tab 2 — Risk Drivers  ✅
# ============================================================================
def get_drivers(
    conn: sqlite3.Connection, *, actor: Actor, division: str | None = None,
    manager: str | None = None, location: str | None = None, level: int | None = None,
    tenure_band: str | None = None, date_range: str | None = None,
) -> dict[str, Any]:
    _validate(level, tenure_band, date_range)
    where, params, eff = _cohort_filter(actor, division=division, manager=manager,
                                        location=location, level=level, tenure_band=tenure_band)
    scope = _scope_dict(eff, manager, location, level, tenure_band)
    cohort = _cohort_rows(conn, where, params)
    tokens = [r["token"] for r in cohort]

    top_drivers: list[dict[str, Any]] = []
    distribution: list[dict[str, Any]] = []
    risk_vs_attrition: dict[str, Any] = {}
    if tokens:
        ph = _ph(len(tokens))
        # Top drivers — mean weight per (label, direction) at each token's latest score date.
        dr = conn.execute(
            f"""SELECT rc.label AS label, rc.direction AS direction,
                       AVG(rc.weight) AS w, COUNT(*) AS n
                FROM reason_codes rc
                JOIN (SELECT employee_token, MAX(as_of_date) AS m FROM scores
                      WHERE employee_token IN ({ph}) GROUP BY employee_token) ls
                  ON ls.employee_token = rc.employee_token AND ls.m = rc.as_of_date
                WHERE rc.metric = 'retention_risk' AND rc.employee_token IN ({ph})
                GROUP BY rc.label, rc.direction
                ORDER BY w DESC""",
            tokens + tokens,
        ).fetchall()
        top_drivers = [{"label": r["label"], "direction": r["direction"],
                        "mean_weight": round(r["w"], 3), "count": r["n"]} for r in dr][:8]

        latest = _latest_scores(conn, tokens)
        distribution = _histogram([s["flight_risk"] for s in latest.values()],
                                  [0, 20, 40, 60, 80, 100])

        lv = conn.execute(
            f"SELECT employee_token FROM label_attrition WHERE attrition = 1 AND employee_token IN ({ph})",
            tokens,
        ).fetchall()
        leaver_set = {r["employee_token"] for r in lv}
        leaver_fr = [latest[t]["flight_risk"] for t in leaver_set if t in latest]
        stayer_fr = [latest[t]["flight_risk"] for t in tokens if t not in leaver_set and t in latest]
        risk_vs_attrition = {
            "leaver_mean_flight_risk": round(sum(leaver_fr) / len(leaver_fr), 2) if leaver_fr else None,
            "stayer_mean_flight_risk": round(sum(stayer_fr) / len(stayer_fr), 2) if stayer_fr else None,
        }

    _audit(conn, actor, "drivers", scope, len(tokens))
    return {"top_drivers": top_drivers, "distribution": distribution,
            "risk_vs_attrition": risk_vs_attrition, "data_status": "ok", "scope": scope}


# ============================================================================
# Tab 5 — Managers & Teams  ✅
# ============================================================================
def get_managers(
    conn: sqlite3.Connection, *, actor: Actor, division: str | None = None,
    manager: str | None = None, location: str | None = None, level: int | None = None,
    tenure_band: str | None = None, date_range: str | None = None,
) -> dict[str, Any]:
    _validate(level, tenure_band, date_range)
    where, params, eff = _cohort_filter(actor, division=division, manager=manager,
                                        location=location, level=level, tenure_band=tenure_band)
    scope = _scope_dict(eff, manager, location, level, tenure_band)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    rows = conn.execute(
        f"""SELECT mt.manager_token AS manager_token, mt.team_turnover_rate AS turnover,
                   mt.span_of_control AS span, mt.peers_departed_recently AS peers_departed,
                   mt.manager_performance_rating AS rating
            FROM manager_team mt
            JOIN employee_core ec ON ec.employee_token = mt.manager_token
            {where_sql}""",
        params,
    ).fetchall()

    managers = [{"manager_token": r["manager_token"], "team_turnover_rate": r["turnover"],
                 "span_of_control": r["span"], "peers_departed_recently": r["peers_departed"],
                 "manager_performance_rating": r["rating"], "headcount": r["span"] or 0}
                for r in rows]
    # Suppress managers whose span-of-control is below N (small-team de-anonymization guard).
    by_manager, suppressed = _suppress_small(
        sorted(managers, key=lambda x: -(x["team_turnover_rate"] or 0)))
    for m in by_manager:
        m.pop("headcount", None)

    span_distribution = _histogram([m["span_of_control"] for m in by_manager if m["span_of_control"]],
                                   [0, 3, 6, 9, 12, 20])
    contagion = sorted(
        ({"manager_token": m["manager_token"], "peers_departed_recently": m["peers_departed_recently"]}
         for m in by_manager if (m["peers_departed_recently"] or 0) > 0),
        key=lambda x: -(x["peers_departed_recently"] or 0))

    _audit(conn, actor, "managers", scope, len(rows))
    return {"by_manager": by_manager, "suppressed_managers": suppressed,
            "span_distribution": span_distribution, "contagion": contagion,
            "data_status": "ok", "scope": scope}


# ============================================================================
# Tab 6 — Fairness & Bias Audit  ✅ (admin-only, via the bias-audit gateway only)
# ============================================================================
def get_fairness(
    conn: sqlite3.Connection, *, actor: Actor, metric: str = "flight_risk", **_ignored: Any
) -> dict[str, Any]:
    """Built ENTIRELY on `bias_audit.get_bias_audit_cohort` — the only audited path to the
    walled protected traits. Never reads gender/birth_year_band/marital_status directly.
    Admin-only (the gateway returns empty + an audited denial for non-admins)."""
    scope = {"metric": metric}
    traits: list[dict[str, Any]] = []
    for trait in bias_audit.list_auditable_traits():
        cohort = bias_audit.get_bias_audit_cohort(conn, trait, actor=actor, metric=metric)
        # Suppress cohorts below N (de-anonymization guard) BEFORE computing the ratio.
        cohorts = [c for c in cohort["cohorts"] if (c["count"] or 0) >= SUPPRESS_N]
        means = [c["mean"] for c in cohorts if c["mean"] is not None]
        adverse_impact_ratio = (round(min(means) / max(means), 3)
                                if means and max(means) else None)
        traits.append({
            "trait": trait, "cohorts": cohorts,
            "adverse_impact_ratio": adverse_impact_ratio,
            # 4/5ths rule: a ratio below 0.8 is the classic adverse-impact flag.
            "four_fifths_pass": (adverse_impact_ratio >= 0.8) if adverse_impact_ratio is not None else None,
        })

    restricted = not actor.is_admin
    _audit(conn, actor, "fairness", scope, 0 if restricted else len(traits))
    return {"traits": traits, "metric": metric, "restricted": restricted,
            "data_status": "ok", "scope": scope}


# ============================================================================
# Tab 7 — Cost & Scenario  ✅ (intervention-ROI 🔧 always locked)
# ============================================================================
def get_cost(
    conn: sqlite3.Connection, *, actor: Actor, division: str | None = None,
    manager: str | None = None, location: str | None = None, level: int | None = None,
    tenure_band: str | None = None, date_range: str | None = None, scenario_n: int = 10,
) -> dict[str, Any]:
    _validate(level, tenure_band, date_range)
    where, params, eff = _cohort_filter(actor, division=division, manager=manager,
                                        location=location, level=level, tenure_band=tenure_band)
    scope = _scope_dict(eff, manager, location, level, tenure_band)
    cohort = _cohort_rows(conn, where, params)
    tokens = [r["token"] for r in cohort]
    div_by_token = {r["token"]: r["division"] for r in cohort}

    by_division: list[dict[str, Any]] = []
    distribution: list[dict[str, Any]] = []
    scenario: list[dict[str, Any]] = []
    total_at_risk = 0.0
    if tokens:
        ph = _ph(len(tokens))
        cost_rows = conn.execute(
            f"""SELECT cm.employee_token AS t, cm.amount AS amount
                FROM capital_metrics cm
                JOIN (SELECT employee_token, MAX(as_of_date) AS m FROM capital_metrics
                      WHERE metric = 'cost_to_lose' AND employee_token IN ({ph})
                      GROUP BY employee_token) lc
                  ON lc.employee_token = cm.employee_token AND lc.m = cm.as_of_date
                WHERE cm.metric = 'cost_to_lose'""",
            tokens,
        ).fetchall()
        cost_by_token = {r["t"]: r["amount"] for r in cost_rows}
        latest = _latest_scores(conn, tokens)

        per_token: list[dict[str, Any]] = []
        div_acc: dict[str, dict[str, Any]] = {}
        for t in tokens:
            cost = cost_by_token.get(t)
            fr = latest.get(t, {}).get("flight_risk")
            if cost is None or fr is None:
                continue
            at_risk = cost * (fr / 100.0)
            total_at_risk += at_risk
            per_token.append({"token": t, "cost_to_lose": round(cost, 2),
                              "flight_risk": fr, "cost_at_risk": round(at_risk, 2)})
            d = div_acc.setdefault(div_by_token.get(t, "?"),
                                   {"segment": div_by_token.get(t, "?"), "headcount": 0, "cost_at_risk": 0.0})
            d["headcount"] += 1
            d["cost_at_risk"] += at_risk

        for d in div_acc.values():
            d["cost_at_risk"] = round(d["cost_at_risk"], 2)
        by_division, _ = _suppress_small(sorted(div_acc.values(), key=lambda x: -x["cost_at_risk"]))
        distribution = _histogram([p["cost_to_lose"] for p in per_token],
                                  [0, 50000, 100000, 200000, 400000, 1000000])
        scenario = sorted(per_token, key=lambda x: -x["cost_at_risk"])[:max(1, int(scenario_n))]

    _audit(conn, actor, "cost", scope, len(tokens))
    return {"total_at_risk": round(total_at_risk, 2), "by_division": by_division,
            "distribution": distribution, "scenario": scenario,
            # 🔧 ROI of an intervention is NOT computable yet (interventions table is empty
            # by design + no causal effect estimate). Never a fabricated number.
            "intervention_roi": {"data_status": "needs_source:interventions"},
            "data_status": "ok", "scope": scope}


# ============================================================================
# Tab 3 — Compensation & Pay Equity  ✅ / ⚠️ (market) / 🔧 (compa-ratio)
# ============================================================================
def get_compensation(
    conn: sqlite3.Connection, *, actor: Actor, division: str | None = None,
    manager: str | None = None, location: str | None = None, level: int | None = None,
    tenure_band: str | None = None, date_range: str | None = None,
) -> dict[str, Any]:
    _validate(level, tenure_band, date_range)
    where, params, eff = _cohort_filter(actor, division=division, manager=manager,
                                        location=location, level=level, tenure_band=tenure_band)
    scope = _scope_dict(eff, manager, location, level, tenure_band)
    cohort = _cohort_rows(conn, where, params)
    tokens = [r["token"] for r in cohort]
    level_by_token = {r["token"]: r["level"] for r in cohort}

    by_level: list[dict[str, Any]] = []
    percentile_distribution: list[dict[str, Any]] = []
    market: dict[str, Any] = {"data_status": "needs_source:external_market"}
    data_status = "needs_source:compensation"
    if tokens:
        ph = _ph(len(tokens))
        comp = conn.execute(
            f"""SELECT employee_token AS t, base_salary AS salary,
                       pay_percentile_in_role AS pct, comp_gap_vs_market AS gap
                FROM compensation WHERE employee_token IN ({ph})""",
            tokens,
        ).fetchall()
        if comp:
            data_status = "ok"
            by_lvl: dict[int, list[float]] = {}
            for r in comp:
                by_lvl.setdefault(level_by_token.get(r["t"], 0), []).append(r["salary"])
            segs = [{"segment": f"L{lvl}", "headcount": len(sals), "box": _box(sals)}
                    for lvl, sals in sorted(by_lvl.items())]
            by_level, _ = _suppress_small(segs)
            percentile_distribution = _histogram(
                [r["pct"] for r in comp if r["pct"] is not None], [0, 20, 40, 60, 80, 100])

            # ⚠️ Comp-vs-market is gated on the licensed benchmark feed (external_market).
            # Renders live ONLY when that source has rows for the scoped cohort.
            em = conn.execute(
                f"SELECT COUNT(*) AS n FROM external_market WHERE employee_token IN ({ph})",
                tokens,
            ).fetchone()["n"]
            if em > 0:
                gaps = [r["gap"] for r in comp if r["gap"] is not None]
                market = {"data_status": "ok",
                          "mean_gap_vs_market": round(sum(gaps) / len(gaps), 4) if gaps else None,
                          "n": len(gaps)}

    _audit(conn, actor, "compensation", scope, len(tokens))
    return {"by_level": by_level, "percentile_distribution": percentile_distribution,
            "market": market,
            # 🔧 strict compa-ratio needs a band-midpoint field that does not exist; fall
            # back to pay_percentile_in_role (above) and flag the missing capability.
            "compa_ratio": {"data_status": "needs_field:compa_ratio", "fallback": "pay_percentile_in_role"},
            "data_status": data_status, "scope": scope}


# ============================================================================
# Tab 4 — Engagement  ⚠️ (source-gated on a survey feed)
# ============================================================================
def get_engagement(
    conn: sqlite3.Connection, *, actor: Actor, division: str | None = None,
    manager: str | None = None, location: str | None = None, level: int | None = None,
    tenure_band: str | None = None, date_range: str | None = None,
) -> dict[str, Any]:
    _validate(level, tenure_band, date_range)
    where, params, eff = _cohort_filter(actor, division=division, manager=manager,
                                        location=location, level=level, tenure_band=tenure_band)
    scope = _scope_dict(eff, manager, location, level, tenure_band)
    cohort = _cohort_rows(conn, where, params)
    tokens = [r["token"] for r in cohort]

    if not tokens:
        _audit(conn, actor, "engagement", scope, 0)
        return {"data_status": "needs_source:engagement", "trend": {"data_status": "insufficient_history", "points": []},
                "headline": {}, "scope": scope}

    ph = _ph(len(tokens))
    # ⚠️ Engagement renders live ONLY when a survey source has rows for the cohort.
    rows = conn.execute(
        f"""SELECT as_of_date AS d, AVG(engagement_score) AS eng, AVG(enps_score) AS enps,
                   AVG(survey_response_rate) AS rr
            FROM engagement WHERE employee_token IN ({ph})
            GROUP BY as_of_date ORDER BY as_of_date""",
        tokens,
    ).fetchall()
    if not rows:
        _audit(conn, actor, "engagement", scope, 0)
        return {"data_status": "needs_source:engagement", "trend": {"data_status": "insufficient_history", "points": []},
                "headline": {}, "scope": scope}

    points = [{"month": r["d"][:7],
               "engagement_score": round(r["eng"], 2) if r["eng"] is not None else None,
               "enps_score": round(r["enps"], 2) if r["enps"] is not None else None}
              for r in rows]
    latest = rows[-1]
    headline = {"engagement_score": round(latest["eng"], 2) if latest["eng"] is not None else None,
                "enps_score": round(latest["enps"], 2) if latest["enps"] is not None else None,
                "response_rate": round(latest["rr"], 4) if latest["rr"] is not None else None}
    trend_status = "ok" if len(points) >= _MIN_TREND_BUCKETS else "insufficient_history"

    _audit(conn, actor, "engagement", scope, len(tokens))
    return {"data_status": "ok", "headline": headline,
            "trend": {"data_status": trend_status, "points": points}, "scope": scope}


# ============================================================================
# Tab 8 — Forecast  🔧 (basic expected-leavers ships; seasonal insufficient_history)
# ============================================================================
def get_forecast(
    conn: sqlite3.Connection, *, actor: Actor, division: str | None = None,
    manager: str | None = None, location: str | None = None, level: int | None = None,
    tenure_band: str | None = None, date_range: str | None = None,
) -> dict[str, Any]:
    _validate(level, tenure_band, date_range)
    where, params, eff = _cohort_filter(actor, division=division, manager=manager,
                                        location=location, level=level, tenure_band=tenure_band)
    scope = _scope_dict(eff, manager, location, level, tenure_band)
    cohort = _cohort_rows(conn, where, params)
    tokens = [r["token"] for r in cohort]
    div_by_token = {r["token"]: r["division"] for r in cohort}

    latest = _latest_scores(conn, tokens)
    expected_leavers = round(sum(s["flight_risk"] / 100.0 for s in latest.values()), 2)
    div_acc: dict[str, dict[str, Any]] = {}
    for t, s in latest.items():
        d = div_acc.setdefault(div_by_token.get(t, "?"),
                               {"segment": div_by_token.get(t, "?"), "headcount": 0, "expected_leavers": 0.0})
        d["headcount"] += 1
        d["expected_leavers"] += s["flight_risk"] / 100.0
    for d in div_acc.values():
        d["expected_leavers"] = round(d["expected_leavers"], 2)
    by_division, _ = _suppress_small(sorted(div_acc.values(), key=lambda x: -x["expected_leavers"]))

    _audit(conn, actor, "forecast", scope, len(tokens))
    return {"expected_leavers": {"value": expected_leavers, "is_estimate": True},
            "by_division": by_division,
            # 🔧 a robust seasonal forecast needs accumulated history (only ≤6 months exist).
            "seasonal": {"data_status": "insufficient_history"},
            "data_status": "ok", "scope": scope}
