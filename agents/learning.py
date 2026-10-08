"""Learning agent (L1-L3).

For one employee (token), spots skill gaps and recommends learning paths, weighted by
their Learning Velocity (capacity to absorb new learning).
  L1 insight  : skill gaps + current learning velocity.
  L2 reco     : a concrete learning path per gap.
  L3 action   : a prepared learning-path enrollment for human approval.

Reads the 360 skills + the learning_velocity panel score; never invents data.
(A real LMS course catalog is a documented future source — see ARCHITECTURE.md.)
"""
from __future__ import annotations

import json

from . import reasoning
from .autonomy import Level
from .recommend import ProposedAction, Recommendation, panel_lookup
from .tools import DataTools, NotFoundError

AGENT = "learning"
_GAP_PROFICIENCY = 2
_MAX_PATHS = 4

_SYSTEM = (
    "You are Talent88's learning partner, talking with an HR manager about one employee (by "
    "token). In plain language, explain their skill gaps and the learning paths you'd suggest, "
    "weighted by learning velocity, and why each fits — grounded ONLY in the skills and panel "
    "data provided. Describe the paths; never enroll anyone, a human approves every action."
)

# A tiny synthetic path catalog keyed by skill family (stand-in for an LMS).
_PATHS = {
    "Engineering": "Hands-on systems & cloud track",
    "Data": "Applied data & ML foundations",
    "Risk": "Risk & regulatory fundamentals",
    "Finance": "Markets & financial modeling track",
    "Operations": "Process automation & controls",
    "Leadership": "People-leadership essentials",
    "Communication": "Influence & executive communication",
    "Product": "Product sense & delivery",
}


def run(tools: DataTools, *, token: str) -> dict:
    try:
        full = tools.employee_360(token)
    except NotFoundError:
        return {"agent": AGENT, "insight": {"note": "employee not found or out of scope"},
                "recommendations": []}

    panel = panel_lookup(full)
    velocity = panel.get("learning_velocity", {}).get("score")
    gaps = [s for s in full.get("skills", []) if s["proficiency"] <= _GAP_PROFICIENCY][:_MAX_PATHS]
    if not gaps:
        note = ("no recorded skills to assess" if not full.get("skills")
                else "no skill gaps below the proficiency bar")
        tools.log_run(agent=AGENT, action="run", level=int(Level.INSIGHT),
                      summary={"token": token}, result_count=0)
        return {"agent": AGENT,
                "insight": {"token": token, "learning_velocity": velocity, "note": note},
                "recommendations": []}

    recos = []
    for g in gaps:
        path = _PATHS.get(g["family"], f"{g['family']} fundamentals")
        recos.append(Recommendation(
            employee_token=token,
            title=f"Learning path: {path}",
            rationale=f"{g['name']} (proficiency {g['proficiency']}/5) is below target; "
                      f"'{path}' closes the gap.",
            drivers=[{"label": f"skill_gap:{g['family']}", "direction": "increases",
                      "weight": 1.0}],
            proposed_action=ProposedAction(kind="enroll_learning_path", employee_token=token,
                                           params={"path": path, "skill": g["name"]}),
        ).to_dict())

    insight = {"token": token, "learning_velocity": velocity,
               "skill_gaps": [g["name"] for g in gaps]}
    tools.log_run(agent=AGENT, action="run", level=int(Level.RECOMMENDATION),
                  summary={"token": token, "gaps": len(gaps)}, result_count=len(recos))
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
