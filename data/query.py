"""QUERY SEAM — the single source of employee/score data access (token-only).

`get_employees(...)` is the one function the watchlist, dashboard and (in future)
a natural-language agent all call. It returns each employee joined with their
latest score + structured reason codes, identified by **token** — it never reads
or returns a real name. (Names are resolved separately at the UI boundary via
data.identity.resolve_names.)

Cross-cutting guarantees enforced HERE, not in the UI:
  1. Permission scope — a manager only ever sees their own division.
  2. Audit — every call writes a tamper-evident audit_log row.

All SQL is parameterized; the only interpolated identifiers (sort column,
direction) come from fixed whitelists. Inputs are validated up front.
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from typing import Any, Literal

from . import canonical
from .audit import write_audit
from .auth import Actor

RiskBand = Literal["high", "medium", "low"]

# Risk band thresholds on flight_risk (0..100).
_BANDS: dict[str, tuple[int, int]] = {
    "high": (70, 101),     # >= 70
    "medium": (40, 70),    # 40..69
    "low": (0, 40),        # < 40
}

# Whitelist mapping sort field -> fully-qualified, trusted column expression.
_SORT_COLUMNS = {
    "flight_risk": "s.flight_risk",
    "value_score": "s.value_score",
    "risk_trend": "s.risk_trend",
    "comp_gap": "f.comp_gap",
    "level": "e.level",
}
_MAX_LIMIT = 1000


class QueryValidationError(ValueError):
    """Raised on invalid query-seam arguments. Safe to surface as a 400."""


def _validate(
    risk_band: str | None, comp_gap: float | None, sort: str | None, limit: int | None
) -> tuple[str | None, str]:
    """Validate inputs; return (validated order-by SQL, ...). Fail closed on bad input."""
    if risk_band is not None and risk_band not in _BANDS:
        raise QueryValidationError("risk_band must be one of: high, medium, low")
    if comp_gap is not None and not (0.0 <= float(comp_gap) <= 1.0):
        raise QueryValidationError("comp_gap must be between 0 and 1")
    if limit is not None and not (1 <= int(limit) <= _MAX_LIMIT):
        raise QueryValidationError(f"limit must be between 1 and {_MAX_LIMIT}")

    order_sql = "ORDER BY s.flight_risk DESC NULLS LAST, e.token ASC"
    if sort is not None:
        field, _, dir_ = sort.partition(":")
        if field not in _SORT_COLUMNS:
            raise QueryValidationError(f"unknown sort field: {field}")
        direction = "ASC" if dir_.lower() == "asc" else "DESC"
        order_sql = f"ORDER BY {_SORT_COLUMNS[field]} {direction} NULLS LAST, e.token ASC"
    return None, order_sql


def _scope_division(actor: Actor, requested: str | None) -> str | None:
    """Managers are hard-pinned to their own division (never wider); admins free."""
    return requested if actor.is_admin else actor.division


def get_employees(
    conn: sqlite3.Connection,
    *,
    actor: Actor,
    division: str | None = None,
    manager_token: str | None = None,
    risk_band: RiskBand | None = None,
    comp_gap: float | None = None,
    sort: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Return employees (token-identified) joined with their latest score + reason codes."""
    _, order_sql = _validate(risk_band, comp_gap, sort, limit)
    eff_division = _scope_division(actor, division)

    where: list[str] = []
    params: list[Any] = []
    if eff_division is not None:
        where.append("e.division = ?")
        params.append(eff_division)
    if manager_token is not None:
        where.append("e.manager_token = ?")
        params.append(manager_token)
    if comp_gap is not None:
        where.append("f.comp_gap >= ?")
        params.append(float(comp_gap))
    if risk_band is not None:
        lo, hi = _BANDS[risk_band]
        where.append("s.flight_risk >= ? AND s.flight_risk < ?")
        params.extend([lo, hi])
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    limit_sql, tail = "", []
    if limit is not None:
        limit_sql = "LIMIT ?"
        tail = [int(limit)]

    sql = f"""
        SELECT e.token, e.role, e.level, e.division, e.team, e.manager_token,
               e.location, e.hire_date, e.employment_type,
               f.comp_gap, f.months_since_promotion, f.tenure_months, f.manager_changes_12mo,
               s.as_of_date AS score_date, s.flight_risk, s.value_score, s.risk_trend
        FROM employees e
        LEFT JOIN (
            SELECT fs.* FROM feature_snapshots fs
            JOIN (SELECT employee_token, MAX(as_of_date) AS m
                  FROM feature_snapshots GROUP BY employee_token) lf
              ON lf.employee_token = fs.employee_token AND lf.m = fs.as_of_date
        ) f ON f.employee_token = e.token
        LEFT JOIN (
            SELECT sc.* FROM scores sc
            JOIN (SELECT employee_token, MAX(as_of_date) AS m
                  FROM scores GROUP BY employee_token) ls
              ON ls.employee_token = sc.employee_token AND ls.m = sc.as_of_date
        ) s ON s.employee_token = e.token
        {where_sql}
        {order_sql}
        {limit_sql}
    """
    rows = conn.execute(sql, params + tail).fetchall()
    records = [_build_record(conn, r) for r in rows]

    write_audit(
        conn, actor_email=actor.email, action="get_employees",
        filters={"division": eff_division, "manager_token": manager_token,
                 "risk_band": risk_band, "comp_gap": comp_gap, "sort": sort, "limit": limit},
        result_count=len(records),
    )
    return records


