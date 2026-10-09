"""People-search agent: LLM parses a request into criteria, the scoped tool retrieves.

The agent is the Graph's conversational discovery. These tests mock the LLM provider (no
running model needed) and use an audit-capable fake search tool, then assert:

  * the model's JSON is parsed into criteria and passed to the scoped search tool;
  * unsupported asks (the model's + a keyword scan + the seam's) are merged and surfaced,
    never silently dropped or faked;
  * when the model is unavailable, a deterministic keyword parse keeps search working and
    the answer falls back to a grounded template (graceful, never a crash);
  * every run is audited and the output is token-only.
"""
from __future__ import annotations

import json

from agents import reasoning, search
from llm import LLMResponse, LLMUnavailable


class SearchTools:
    """Audit-capable fake: records the criteria the agent searched with + audit rows."""
    def __init__(self, hits=None, unsupported=None, me=None):
        self._hits = hits or []
        self._unsupported = unsupported or []
        self._me = me or {"role": "manager", "division": "technology"}
        self.searched_with = None
        self.logged = []
        self.llm_logged = []

    def search_people(self, criteria, limit=None):
        self.searched_with = criteria
        return {"matched": self._hits, "applied": criteria,
                "unsupported_fields": self._unsupported}

    def me(self):
        return self._me

    def log_run(self, **kw):
        self.logged.append(kw)

    def log_llm(self, **kw):
        self.llm_logged.append(kw)


def _hit(token, **extra):
    base = {"token": token, "role": "engineer", "division": "technology", "level": 5,
            "skills": [], "licenses": [], "latest_score": {"flight_risk": 20, "value_score": 80}}
    base.update(extra)
    return base


# ---- LLM-driven parse --------------------------------------------------------
def test_llm_json_is_parsed_into_scoped_search(monkeypatch):
    """The model returns JSON criteria; the agent strips it to known fields and searches."""
    reply = json.dumps({"criteria": {"license": "CFA", "division": "risk & compliance",
                                      "bogus_field": "x"},
                        "unsupported": []})
    monkeypatch.setattr(reasoning, "generate",
                        lambda messages: LLMResponse(text=reply, model="m"))
    tools = SearchTools(hits=[_hit("emp_1", licenses=[{"name": "CFA"}])])
    out = search.run(tools, "compliance folks with a CFA")
    # Only known fields are forwarded; the made-up key is dropped (the seam is source of truth).
    assert tools.searched_with == {"license": "CFA", "division": "risk & compliance"}
    assert [m["token"] for m in out["matched"]] == ["emp_1"]
    assert out["agent"] == "search"


def test_unsupported_fields_from_model_keyword_and_seam_are_merged(monkeypatch):
    """A request for languages/visa/availability is surfaced as unsupported — from the
    model's own list, a keyword scan, AND whatever the seam couldn't honor — never faked."""
    reply = json.dumps({"criteria": {"division": "technology"}, "unsupported": ["language"]})
    monkeypatch.setattr(reasoning, "generate",
                        lambda messages: LLMResponse(text=reply, model="m"))
    tools = SearchTools(hits=[], unsupported=["availability"])
    out = search.run(
        tools, "French speakers in tech with a visa to work in Brazil, and are they available?")
    assert set(out["unsupported_fields"]) >= {"language", "visa", "availability"}
    assert "not stored yet" in (out["note"] or "").lower()


def test_known_missing_fields_flagged_even_if_model_forgets(monkeypatch):
    """Keyword scan catches visa/availability even when the model omits them from JSON."""
    reply = json.dumps({"criteria": {"location": "London"}, "unsupported": []})
    monkeypatch.setattr(reasoning, "generate",
                        lambda messages: LLMResponse(text=reply, model="m"))
    tools = SearchTools(hits=[])
    out = search.run(tools, "people in London available to relocate with a work visa")
    assert {"visa", "availability"} <= set(out["unsupported_fields"])


# ---- graceful fallback when the model is down --------------------------------
def test_keyword_fallback_when_llm_unavailable(monkeypatch):
    """Model down → deterministic keyword parse still pulls license/location/risk, and the
    answer falls back to a grounded template. No crash, nothing invented."""
    def _down(messages):
        raise LLMUnavailable("offline")
    monkeypatch.setattr(reasoning, "generate", _down)
    tools = SearchTools(hits=[_hit("emp_2")])
    out = search.run(tools, "a CFA holder in London who is low risk")
    assert tools.searched_with.get("license") == "CFA"
    assert tools.searched_with.get("location") == "London"
    assert tools.searched_with.get("risk_band") == "low"
    assert out["answer"]                 # template, non-blank
    assert tools.llm_logged == []        # nothing audited (model never ran)


# ---- audit + token-only ------------------------------------------------------
def test_run_is_audited_at_insight_level(monkeypatch):
    monkeypatch.setattr(reasoning, "generate",
                        lambda messages: LLMResponse(text='{"criteria":{},"unsupported":[]}', model="m"))
    tools = SearchTools(hits=[_hit("emp_3")])
    search.run(tools, "anyone")
    assert tools.logged and tools.logged[0]["agent"] == "search"
    assert tools.logged[0]["level"] == 1


def test_output_is_token_only(monkeypatch):
    monkeypatch.setattr(reasoning, "generate",
                        lambda messages: LLMResponse(text='{"criteria":{},"unsupported":[]}', model="m"))
    tools = SearchTools(hits=[_hit("emp_4")])
    out = search.run(tools, "anyone")
    for m in out["matched"]:
        assert "token" in m and not ({"name", "full_name", "email"} & set(m))


def test_parse_extracts_json_from_noisy_reply(monkeypatch):
    """The model wraps JSON in prose/fences; the extractor still recovers the object."""
    reply = "Sure! Here is the search:\n```json\n{\"criteria\": {\"location\": \"Tokyo\"}, \"unsupported\": []}\n```"
    monkeypatch.setattr(reasoning, "generate",
                        lambda messages: LLMResponse(text=reply, model="m"))
    tools = SearchTools(hits=[])
    search.run(tools, "folks in tokyo")
    assert tools.searched_with == {"location": "Tokyo"}
