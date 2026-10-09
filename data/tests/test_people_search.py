"""People-search seam: multi-field discovery that stays scoped, honest, and walled.

`query.search_people` powers the Company Graph's natural-language search — an LLM parses
free text into structured `criteria`, this reader retrieves the matching people. The
search surface is broad (skills, certs, licenses, location, level, comp position, risk),
but the guarantees are exactly the rest of the seam's:

  * SCOPE — a manager only ever reaches their own division, even by naming another one.
  * THE SCORING WALL — gender / birth_year_band / marital_status / school_prestige /
    named_employer are NOT searchable; an attempt is reported unsupported, never applied.
  * HONESTY — a field the schema doesn't carry (language, visa, availability) is named in
    `unsupported_fields`, never silently dropped or faked.
  * TOKEN-ONLY — no name/email crosses the boundary.
  * INJECTION-SAFE — values are bound parameters; a SQL-ish value matches literally or not
    at all, it never executes.
  * AUDIT — every search writes one audit row carrying the applied + unsupported criteria.

Runs against a freshly generated throwaway DB (never the dev database).
"""
from __future__ import annotations

import json
import os

import pytest
from cryptography.fernet import Fernet

os.environ.setdefault("PULSESCORE_DATA_KEY", Fernet.generate_key().decode())

from data import canonical, generate, query  # noqa: E402
from data.auth import Actor  # noqa: E402
from data.db import get_connection  # noqa: E402


@pytest.fixture(scope="module")
def conn(tmp_path_factory):
    db = tmp_path_factory.mktemp("search") / "gen.db"
    prev = os.environ.get("PULSESCORE_DB")
    os.environ["PULSESCORE_DB"] = str(db)
    try:
        generate.build(seed=7)
        c = get_connection()
        yield c
        c.close()
    finally:
        if prev is None:
            os.environ.pop("PULSESCORE_DB", None)
        else:
            os.environ["PULSESCORE_DB"] = prev


ADMIN = Actor(email="admin@pulsescore.local", name="Admin", role="admin", division=None)
MGR = Actor(email="m@pulsescore.local", name="Mgr", role="manager", division="technology")


# ---- scope -------------------------------------------------------------------
def test_manager_is_fenced_even_when_naming_another_division(conn):
    """A scoped manager asking for 'operations' is clamped to their own division — the
    broad field search inherits the seam's scope, it does not widen access."""
    res = query.search_people(conn, actor=MGR, criteria={"division": "operations"}, limit=500)
    divs = {r["division"] for r in res["matched"]}
    assert divs == {"technology"}, "search must clamp a foreign-division filter to the caller's scope"
    assert res["applied"]["division"] == "technology"


def test_admin_search_can_span_divisions(conn):
    res = query.search_people(conn, actor=ADMIN, criteria={}, limit=500)
    assert len({r["division"] for r in res["matched"]}) > 1


# ---- multi-field intersection ------------------------------------------------
def test_multi_field_criteria_all_apply(conn):
    """A CFA holder at level >= 4: both the license AND the level floor constrain. Score-
    independent so it holds on a freshly generated (not-yet-scored) DB."""
    res = query.search_people(conn, actor=ADMIN,
                              criteria={"license": "CFA", "min_level": 4}, limit=100)
    assert res["matched"], "expected at least one senior CFA holder in the synthetic org"
    for r in res["matched"]:
        assert "cfa" in json.dumps(r["licenses"]).lower()
        assert r["level"] >= 4
    # The intersection is real: it is a strict subset of CFA-holders-of-any-level.
    all_cfa = query.search_people(conn, actor=ADMIN, criteria={"license": "CFA"}, limit=500)
    assert len(res["matched"]) < len(all_cfa["matched"])


def test_risk_band_criterion_constrains_when_scored(conn):
    """When scores are present the risk-band criterion bounds flight_risk; on an unscored
    DB it simply matches nobody (NULL risk is excluded) — never an error, never invented."""
    res = query.search_people(conn, actor=ADMIN, criteria={"risk_band": "high"}, limit=50)
    for r in res["matched"]:
        assert r["latest_score"] is not None and r["latest_score"]["flight_risk"] >= 70


def test_skill_list_form_requires_all(conn):
    res = query.search_people(conn, actor=ADMIN,
                              criteria={"skills": ["Negotiation", "Strategy"]}, limit=50)
    for r in res["matched"]:
        skills = json.dumps(r["skills"]).lower()
        assert "negotiation" in skills and "strategy" in skills


# ---- the scoring wall is NOT searchable --------------------------------------
@pytest.mark.parametrize("walled", sorted(canonical.SCORING_EXCLUDED_COLUMNS))
def test_walled_field_is_reported_unsupported_never_applied(conn, walled):
    res = query.search_people(conn, actor=ADMIN, criteria={walled: "anything"})
    assert walled in res["unsupported_fields"]
    assert walled not in res["applied"]


def test_walled_field_does_not_filter_results(conn):
    """Passing a walled field must behave as if it were absent (it is ignored, not honored):
    the result set is the same as an empty search, proving it never became a WHERE clause."""
    base = query.search_people(conn, actor=ADMIN, criteria={}, limit=500)
    walled = query.search_people(conn, actor=ADMIN, criteria={"gender": "female"}, limit=500)
    assert len(walled["matched"]) == len(base["matched"])


# ---- honesty about missing fields --------------------------------------------
def test_unknown_fields_are_named_not_faked(conn):
    """The exact fields the user's examples need but the schema lacks — language / visa /
    availability — come back as unsupported so the assistant can say so, not invent them."""
    res = query.search_people(conn, actor=ADMIN, criteria={
        "language": "French", "visa": "latin_america", "availability": "relocate",
        "division": "technology"})
    assert set(res["unsupported_fields"]) == {"language", "visa", "availability"}
    assert "division" in res["applied"]  # the real field still applied


# ---- token-only output (no PII leaks) ----------------------------------------
def test_results_are_token_only(conn):
    res = query.search_people(conn, actor=ADMIN, criteria={}, limit=50)
    assert res["matched"]
    for r in res["matched"]:
        assert r["token"].startswith("emp_")
        assert not ({"name", "full_name", "email"} & set(r))


# ---- injection safety --------------------------------------------------------
def test_values_are_bound_parameters_not_executed(conn):
    """A SQL-ish value is treated as a literal to match, never executed. The table is
    intact afterward and the malicious string simply matches nothing."""
    evil = "'; DROP TABLE employees; --"
    res = query.search_people(conn, actor=ADMIN, criteria={"role": evil}, limit=50)
    assert res["matched"] == []  # literal match, found nobody
    # employees table is still there + populated
    assert conn.execute("SELECT COUNT(*) FROM employees").fetchone()[0] > 100


# ---- audit -------------------------------------------------------------------
def test_search_writes_one_audit_row_with_criteria(conn):
    before = conn.execute(
        "SELECT COUNT(*) FROM audit_log WHERE action = 'search_people'").fetchone()[0]
    query.search_people(conn, actor=ADMIN, criteria={"location": "London"}, limit=10)
    rows = conn.execute(
        "SELECT filters_json FROM audit_log WHERE action = 'search_people' "
        "ORDER BY id DESC LIMIT 1").fetchall()
    after = conn.execute(
        "SELECT COUNT(*) FROM audit_log WHERE action = 'search_people'").fetchone()[0]
    assert after == before + 1
    payload = json.loads(rows[0]["filters_json"])
    assert payload["criteria"].get("location") == "London"