def _build_record(conn: sqlite3.Connection, r: sqlite3.Row) -> dict[str, Any]:
    latest_score = None
    if r["flight_risk"] is not None:
        latest_score = {
            "as_of_date": r["score_date"], "flight_risk": r["flight_risk"],
            "value_score": r["value_score"], "risk_trend": r["risk_trend"],
        }
    reason_codes: list[dict[str, Any]] = []
    if r["score_date"] is not None:
        rc_rows = conn.execute(
            "SELECT label, direction, weight FROM reason_codes "
            "WHERE employee_token = ? AND as_of_date = ? AND metric = 'retention_risk' "
            "ORDER BY weight DESC",
            (r["token"], r["score_date"]),
        ).fetchall()
        reason_codes = [dict(rc) for rc in rc_rows]
    return {
        "token": r["token"],
        "role": r["role"], "level": r["level"], "division": r["division"],
        "team": r["team"], "manager_token": r["manager_token"], "location": r["location"],
        "hire_date": r["hire_date"], "employment_type": r["employment_type"],
        "features": None if r["comp_gap"] is None else {
            "comp_gap": r["comp_gap"], "months_since_promotion": r["months_since_promotion"],
            "tenure_months": r["tenure_months"], "manager_changes_12mo": r["manager_changes_12mo"],
        },
        "latest_score": latest_score,
        "reason_codes": reason_codes,
    }


def get_employee_history(
    conn: sqlite3.Connection, token: str, *, actor: Actor
) -> dict[str, Any]:
    """One employee's profile + full score history (token-only), same scope enforced."""
    emp = conn.execute(
        "SELECT token, role, level, division, team, manager_token, location, hire_date, "
        "employment_type FROM employees WHERE token = ?", (token,),
    ).fetchone()
    if emp is None or (not actor.is_admin and emp["division"] != actor.division):
        write_audit(conn, actor_email=actor.email, action="get_employee_history",
                    filters={"token": token, "denied": emp is not None}, result_count=0)
        return {}

    history = [dict(h) for h in conn.execute(
        "SELECT as_of_date, flight_risk, value_score, risk_trend FROM scores "
        "WHERE employee_token = ? ORDER BY as_of_date ASC", (token,),
    ).fetchall()]

    latest_date = history[-1]["as_of_date"] if history else None
    reason_codes: list[dict[str, Any]] = []
    if latest_date is not None:
        reason_codes = [dict(rc) for rc in conn.execute(
            "SELECT label, direction, weight FROM reason_codes "
            "WHERE employee_token = ? AND as_of_date = ? AND metric = 'retention_risk' "
            "ORDER BY weight DESC",
            (token, latest_date),
        ).fetchall()]

    write_audit(conn, actor_email=actor.email, action="get_employee_history",
                filters={"token": token}, result_count=len(history))
    return {**dict(emp), "history": history, "reason_codes": reason_codes}


