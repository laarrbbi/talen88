"""Retention / Career / Learning / Workforce agents: grounded, scoped, audited, capped.

A fake DataTools stands in for the data API so we exercise the agent logic without a
running server. The fake mimics the seam's contract (scoped token-only records + 360
panels/capital) and records audit writes, so we can assert every run logs at L2, that
recommendations are grounded in reason codes / panel data (never invented), and that
nothing proposes an action above the L3 ceiling.
"""
from __future__ import annotations

import pytest

from agents import career, learning, retention, workforce
from agents.autonomy import AutonomyError, Level
from agents.recommend import ProposedAction
from agents.tools import NotFoundError


class FakeTools:
    """Scope-respecting stand-in for DataTools, keyed by token like the real seam."""

    def __init__(self, rows=None, panels=None, capital=None, skills=None, missing=()):
        self._rows = rows or []
        self._panels = panels or {}
        self._capital = capital or {}
        self._skills = skills or {}
        self._missing = set(missing)
        self.logged = []

    def list_employees(self, **filters):
        rows = self._rows
        if filters.get("risk_band") == "high":
            rows = [r for r in rows if r["latest_score"]["flight_risk"] >= 70]
        if filters.get("division"):
            rows = [r for r in rows if r["division"] == filters["division"]]
        return rows

    def employee_360(self, token):
        if token in self._missing:
            raise NotFoundError(f"{token} not found")
        return {"panel": self._panels.get(token, []),
                "capital": self._capital.get(token, []),
                "skills": self._skills.get(token, [])}

    def log_run(self, **kw):
        self.logged.append(kw)


def _row(token, *, level=2, division="technology", fr=80, reason_codes=None):
    return {"token": token, "division": division, "level": level,
            "latest_score": {"flight_risk": fr, "value_score": 60},
            "reason_codes": reason_codes or []}


# ---- retention ---------------------------------------------------------------
def test_retention_picks_intervention_from_top_driver_and_audits():
    rows = [_row("emp_1", fr=88, reason_codes=[
        {"label": "comp_below_band", "direction": "increases"},
        {"label": "tenure_cliff", "direction": "increases"}])]
    capital = {"emp_1": [{"metric": "cost_to_lose", "amount": 250000},
                         {"metric": "suggested_retention_investment", "amount": 50000}]}
    tools = FakeTools(rows, capital=capital)
    out = retention.run(tools)
    rec = out["recommendations"][0]
    assert rec["proposed_action"]["kind"] == "compensation_review"  # from top driver
    assert rec["estimates"]["cost_to_lose_usd"] == 250000
    assert "estimates_caveat" in rec  # modeled-estimate caveat present
    assert tools.logged[0]["level"] == int(Level.RECOMMENDATION)


def test_retention_is_honest_when_no_driver():
    rows = [_row("emp_1", fr=90, reason_codes=[])]
    out = retention.run(FakeTools(rows))
    rec = out["recommendations"][0]
    assert rec["proposed_action"] is None  # nothing invented
    assert "no dominant driver" in rec["rationale"]


def test_retention_empty_scope_note():
    out = retention.run(FakeTools([]))
    assert out["recommendations"] == []
    assert out["insight"]["note"]


# ---- career ------------------------------------------------------------------
def test_career_promotion_when_panel_ready():
    panels = {"emp_1": [{"metric": "mobility_readiness", "score": 85, "reason_codes": []},
                        {"metric": "performance_impact", "score": 75}]}
    skills = {"emp_1": [{"name": "SQL", "family": "Data", "proficiency": 2}]}
    tools = FakeTools(panels=panels, skills=skills)
    out = career.run(tools, token="emp_1")
    titles = [r["title"] for r in out["recommendations"]]
    assert "Promotion nomination" in titles
    assert out["insight"]["promotion_ready"] is True


def test_career_says_so_when_panel_missing():
    tools = FakeTools(panels={"emp_1": []})
    out = career.run(tools, token="emp_1")
    assert out["recommendations"] == []
    assert "score refresh" in out["insight"]["note"]


