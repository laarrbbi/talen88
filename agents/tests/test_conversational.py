"""Conversational agent: deterministic planning + scoped, audited, honest answers.

A fake DataTools stands in for the data API so we exercise the agent logic without a
running server. The fake mimics the seam's contract (scoped token-only records) and
records audit writes, so we can assert the agent logs every run and never invents data.
"""
from __future__ import annotations

from agents import conversational
from agents.conversational import plan


# ---- pure planner ------------------------------------------------------------
def test_plan_high_risk():
    assert plan("who on my team is high-risk?").intent == "high_risk"


def test_plan_combined_risk_and_underpaid():
    p = plan("who is high-risk and underpaid")
    assert p.intent == "risk_and_underpaid"
    assert p.filters["risk_band"] == "high" and p.filters["comp_gap"] > 0


def test_plan_promotion_uses_panel_scan():
    p = plan("who's ready for promotion?")
    assert p.intent == "promotion_ready" and p.tool == "panel_scan"


def test_plan_workforce_overview():
    assert plan("where are my biggest workforce risks?").intent == "workforce_risks"


def test_plan_unknown_is_honest():
    p = plan("what's the weather")
    assert p.intent == "unknown" and p.note


# ---- executor with a fake, scope-respecting tools client ---------------------
class FakeTools:
    def __init__(self, rows, dash=None, panels=None):
        self._rows = rows
        self._dash = dash or {"headline": {"total_scored": 2, "high_risk_count": 1,
                                            "key_person_count": 1, "act_now_count": 1},
                              "by_division": [], "quadrant": []}
        self._panels = panels or {}
        self.logged = []

    def list_employees(self, **filters):
        rows = self._rows
        if filters.get("risk_band") == "high":
            rows = [r for r in rows if r["latest_score"]["flight_risk"] >= 70]
        if "comp_gap" in filters:
            rows = [r for r in rows if r["features"]["comp_gap"] >= filters["comp_gap"]]
        return rows

    def employee_360(self, token):
        return {"panel": self._panels.get(token, [])}

    def dashboard(self):
        return self._dash

    def log_run(self, **kw):
        self.logged.append(kw)


def _emp(token, fr, vs, gap):
    return {"token": token, "division": "technology",
            "latest_score": {"flight_risk": fr, "value_score": vs},
            "features": {"comp_gap": gap}}


def test_answer_high_risk_filters_and_audits():
    tools = FakeTools([_emp("emp_1", 85, 60, 0.2), _emp("emp_2", 30, 50, 0.0)])
    res = conversational.answer(tools, "who is high-risk?")
    assert [m["token"] for m in res["matched"]] == ["emp_1"]
    # exactly one audit row written at L1 (insight, read-only)
    assert len(tools.logged) == 1 and tools.logged[0]["level"] == 1
    assert tools.logged[0]["result_count"] == 1


def test_answer_combined_risk_and_underpaid():
    tools = FakeTools([_emp("emp_1", 85, 60, 0.25), _emp("emp_2", 90, 60, 0.0)])
    res = conversational.answer(tools, "high-risk and underpaid people")
    assert [m["token"] for m in res["matched"]] == ["emp_1"]  # emp_2 not underpaid


def test_answer_promotion_reads_panel_not_invented():
    rows = [_emp("emp_1", 30, 80, 0.0), _emp("emp_2", 30, 70, 0.0)]
    panels = {"emp_1": [{"metric": "mobility_readiness", "score": 88}],
              "emp_2": [{"metric": "mobility_readiness", "score": 40}]}
    tools = FakeTools(rows, panels=panels)
    res = conversational.answer(tools, "who is ready for promotion?")
    assert [m["token"] for m in res["matched"]] == ["emp_1"]
    assert res["matched"][0]["mobility_readiness"] == 88


def test_answer_says_so_when_panel_missing():
    rows = [_emp("emp_1", 30, 80, 0.0)]
    tools = FakeTools(rows, panels={"emp_1": []})  # no panel data
    res = conversational.answer(tools, "who is ready for promotion?")
    assert res["matched"] == [] and res["note"]  # honest: no data, says so


def test_answer_workforce_summary_from_dashboard():
    tools = FakeTools([])
    res = conversational.answer(tools, "where are my biggest workforce risks?")
    assert "summary" in res and res["intent"] == "workforce_risks"
    assert tools.logged[0]["action"] == "workforce_risks"


# ---- conversational: greeting / off-topic is answered, not dead-ended ---------------
def test_greeting_is_answered_and_audited_without_inventing_people():
    """A greeting (no workforce intent) still returns a non-blank answer + an audit row,
    and never fabricates matches (there is nothing to match)."""
    tools = FakeTools([_emp("emp_1", 85, 60, 0.2)])
    res = conversational.answer(tools, "hi there")
    assert res["intent"] == "unknown"
    assert res["matched"] == []                       # invents nobody
    assert isinstance(res["answer"], str) and res["answer"].strip()  # never blank
    assert len(tools.logged) == 1 and tools.logged[0]["result_count"] == 0


# ---- conversational: history is threaded to the model for follow-ups ---------------
class HistoryCapturingTools(FakeTools):
    """Audit-capable fake that records the messages an LLM call would receive."""
    def __init__(self, rows, me=None):
        super().__init__(rows)
        self._me = me or {"role": "manager", "division": "technology"}
        self.captured = None

    def me(self):
        return self._me

    def log_llm(self, **kw):
        self.captured = kw


def test_history_and_caller_are_passed_to_the_model(monkeypatch):
    from agents import reasoning
    from llm import LLMResponse
    monkeypatch.setattr(reasoning, "generate",
                        lambda messages: LLMResponse(text="Sure — emp_1 is the one.", model="m"))
    tools = HistoryCapturingTools([_emp("emp_1", 85, 60, 0.2)])
    history = [{"role": "user", "content": "who is high-risk?"},
               {"role": "assistant", "content": "1 person is high flight-risk."}]
    res = conversational.answer(tools, "what about them?", history=history)
    assert res["answer"] == "Sure — emp_1 is the one."
    blob = "\n".join(m["content"] for m in tools.captured["messages"])
    assert "who is high-risk?" in blob          # prior turn replayed
    assert "technology" in blob                 # caller scope described