def _profile_block(r: sqlite3.Row | None) -> dict[str, Any]:
    """Shape the canonical-record enrichment for the Employee-360 profile. Token-only
    and privacy-safe: every field here is already on the people-search surface, so the
    bias-audit columns (gender, birth_year_band, marital_status), the identity-proxy
    columns (school_prestige, named_employer) and the licensed comp_gap_vs_market are
    never read on an individual profile. JSON list columns (certifications, licenses,
    promotions) are parsed; an absent canonical row or bad JSON degrades to empty."""
    import json as _json

    if r is None:
        return {}

    def _arr(text: str | None) -> list:
        try:
            v = _json.loads(text) if text else []
            return v if isinstance(v, list) else []
        except (ValueError, TypeError):
            return []

    return {
        "title": r["title"],
        "status": r["status"],
        "skiplevel_token": r["skiplevel_token"],
        "business_travel_frequency": r["business_travel_frequency"],
        "education_level": r["education_level"],
        "education_field": r["education_field"],
        "certifications": _arr(r["certifications"]),
        "licenses": _arr(r["licenses"]),
        "trainings_last_year": r["training_times_last_year"],
        "years_of_experience": r["years_of_experience"],
        "pay_percentile_in_role": r["pay_percentile_in_role"],
        "pay_band": r["pay_band"],
        "tenure_years": r["tenure"],
        "years_in_current_role": r["years_in_current_role"],
        "total_working_years": r["total_working_years"],
        "months_since_promotion": r["time_since_last_promotion"],
        "promotions": _arr(r["promotions"]),
    }