def test_career_not_found_is_graceful():
    out = career.run(FakeTools(missing={"ghost"}), token="ghost")
    assert out["recommendations"] == [] and out["insight"]["note"]


# ---- learning ----------------------------------------------------------------
def test_learning_paths_from_gaps_and_velocity():
    panels = {"emp_1": [{"metric": "learning_velocity", "score": 72}]}
    skills = {"emp_1": [{"name": "Python", "family": "Engineering", "proficiency": 1},
                        {"name": "Comms", "family": "Communication", "proficiency": 5}]}
    out = learning.run(FakeTools(panels=panels, skills=skills), token="emp_1")
    assert len(out["recommendations"]) == 1  # only the below-bar skill
    assert out["recommendations"][0]["proposed_action"]["kind"] == "enroll_learning_path"
    assert out["insight"]["learning_velocity"] == 72


def test_learning_honest_when_no_gaps():
    skills = {"emp_1": [{"name": "Comms", "family": "Communication", "proficiency": 5}]}
    out = learning.run(FakeTools(skills=skills), token="emp_1")
    assert out["recommendations"] == [] and out["insight"]["note"]


# ---- workforce planning ------------------------------------------------------
def test_workforce_flags_uncovered_succession_gap():
    rows = [_row("lead_1", level=5, fr=85), _row("lead_2", level=4, fr=80),
            _row("ic_1", level=2, fr=90)]
    # no ready successors in scope -> exposed division
    tools = FakeTools(rows, panels={})
    out = workforce.run(tools)
    rec = out["recommendations"][0]
    assert rec["proposed_action"]["kind"] == "open_requisition"
    scen = out["insight"]["succession_scenario"]["technology"]
    assert scen["at_risk_leaders"] == 2 and scen["uncovered"] == 2


def test_workforce_covered_when_ready_successors_present():
    rows = [_row("lead_1", level=5, fr=85)]
    panels = {"lead_1": [{"metric": "mobility_readiness", "score": 90}]}
    out = workforce.run(FakeTools(rows, panels=panels))
    rec = out["recommendations"][0]
    assert rec["proposed_action"]["kind"] == "build_successor_plan"  # depth, not a gap


# ---- autonomy ceiling on proposed actions ------------------------------------
def test_proposed_action_refuses_above_l3():
    with pytest.raises(AutonomyError):
        ProposedAction(kind="auto_fire", employee_token="emp_1", level=4)


# ---- conversational-but-grounded narration (Phase 4) -------------------------
class NarrateTools(FakeTools):
    """FakeTools that can audit an LLM call (so narrate runs) and answers `me()` — used to
    capture exactly what the four action agents send to the model."""
    def __init__(self, *a, me=None, **kw):
        super().__init__(*a, **kw)
        self._me = me or {"role": "manager", "division": "technology", "email": "x@y.co"}
        self.llm_logged = []
        self.sent = None

    def me(self):
        return self._me

    def log_llm(self, **kw):
        self.llm_logged.append(kw)

    def dashboard(self):
        return {"headline": {}, "by_division": [], "quadrant": []}


def test_retention_narrates_conversationally_with_caller_scope(monkeypatch):
    """The retention agent now talks like a partner: the model gets the conversational rules
    block + a descriptive caller line, the call is audited, and the narrative is returned."""
    from agents import reasoning
    from llm import LLMResponse

    def _capture(messages):
        tools.sent = messages
        return LLMResponse(text="emp_1 looks high-risk on comp; here's the thinking.",
                           model="gemma3:1b")
    monkeypatch.setattr(reasoning, "generate", _capture)

    rows = [_row("emp_1", fr=88, reason_codes=[
        {"label": "comp_below_band", "direction": "increases"}])]
    tools = NarrateTools(rows)
    out = retention.run(tools)

    sys_msg = tools.sent[0]["content"]
    assert "Never invent" in sys_msg                      # grounded, no fabrication
    assert "manager" in sys_msg and "technology" in sys_msg  # caller scope is described
    assert "already limited to the people in their permission scope" in sys_msg  # not widening
    assert out["narrative"]                                # natural narrative returned
    assert tools.llm_logged                                # the model call was audited
