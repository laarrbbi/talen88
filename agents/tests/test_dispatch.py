"""Dispatcher: one chat surface routes to the right agent, scoped + audited, nothing executed.

These tests exercise the deterministic (LLM-down) path: the fake tools client cannot audit an
LLM call (no `log_llm`), so reasoning.audited_generate / narrate return None and the dispatcher
falls back to its keyword classifier and the agents to their grounded templates. That keeps the
routing assertions stable and proves the chat still works with no model available.
"""
from __future__ import annotations

from agents import dispatch
from agents.autonomy import MAX_LEVEL


class FakeTools:
    """Scope-respecting stand-in for DataTools. No `log_llm` => deterministic (template) path."""

    def __init__(self, rows=None, capital=None, search_hits=None):
        self._rows = rows or []
        self._capital = capital or {}
        self._search_hits = search_hits or []
        self.logged = []

    def list_employees(self, **filters):
        rows = self._rows
        if filters.get("risk_band") == "high":
            rows = [r for r in rows if (r["latest_score"] or {}).get("flight_risk", 0) >= 70]
        if filters.get("division"):
            rows = [r for r in rows if r["division"] == filters["division"]]
        return rows

    def employee_360(self, token):
        return {"panel": [], "capital": self._capital.get(token, []), "skills": []}

    def search_people(self, criteria, limit=None):
        return {"matched": self._search_hits, "applied": criteria, "unsupported_fields": []}

    def dashboard(self):
        return {"headline": {"high_risk_count": 1, "total_scored": 3, "act_now_count": 1,
                             "key_person_count": 1}, "by_division": [], "quadrant": []}

    def me(self):
        return {"role": "manager", "division": "technology", "email": "tm@x.co"}

    def log_run(self, **kw):
        self.logged.append(kw)


def _row(token, *, division="technology", fr=85, reason_codes=None):
    return {"token": token, "division": division, "level": 3, "role": "SWE",
            "location": "London",
            "latest_score": {"flight_risk": fr, "value_score": 60},
            "reason_codes": reason_codes or []}


def _routes(tools):
    return [k["action"] for k in tools.logged if k["action"].startswith("route:")]


# ---- routing ----------------------------------------------------------------
def test_analytic_question_routes_to_conversational():
    tools = FakeTools(rows=[_row("emp_1", fr=90)])
    out = dispatch.dispatch(tools, "who is high-risk and underpaid?")
    assert out["kind"] == "answer" and out["agent"] == "conversational"
    assert "route:answer" in _routes(tools)


def test_people_search_routes_to_search():
    tools = FakeTools(search_hits=[{"token": "emp_2", "role": "Analyst"}])
    out = dispatch.dispatch(tools, "find a CFA in risk & compliance")
    assert out["kind"] == "search" and out["agent"] == "search"
    assert out["matched"] == [{"token": "emp_2", "role": "Analyst"}]


def test_retention_request_routes_to_retention_with_division():
    rows = [_row("emp_1", division="operations", fr=88,
                 reason_codes=[{"label": "comp_below_band", "direction": "increases"}])]
    capital = {"emp_1": [{"metric": "cost_to_lose", "amount": 200000}]}
    tools = FakeTools(rows=rows, capital=capital)
    out = dispatch.dispatch(tools, "give me retention recommendations for operations")
    assert out["kind"] == "recommendation" and out["agent"] == "retention"
    assert out["recommendations"]                       # grounded recos came back
    route = next(k for k in tools.logged if k["action"] == "route:retention")
    assert route["summary"]["division"] == "operations"  # division parsed from text


def test_workforce_request_routes_to_workforce():
    rows = [_row("lead_1", fr=85)]
    tools = FakeTools(rows=rows)
    out = dispatch.dispatch(tools, "where are my succession gaps?")
    assert out["agent"] == "workforce_planning" and out["kind"] == "recommendation"


# ---- single-person agents need a person; otherwise we ask (not guess) -------
def test_career_without_person_asks_a_clarifying_question():
    tools = FakeTools()
    out = dispatch.dispatch(tools, "is this person ready for promotion?")
    assert out["kind"] == "clarify" and out["agent"] == "career"
    assert "which person" in out["answer"].lower()
    assert out["recommendations"] == []


def test_career_with_person_runs_career_agent():
    tools = FakeTools()
    out = dispatch.dispatch(tools, "is this person ready for promotion?",
                            person_token="emp_9")
    assert out["agent"] == "career" and out["kind"] == "recommendation"
    assert "route:career" in _routes(tools)


def test_learning_with_person_routes_to_learning():
    tools = FakeTools()
    out = dispatch.dispatch(tools, "what's a good learning path for them?",
                            person_token="emp_9")
    assert out["agent"] == "learning"


# ---- explicit UI division wins over a text mention --------------------------
def test_ui_division_overrides_text():
    rows = [_row("emp_1", division="technology", fr=88,
                 reason_codes=[{"label": "comp_below_band", "direction": "increases"}])]
    tools = FakeTools(rows=rows, capital={"emp_1": []})
    dispatch.dispatch(tools, "retention recommendations for operations",
                      division="technology")
    route = next(k for k in tools.logged if k["action"] == "route:retention")
    assert route["summary"]["division"] == "technology"


# ---- audit: the routing decision is logged WITHOUT the raw question ---------
def test_route_decision_is_audited_without_question_text():
    tools = FakeTools(rows=[_row("emp_1")])
    q = "is Ada Lovelace ready for promotion?"   # contains a name we must not persist
    dispatch.dispatch(tools, q, person_token="emp_1")
    route = next(k for k in tools.logged if k["action"].startswith("route:"))
    blob = str(route)
    assert "Ada" not in blob and "Lovelace" not in blob   # raw question never in the audit
    assert route["summary"]["q_len"] == len(q)            # only its length is recorded


# ---- autonomy ceiling: recommendations come back inert, capped at L3 --------
def test_recommendations_are_inert_and_capped_at_l3():
    rows = [_row("emp_1", fr=90,
                 reason_codes=[{"label": "comp_below_band", "direction": "increases"}])]
    tools = FakeTools(rows=rows, capital={"emp_1": []})
    out = dispatch.dispatch(tools, "retention recommendations")
    for rec in out["recommendations"]:
        action = rec.get("proposed_action")
        if action:
            assert action["level"] <= int(MAX_LEVEL)   # never above the L3 ceiling