def get_employee_360(
    conn: sqlite3.Connection, token: str, *, actor: Actor
) -> dict[str, Any]:
    """Unified Employee-360 view (token-only): core record + latest 360 attributes +
    skills graph + latest score/reason codes + canonical profile enrichment. Same scope
    + audit guarantees as the rest of the seam (a manager only ever sees their own
    division). The full score panel (8 scores + capital) is layered on as well.
    """
    emp = conn.execute(
        "SELECT token, role, level, division, team, manager_token, location, hire_date, "
        "employment_type FROM employees WHERE token = ?", (token,),
    ).fetchone()
    if emp is None or (not actor.is_admin and emp["division"] != actor.division):
        write_audit(conn, actor_email=actor.email, action="get_employee_360",
                    filters={"token": token, "denied": emp is not None}, result_count=0)
        return {}

    attrs_row = conn.execute(
        "SELECT as_of_date, perf_rating, engagement_pulse, learning_hours_12mo, "
        "internal_moves, span_of_control, consented_signals FROM employee_attributes "
        "WHERE employee_token = ? ORDER BY as_of_date DESC LIMIT 1", (token,),
    ).fetchone()
    attributes = dict(attrs_row) if attrs_row is not None else None
    # Engagement is only surfaced when the employee consented (no covert monitoring).
    if attributes is not None and attributes["consented_signals"] == 0:
        attributes["engagement_pulse"] = None

    skills = [dict(s) for s in conn.execute(
        "SELECT sk.name, sk.family, es.proficiency FROM employee_skills es "
        "JOIN skills sk ON sk.id = es.skill_id WHERE es.employee_token = ? "
        "ORDER BY es.proficiency DESC, sk.name ASC", (token,),
    ).fetchall()]

    score_row = conn.execute(
        "SELECT as_of_date, flight_risk, value_score, risk_trend FROM scores "
        "WHERE employee_token = ? ORDER BY as_of_date DESC LIMIT 1", (token,),
    ).fetchone()
    latest_score = dict(score_row) if score_row is not None else None
    latest_date = score_row["as_of_date"] if score_row is not None else None

    # Retention-risk trend (the 6 snapshots) for the profile chart.
    history = [dict(h) for h in conn.execute(
        "SELECT as_of_date, flight_risk, value_score, risk_trend FROM scores "
        "WHERE employee_token = ? ORDER BY as_of_date ASC", (token,),
    ).fetchall()]

    # Full intelligence panel + capital for the latest date, each with reason codes.
    panel: list[dict[str, Any]] = []
    capital: list[dict[str, Any]] = []
    reason_codes: list[dict[str, Any]] = []   # retention-risk codes (legacy field)
    if latest_date is not None:
        rc_by_metric: dict[str, list[dict[str, Any]]] = {}
        for rc in conn.execute(
            "SELECT metric, label, direction, weight FROM reason_codes "
            "WHERE employee_token = ? AND as_of_date = ? ORDER BY weight DESC",
            (token, latest_date),
        ).fetchall():
            rc_by_metric.setdefault(rc["metric"], []).append(
                {"label": rc["label"], "direction": rc["direction"], "weight": rc["weight"]})
        reason_codes = rc_by_metric.get("retention_risk", [])
        panel = [{"metric": m["metric"], "score": m["score"],
                  "reason_codes": rc_by_metric.get(m["metric"], [])}
                 for m in conn.execute(
                     "SELECT metric, score FROM metric_scores WHERE employee_token = ? "
                     "AND as_of_date = ? ORDER BY metric", (token, latest_date)).fetchall()]
        capital = [dict(c) for c in conn.execute(
            "SELECT metric, amount, unit, is_estimate FROM capital_metrics "
            "WHERE employee_token = ? AND as_of_date = ? ORDER BY metric",
            (token, latest_date)).fetchall()]

    # Canonical-record enrichment (same token, GOLD layer). LEFT JOINs so a token
    # without a canonical row degrades to nulls rather than failing; only privacy-safe
    # fields are read (see _profile_block — the bias/identity/licensed columns are not).
    prof_row = conn.execute(
        "SELECT ec.title, ec.status, ec.skiplevel_token, ec.business_travel_frequency, "
        "sc.education_level, sc.education_field, sc.certifications, sc.licenses, "
        "sc.training_times_last_year, cv.years_of_experience, "
        "cp.pay_percentile_in_role, cp.pay_band, "
        "cm.tenure, cm.years_in_current_role, cm.total_working_years, "
        "cm.time_since_last_promotion, cm.promotions "
        "FROM employees e "
        "LEFT JOIN employee_core ec ON ec.employee_token = e.token "
        "LEFT JOIN skills_credentials sc ON sc.employee_token = e.token "
        "LEFT JOIN cv_trajectory cv ON cv.employee_token = e.token "
        "LEFT JOIN compensation cp ON cp.employee_token = e.token "
        "LEFT JOIN career_mobility cm ON cm.employee_token = e.token "
        "WHERE e.token = ?", (token,),
    ).fetchone()

    write_audit(conn, actor_email=actor.email, action="get_employee_360",
                filters={"token": token}, result_count=1)
    return {**dict(emp), "attributes": attributes, "skills": skills,
            "latest_score": latest_score, "reason_codes": reason_codes,
            "history": history, "panel": panel, "capital": capital,
            "profile": _profile_block(prof_row)}


