"""Career agent (L1-L3).

For one employee (token), reads their 360 panel + skills and surfaces:
  L1 insight  : promotion readiness (mobility) + skill growth areas.
  L2 reco     : promotion nomination when ready; mentor/learning suggestions for gaps.
  L3 action   : a prepared promotion-review nomination for human approval.

Grounds every statement in the panel scores + their reason codes. Never invents a
score: if the panel is missing, it says so. (Internal-role matching against a real
opportunity catalog is a documented future source — see ARCHITECTURE.md.)
"""
from __future__ import annotations

import json

from . import reasoning
from .autonomy import Level
from .recommend import ProposedAction, Recommendation, panel_lookup
from .tools import DataTools, NotFoundError

AGENT = "career"
_READY = 70           # mobility_readiness bar for "promotion ready"
_PERF_OK = 60
_GAP_PROFICIENCY = 2  # proficiency <= 2 is a growth area
_MAX_GAPS = 3

_SYSTEM = (
    "You are Talent88's career-development partner, talking with an HR manager about one "
    "employee (by token). In plain, encouraging language, explain their promotion readiness "
    "and skill growth areas and the reasoning behind your read — grounded ONLY in the panel "
    "scores and skills provided. Describe the proposed development steps; never execute them, "
    "a human approves every action."
)


def run(tools: DataTools, *, token: str) -> dict:
    try:
        full = tools.employee_360(token)
    except NotFoundError:
        return {"agent": AGENT, "insight": {"note": "employee not found or out of scope"},
                "recommendations": []}

    panel = panel_lookup(full)
    mobility = panel.get("mobility_readiness", {})
    perf = panel.get("performance_impact", {})
    if not panel:
        tools.log_run(agent=AGENT, action="run", level=int(Level.INSIGHT),
                      summary={"token": token, "panel": "missing"}, result_count=0)
        return {"agent": AGENT,
                "insight": {"token": token,
                            "note": "no score panel yet — run the score refresh first"},
                "recommendations": []}

    gaps = [s for s in full.get("skills", []) if s["proficiency"] <= _GAP_PROFICIENCY][:_MAX_GAPS]
    recos: list[dict] = []

    ready = mobility.get("score", 0) >= _READY and perf.get("score", 0) >= _PERF_OK
    if ready:
        recos.append(Recommendation(
            employee_token=token, title="Promotion nomination",
            rationale="High mobility readiness and solid performance indicate promotion readiness.",
            drivers=mobility.get("reason_codes", []),
            proposed_action=ProposedAction(kind="promotion_review", employee_token=token,
                                           params={"mobility": mobility.get("score")}),
        ).to_dict())
    for g in gaps:
        recos.append(Recommendation(
            employee_token=token,
            title=f"Develop: {g['name']}",
            rationale=f"{g['name']} is a growth area (proficiency {g['proficiency']}/5); "
                      "pair a mentor with a focused learning plan.",
            drivers=[{"label": f"skill_gap:{g['family']}", "direction": "increases",
                      "weight": 1.0}],
            proposed_action=ProposedAction(kind="assign_mentor", employee_token=token,
                                           params={"skill": g["name"]}),
        ).to_dict())

    insight = {"token": token, "promotion_ready": ready,
               "mobility_readiness": mobility.get("score"),
               "skill_growth_areas": [g["name"] for g in gaps]}
    tools.log_run(agent=AGENT, action="run", level=int(Level.RECOMMENDATION),
                  summary={"token": token, "promotion_ready": ready}, result_count=len(recos))
    narrative = reasoning.narrate(
        tools, agent=AGENT, system=_SYSTEM,
        data_json=json.dumps({"insight": insight, "recommendations": recos}, default=str),
        caller=_caller(tools), conversational=True)
    return {"agent": AGENT, "insight": insight, "recommendations": recos,
            "narrative": narrative}


def _caller(tools: DataTools) -> dict | None:
    """Best-effort 'who am I talking with' (role + scope) so the narrative can address the
    manager naturally. Scope is descriptive only — the data is already clamped by the seam."""
    try:
        me = tools.me()
        return {"role": me.get("role"), "division": me.get("division")}
    except Exception:
        return None
