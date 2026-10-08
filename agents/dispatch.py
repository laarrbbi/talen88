"""Conversational dispatcher — one chat surface, every agent reachable in plain language.

The Agents page is a single chat. A turn can be an analytic question ("who is high-risk and
underpaid?"), a people search ("find a CFA in compliance"), or a request for one of the four
recommending agents ("give me retention recommendations for operations", "is this person ready
for promotion?"). `dispatch()` reads the request, decides WHICH agent should answer, runs it,
and returns a single unified shape the chat renders.

It changes nothing about the agents themselves — it only routes to their existing `run()` /
`answer()` entry points, so every guarantee already in place still holds:
  * SCOPE + AUDIT — the same bearer token is forwarded; each agent logs its own run, and the
    routing decision is itself audited here (without the raw question text — see below).
  * TOKEN-ONLY — names never reach this layer or the model; a chosen person arrives only as an
    opaque token from the UI's person-picker (resolved behind the identity boundary).
  * AUTONOMY CEILING — recommendations come back inert (L3 proposed actions); nothing executes.
  * GRACEFUL DEGRADATION — intent classification is LLM-first with a deterministic keyword
    fallback, so the chat keeps working when the model is down.

INTENT MODEL
    answer              -> conversational.answer  (read-only analytic Q&A, L1)
    search              -> search.run             (people discovery over the canonical record, L1)
    retention           -> retention.run(division=...)        (L1 insight + L2 recos, L3 inert)
    workforce_planning  -> workforce.run(division=...)
    career              -> career.run(token=...)              (needs a person; else we ask)
    learning            -> learning.run(token=...)            (needs a person; else we ask)
"""
from __future__ import annotations

import json
import re

from . import career, conversational, learning, reasoning, retention, search, workforce
from .autonomy import Level
from .tools import DataTools

AGENT = "dispatch"

# The agents that need a single person (token) vs. a division scope.
_PERSON_AGENTS = {"career": career, "learning": learning}
_DIVISION_AGENTS = {"retention": retention, "workforce_planning": workforce}
_INTENTS = ("answer", "search", "retention", "workforce_planning", "career", "learning")

_CLASSIFY_SYSTEM = (
    "You route an HR manager's chat message to ONE workforce agent. Reply with a STRICT JSON "
    "object and nothing else: {\"intent\": <one of " + ", ".join(_INTENTS) + ">, "
    "\"division\": <division name or null>}. Choose:\n"
    "  - answer: an analytic question about the workforce (flight risk, comp gaps, key talent, "
    "promotion readiness across the team, overall risk).\n"
    "  - search: finding specific PEOPLE by attributes (skills, licenses, location, role).\n"
    "  - retention: they want retention recommendations / interventions for at-risk people.\n"
    "  - workforce_planning: succession gaps, hiring, redeployment, headcount planning.\n"
    "  - career: promotion readiness or growth plan for ONE specific person.\n"
    "  - learning: skill gaps or a learning path for ONE specific person.\n"
    "Set division only if the message clearly names one (e.g. 'in operations'); else null. "
    "Never include a person's name. Reply with the JSON object only, no prose."
)

# Deterministic keyword cues for when the model is unavailable. Order matters: the more
# specific recommendation intents are checked before the generic analytic 'answer'.
_RETENTION_CUES = ("retention", "retain", "keep them", "intervention", "stop .* leaving")
_WORKFORCE_CUES = ("succession", "workforce plan", "headcount", "backfill", "redeploy",
                   "hiring plan", "hire for", "open req", "coverage gap")
_CAREER_CUES = ("ready for promotion", "promotion ready", "ready to be promoted",
                "career path", "growth plan", "develop them", "next role")
_LEARNING_CUES = ("learning path", "skill gap", "upskill", "training", "course", "reskill")
_SEARCH_CUES = ("find ", "search for", "look for", "who has", "people with", "anyone with",
                "show me .* with")
# Phrasing that points at ONE specific individual (vs. an org-wide question). Lets the
# fallback route a career/learning ask to the single-person agent even before a person is
# picked — the dispatcher then asks WHO (clarify) instead of answering org-wide.
_SINGULAR_CUES = ("this person", "is this", "for them", "for him", "for her", "their ",
                  " they ", " them", " he ", " she ")
_DIVISIONS = ("technology", "operations", "front office", "risk & compliance", "corporate")


def _classify(tools: DataTools, question: str,
              history: list[dict] | None, *, has_person: bool) -> tuple[str, str | None]:
    """Return (intent, division). LLM-first, deterministic keyword fallback. `has_person`
    lets the fallback prefer the single-person agents only when a person is actually selected."""
    messages = [{"role": "system", "content": _CLASSIFY_SYSTEM}]
    for turn in (history or [])[-6:]:
        if turn.get("role") in ("user", "assistant") and (turn.get("content") or "").strip():
            messages.append({"role": turn["role"], "content": turn["content"].strip()})
    messages.append({"role": "user", "content": question.strip()})

    resp = reasoning.audited_generate(tools, agent=AGENT, messages=messages)
    if resp is not None:
        parsed = _extract_json(resp.text)
        if parsed is not None and parsed.get("intent") in _INTENTS:
            div = parsed.get("division")
            div = div if isinstance(div, str) and div.strip() else None
            return parsed["intent"], _canon_division(div)
    return _keyword_classify(question, has_person=has_person)