def get_dashboard_metrics(conn: sqlite3.Connection, *, actor: Actor) -> dict[str, Any]:
    """Dashboard aggregates, scoped to the actor (token-only points)."""
    employees = get_employees(conn, actor=actor)  # audited + scoped
    scored = [e for e in employees if e["latest_score"] is not None]
    high_risk = [e for e in scored if e["latest_score"]["flight_risk"] >= 70]
    key_person = [e for e in scored if e["latest_score"]["value_score"] >= 70]
    act_now = [e for e in scored if e["latest_score"]["flight_risk"] >= 70
               and e["latest_score"]["value_score"] >= 70]

    # Cost-to-lose (latest, per token) powers the capital quadrant (risk x cost).
    cost_rows = conn.execute(
        "SELECT cm.employee_token, cm.amount FROM capital_metrics cm "
        "JOIN (SELECT employee_token, MAX(as_of_date) AS m FROM capital_metrics "
        "      GROUP BY employee_token) lc "
        "  ON lc.employee_token = cm.employee_token AND lc.m = cm.as_of_date "
        "WHERE cm.metric = 'cost_to_lose'"
    ).fetchall()
    cost_by_token = {r["employee_token"]: r["amount"] for r in cost_rows}

    quadrant = [{"token": e["token"], "division": e["division"],
                 "flight_risk": e["latest_score"]["flight_risk"],
                 "value_score": e["latest_score"]["value_score"],
                 "cost_to_lose": cost_by_token.get(e["token"])} for e in scored]

    by_division: dict[str, dict[str, int]] = {}
    for e in scored:
        d = by_division.setdefault(e["division"], {"total": 0, "high_risk": 0})
        d["total"] += 1
        if e["latest_score"]["flight_risk"] >= 70:
            d["high_risk"] += 1
    division_rollup = sorted(({"division": k, **v} for k, v in by_division.items()),
                             key=lambda x: x["high_risk"], reverse=True)
    return {
        "headline": {"total_scored": len(scored), "high_risk_count": len(high_risk),
                     "key_person_count": len(key_person), "act_now_count": len(act_now)},
        "quadrant": quadrant, "by_division": division_rollup,
    }


# ============================================================================
# PEOPLE SEARCH — multi-field discovery over the canonical record (token-only).
#
# Powers the Company Graph's natural-language search: an LLM parses a free-text
# request into the structured `criteria` below; this reader retrieves the matching
# people. It rides the SAME guarantees as the rest of the seam — permission scope
# (a manager can't reach past their division, even by naming another one),
# parameterized SQL (every value is a bound parameter; the only interpolated text is
# from fixed whitelists), token-only output, and an audit row per call.
#
# Two hard boundaries unique to a broad field search:
#   * THE SCORING WALL is never searchable. gender / birth_year_band / marital_status
#     / school_prestige / named_employer (canonical.SCORING_EXCLUDED_COLUMNS) are
#     fairness/identity-proxy fields — a search filter over them would be a
#     proxy-discrimination path, so they are simply not in SEARCH_FIELDS and any
#     attempt to use them is reported back as unsupported, never honored.
#   * MISSING FIELDS ARE NAMED, NOT FAKED. A criterion the schema does not carry
#     (e.g. spoken language, visa/work-authorization, availability) is echoed in
#     `unsupported_fields` so the caller can say so honestly instead of inventing.
# ============================================================================

# Searchable canonical fields -> how each maps to a parameterized WHERE fragment.
# `kind` drives the fragment builder below; nothing here is ever string-formatted
# with a user value. JSON-array columns (skills/certs/licenses) are matched with a
# bound LIKE so "has skill X" works without parsing JSON in SQL.
SEARCH_FIELDS: dict[str, dict[str, str]] = {
    "division":        {"table": "ec", "col": "division",        "kind": "eq_ci"},
    "location":        {"table": "ec", "col": "location",        "kind": "eq_ci"},
    "role":            {"table": "ec", "col": "role",            "kind": "like_ci"},
    "title":           {"table": "ec", "col": "title",           "kind": "like_ci"},
    "team":            {"table": "ec", "col": "team",            "kind": "like_ci"},
    "employment_type": {"table": "ec", "col": "employment_type", "kind": "eq_ci"},
    "status":          {"table": "ec", "col": "status",          "kind": "eq_ci"},
    "min_level":       {"table": "ec", "col": "level",           "kind": "gte"},
    "skill":           {"table": "sc", "col": "skills",          "kind": "json_like"},
    "certification":   {"table": "sc", "col": "certifications",  "kind": "json_like"},
    "license":         {"table": "sc", "col": "licenses",        "kind": "json_like"},
    "education_level": {"table": "sc", "col": "education_level", "kind": "eq_ci"},
    "education_field": {"table": "sc", "col": "education_field", "kind": "eq_ci"},
    "min_pay_percentile": {"table": "cp", "col": "pay_percentile_in_role", "kind": "gte"},
    "max_pay_percentile": {"table": "cp", "col": "pay_percentile_in_role", "kind": "lte"},
    "min_years_experience": {"table": "cv", "col": "years_of_experience", "kind": "gte"},
    "risk_band":       {"table": "s",  "col": "flight_risk",     "kind": "risk_band"},
    "min_value_score": {"table": "s",  "col": "value_score",     "kind": "gte"},
    "max_flight_risk": {"table": "s",  "col": "flight_risk",     "kind": "lte"},
}
_SEARCH_MAX = 200

# Defense in depth: a walled column must NEVER become searchable. If anyone ever adds a
# bias/identity-proxy field to SEARCH_FIELDS, fail loudly at import rather than ship a
# proxy-discrimination filter. (The mapped *column* names are what the wall guards.)
assert not ({s["col"] for s in SEARCH_FIELDS.values()} & canonical.SCORING_EXCLUDED_COLUMNS), \
    "a scoring-walled column is exposed in SEARCH_FIELDS"


def _search_fragment(field: str, value: Any, params: list[Any]) -> str | None:
    """Build ONE parameterized WHERE fragment for a search field. Appends to `params`
    and returns the SQL (with ? placeholders) or None to skip an empty value."""
    spec = SEARCH_FIELDS[field]
    col = f"{spec['table']}.{spec['col']}"   # both halves are from the fixed whitelist
    kind = spec["kind"]
    if value is None or value == "":
        return None
    if kind == "eq_ci":
        params.append(str(value).lower())
        return f"LOWER({col}) = ?"
    if kind == "like_ci":
        params.append(f"%{str(value).lower()}%")
        return f"LOWER({col}) LIKE ?"
    if kind == "json_like":
        # JSON array text e.g. ["CFA", ...] or [{"name": "CFA"}]; case-insensitive contains.
        params.append(f"%{str(value).lower()}%")
        return f"LOWER({col}) LIKE ?"
    if kind == "gte":
        params.append(float(value))
        return f"{col} >= ?"
    if kind == "lte":
        params.append(float(value))
        return f"{col} <= ?"
    if kind == "risk_band":
        if value not in _BANDS:
            raise QueryValidationError("risk_band must be one of: high, medium, low")
        lo, hi = _BANDS[value]
        params.extend([lo, hi])
        return f"({col} >= ? AND {col} < ?)"
    raise QueryValidationError(f"unsupported search kind for {field}")


def search_people(
    conn: sqlite3.Connection, *, actor: Actor, criteria: dict[str, Any],
    limit: int | None = None,
) -> dict[str, Any]:
    """Multi-field people search over the canonical record, scoped + audited.

    `criteria` is a flat {field: value} dict; recognized keys are SEARCH_FIELDS, plus a
    `skills`/`certifications`/`licenses` list form (ALL must match). Unrecognized keys —
    including any walled field or a field the schema doesn't carry — are returned in
    `unsupported_fields` and never applied. Output rows are token-only.
    """
    where: list[str] = ["e.token = ec.employee_token"]
    params: list[Any] = []
    applied: dict[str, Any] = {}
    unsupported: list[str] = []

    # Scope is enforced regardless of any 'division' the caller asked for.
    eff_division = _scope_division(actor, criteria.get("division") if actor.is_admin else None)
    if eff_division is not None:
        where.append("LOWER(ec.division) = ?")
        params.append(eff_division.lower())
        applied["division"] = eff_division

    # List forms: skill/skills, certification/certifications, license/licenses (ALL match).
    list_aliases = {"skills": "skill", "certifications": "certification", "licenses": "license"}
    for key, value in criteria.items():
        if key == "division":
            continue  # handled by scope above (never widens)
        single = list_aliases.get(key)
        if single:  # a list of required values
            for item in (value or []):
                frag = _search_fragment(single, item, params)
                if frag:
                    where.append(frag)
            applied[key] = value
            continue
        if key not in SEARCH_FIELDS:
            unsupported.append(key)
            continue
        frag = _search_fragment(key, value, params)
        if frag is not None:
            where.append(frag)
            applied[key] = value

    n = min(int(limit), _SEARCH_MAX) if limit else _SEARCH_MAX
    sql = f"""
        SELECT e.token, ec.role, ec.title, ec.level, ec.division, ec.team, ec.location,
               ec.employment_type, ec.status,
               sc.skills, sc.certifications, sc.licenses,
               sc.education_level, sc.education_field,
               cp.pay_percentile_in_role, cv.years_of_experience,
               s.flight_risk, s.value_score
        FROM employees e
        JOIN employee_core ec ON ec.employee_token = e.token
        LEFT JOIN skills_credentials sc ON sc.employee_token = e.token
        LEFT JOIN compensation cp ON cp.employee_token = e.token
        LEFT JOIN cv_trajectory cv ON cv.employee_token = e.token
        LEFT JOIN (
            SELECT sc2.* FROM scores sc2
            JOIN (SELECT employee_token, MAX(as_of_date) AS m
                  FROM scores GROUP BY employee_token) ls
              ON ls.employee_token = sc2.employee_token AND ls.m = sc2.as_of_date
        ) s ON s.employee_token = e.token
        WHERE {" AND ".join(where)}
        ORDER BY s.value_score DESC NULLS LAST, e.token ASC
        LIMIT ?
    """
    rows = conn.execute(sql, params + [n]).fetchall()
    records = [_search_record(r) for r in rows]

    write_audit(conn, actor_email=actor.email, action="search_people",
                filters={"criteria": applied, "unsupported": unsupported,
                         "division": eff_division},
                result_count=len(records))
    return {"matched": records, "applied": applied, "unsupported_fields": unsupported}