def _keyword_classify(question: str, *, has_person: bool) -> tuple[str, str | None]:
    q = question.lower()
    div = next((d for d in _DIVISIONS if d in q), None)
    # Single-person intents apply when a person is picked OR the wording points at one
    # individual; the dispatcher asks WHO if no token is attached.
    singular = has_person or _any(f" {q} ", _SINGULAR_CUES)
    if singular and _any(q, _CAREER_CUES):
        return "career", div
    if singular and _any(q, _LEARNING_CUES):
        return "learning", div
    if _any(q, _RETENTION_CUES):
        return "retention", div
    if _any(q, _WORKFORCE_CUES):
        return "workforce_planning", div
    if _any(q, _SEARCH_CUES):
        return "search", div
    return "answer", div


def dispatch(tools: DataTools, question: str, *, history: list[dict] | None = None,
             person_token: str | None = None, division: str | None = None) -> dict:
    """Route one chat turn to the right agent and return a unified result.

    The returned shape is the same for every route so the chat renders it uniformly:
        {kind, agent, answer, matched, recommendations, note}
    `kind` is one of: answer | search | recommendation | clarify. A 'clarify' turn asks the
    manager for a missing input (e.g. which person) instead of guessing.
    """
    intent, parsed_division = _classify(tools, question, history, has_person=bool(person_token))
    # An explicit division picked in the UI wins over one merely mentioned in the text.
    division = division or parsed_division

    # Audit the routing decision itself (no raw question text — it can name a person).
    tools.log_run(agent=AGENT, action=f"route:{intent}", level=int(Level.INSIGHT),
                  summary={"intent": intent, "division": division,
                           "has_person": bool(person_token), "q_len": len(question)},
                  result_count=0)

    if intent in _PERSON_AGENTS:
        if not person_token:
            return _clarify(intent)
        run = _PERSON_AGENTS[intent].run(tools, token=person_token)
        return _from_recommender(intent, run)

    if intent in _DIVISION_AGENTS:
        run = _DIVISION_AGENTS[intent].run(tools, division=division)
        return _from_recommender(intent, run)

    if intent == "search":
        res = search.run(tools, question, history=history)
        return {"kind": "search", "agent": "search", "answer": res.get("answer", ""),
                "matched": res.get("matched", []), "recommendations": [],
                "note": res.get("note")}

    # Default: read-only conversational analytic answer.
    res = conversational.answer(tools, question, history=history)
    return {"kind": "answer", "agent": "conversational", "answer": res.get("answer", ""),
            "matched": res.get("matched", []), "recommendations": [],
            "note": res.get("note")}


def _from_recommender(agent: str, run: dict) -> dict:
    """Normalize a recommending agent's {agent, insight, recommendations, narrative} into the
    unified chat shape. Falls back to a grounded template when the narrative (LLM) is absent."""
    recos = run.get("recommendations", [])
    insight = run.get("insight", {}) or {}
    answer = run.get("narrative") or _reco_template(agent, recos, insight)
    note = insight.get("note")
    return {"kind": "recommendation", "agent": agent, "answer": answer,
            "matched": [], "recommendations": recos, "note": note}


def _reco_template(agent: str, recos: list[dict], insight: dict) -> str:
    """Deterministic one-liner for when the AI summary is unavailable."""
    n = len(recos)
    label = agent.replace("_", " ")
    if n == 0:
        return insight.get("note") or f"No {label} recommendations for the people in scope right now."
    return (f"{n} grounded {label} recommendation{'s' if n != 1 else ''} below — each is a "
            "proposal a human approves before anything is recorded.")


def _clarify(intent: str) -> dict:
    """A single-person agent was asked for without a person — ask, don't guess."""
    what = "promotion readiness and growth" if intent == "career" else "skill gaps and a learning path"
    return {"kind": "clarify", "agent": intent, "matched": [], "recommendations": [],
            "note": None,
            "answer": (f"Happy to look at {what} — which person should I focus on? "
                       "Pick someone with the person selector above.")}


# ---- helpers ----------------------------------------------------------------
def _any(text: str, cues: tuple[str, ...]) -> bool:
    return any(re.search(c, text) if " .* " in c or c.endswith("*") else c in text for c in cues)


def _canon_division(div: str | None) -> str | None:
    if not div:
        return None
    low = div.strip().lower()
    return next((d for d in _DIVISIONS if d == low), div.strip())


def _extract_json(text: str) -> dict | None:
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
        return obj if isinstance(obj, dict) else None
    except ValueError:
        return None