_PULSE_MAX_HORIZON = 90


def _parse_iso(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _days_until_md(today: date, md: str | None) -> int | None:
    """Days from `today` to the next occurrence of an "MM-DD" anniversary (0..365)."""
    if not md:
        return None
    try:
        mm, dd = (int(x) for x in md.split("-"))
        nxt = date(today.year, mm, dd)
    except ValueError:
        return None
    if nxt < today:
        try:
            nxt = date(today.year + 1, mm, dd)
        except ValueError:
            return None
    return (nxt - today).days


def get_team_pulse(
    conn: sqlite3.Connection,
    *,
    actor: Actor,
    division: str | None = None,
    manager_token: str | None = None,
    horizon_days: int = 30,
) -> dict[str, Any]:
    """Operational "what's happening" digest for the viewer's scoped team (token-only).

    Mirrors the rest of the seam's guarantees: a manager only ever sees their own
    division (never wider), and every call writes one audit row. Returns neutral,
    coarse operational signals — birthdays, work anniversaries, who's away + when they
    return, onboarding, upcoming role changes, expiring credentials, availability.

    PRIVACY: leave is reported as availability + return date ONLY. There is no leave
    reason in the schema or this payload — a manager sees who is away and when they're
    back, never why.
    """
    if not (1 <= int(horizon_days) <= _PULSE_MAX_HORIZON):
        raise QueryValidationError(f"horizon_days must be between 1 and {_PULSE_MAX_HORIZON}")
    eff_division = _scope_division(actor, division)
    today = date.today()

    where: list[str] = []
    params: list[Any] = []
    if eff_division is not None:
        where.append("e.division = ?")
        params.append(eff_division)
    if manager_token is not None:
        where.append("e.manager_token = ?")
        params.append(manager_token)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    rows = conn.execute(
        f"""
        SELECT e.token, e.role, e.division, e.team, e.manager_token, e.location,
               e.hire_date,
               o.birthday_md, o.availability, o.pto_return_date, o.on_extended_leave,
               o.leave_return_date, o.onboarding_status, o.role_change_note,
               o.role_change_date, o.credential_name, o.credential_expiry
        FROM employees e
        LEFT JOIN employee_ops o ON o.employee_token = e.token
        {where_sql}
        """,
        params,
    ).fetchall()

    birthdays: list[dict[str, Any]] = []
    anniversaries: list[dict[str, Any]] = []
    on_pto: list[dict[str, Any]] = []
    on_leave: list[dict[str, Any]] = []
    onboarding: list[dict[str, Any]] = []
    role_changes: list[dict[str, Any]] = []
    credentials: list[dict[str, Any]] = []
    availability = {"in_office": 0, "remote": 0, "out": 0}

    for r in rows:
        token = r["token"]
        base = {"token": token, "role": r["role"], "division": r["division"],
                "team": r["team"]}

        bd = _days_until_md(today, r["birthday_md"])
        if bd is not None and bd <= horizon_days:
            birthdays.append({**base, "in_days": bd, "month_day": r["birthday_md"]})

        hire = _parse_iso(r["hire_date"])
        ad = _days_until_md(today, r["hire_date"][5:] if r["hire_date"] else None)
        if ad is not None and ad <= horizon_days and hire is not None:
            occurrence = today + timedelta(days=ad)   # the upcoming anniversary date
            years = occurrence.year - hire.year
            if years >= 1:
                anniversaries.append({**base, "in_days": ad, "years": years})

        pto_ret = _parse_iso(r["pto_return_date"])
        if pto_ret is not None and pto_ret >= today:
            on_pto.append({**base, "return_date": r["pto_return_date"]})

        if r["on_extended_leave"]:
            on_leave.append({**base, "return_date": r["leave_return_date"]})

        if r["onboarding_status"] == "in_progress":
            onboarding.append({**base, "status": "in_progress", "hire_date": r["hire_date"]})

        rc_date = _parse_iso(r["role_change_date"])
        if r["role_change_note"] and rc_date is not None:
            d = (rc_date - today).days
            if 0 <= d <= horizon_days:
                role_changes.append({**base, "note": r["role_change_note"],
                                     "effective_date": r["role_change_date"], "in_days": d})

        cred_exp = _parse_iso(r["credential_expiry"])
        if r["credential_name"] and cred_exp is not None:
            d = (cred_exp - today).days
            if d <= horizon_days:
                credentials.append({**base, "name": r["credential_name"],
                                    "expiry_date": r["credential_expiry"], "in_days": d})

        if r["availability"] in availability:
            availability[r["availability"]] += 1

    birthdays.sort(key=lambda x: x["in_days"])
    anniversaries.sort(key=lambda x: x["in_days"])
    role_changes.sort(key=lambda x: x["in_days"])
    credentials.sort(key=lambda x: x["in_days"])
    on_pto.sort(key=lambda x: x["return_date"] or "")
    on_leave.sort(key=lambda x: x["return_date"] or "")

    result = {
        "as_of": today.isoformat(),
        "horizon_days": horizon_days,
        "scope": {"division": eff_division, "manager_token": manager_token},
        "birthdays": birthdays,
        "anniversaries": anniversaries,
        "on_pto": on_pto,
        "on_leave": on_leave,
        "onboarding": onboarding,
        "role_changes": role_changes,
        "credentials": credentials,
        "availability": availability,
        "team_size": len(rows),
    }

    write_audit(
        conn, actor_email=actor.email, action="get_team_pulse",
        filters={"division": eff_division, "manager_token": manager_token,
                 "horizon_days": horizon_days},
        result_count=len(rows),
    )
    return result


def _search_record(r: sqlite3.Row) -> dict[str, Any]:
    """Shape a token-only search hit. No name/email ever — identity stays at its boundary."""
    import json as _json

    def _arr(text: str | None) -> list:
        try:
            return _json.loads(text) if text else []
        except (ValueError, TypeError):
            return []

    latest = None
    if r["flight_risk"] is not None:
        latest = {"flight_risk": r["flight_risk"], "value_score": r["value_score"]}
    return {
        "token": r["token"], "role": r["role"], "title": r["title"], "level": r["level"],
        "division": r["division"], "team": r["team"], "location": r["location"],
        "employment_type": r["employment_type"], "status": r["status"],
        "skills": _arr(r["skills"]), "certifications": _arr(r["certifications"]),
        "licenses": _arr(r["licenses"]),
        "education_level": r["education_level"], "education_field": r["education_field"],
        "pay_percentile_in_role": r["pay_percentile_in_role"],
        "years_of_experience": r["years_of_experience"],
        "latest_score": latest,
    }
