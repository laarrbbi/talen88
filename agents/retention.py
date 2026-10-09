"""Retention agent (L1-L3).

L1 insight  : who in scope is high flight-risk, and why (their retention reason codes).
L2 reco     : a targeted intervention per person, chosen from the TOP reason code — so
              the rationale is always grounded in what the model actually flagged.
L3 action   : a prepared, human-approvable retention action (no execution).

Reads the model's reason codes + capital estimates via the scoped tools; never invents
scores. If a high-risk person has no reason codes, it says so rather than guessing.
"""
from __future__ import annotations

import json

from . import reasoning
from .autonomy import Level
from .recommend import ProposedAction, Recommendation, capital_lookup
from .tools import DataTools

AGENT = "retention"
_HIGH_RISK = 70
_SCAN_CAP = 60

_SYSTEM = (
    "You are Talent88's retention partner, talking with an HR manager about their team. In "
    "plain, warm language, walk them through who in scope is at high flight risk and why, and "
    "the thinking behind each proposed intervention — grounded ONLY in the reason codes and "
    "modeled estimates provided. Every dollar figure is a modeled estimate, not a precise "
    "individual figure — say so. You may describe the proposed interventions, but you never "
    "execute anything; a human approves every action."
)

# Top retention driver -> (action kind, human-readable intervention).
_INTERVENTION = {
    "comp_below_band": ("compensation_review",
                        "Pay sits below band; a targeted comp adjustment addresses the top driver."),
    "overdue_promotion": ("promotion_review",
                          "Overdue for promotion; open a leveling/promotion review."),
    "manager_instability": ("stabilize_reporting_line",
                            "Repeated manager changes; stabilize the reporting line and assign a mentor."),
    "tenure_cliff": ("growth_conversation",
                     "Approaching the tenure cliff; hold a growth and career conversation."),
}
_DEFAULT = ("retention_conversation",
            "Elevated flight risk; hold a structured retention conversation.")


def run(tools: DataTools, *, division: str | None = None) -> dict:
    """L1 insight + L2 recommendations for high flight-risk people in scope."""
    rows = tools.list_employees(risk_band="high", division=division,
                                sort="flight_risk:desc")
    recos: list[dict] = []
    for r in rows[:_SCAN_CAP]:
        token = r["token"]
        drivers = [rc for rc in r.get("reason_codes", []) if rc["direction"] == "increases"]
        cap = capital_lookup(tools.employee_360(token))
        if not drivers:
            recos.append(Recommendation(
                employee_token=token, title="Retention conversation",
                rationale="High flight risk but no dominant driver is recorded; review manually.",
                drivers=[]).to_dict())
            continue
        top = drivers[0]["label"]
        kind, rationale = _INTERVENTION.get(top, _DEFAULT)
        estimates = {}
        if "cost_to_lose" in cap:
            estimates["cost_to_lose_usd"] = cap["cost_to_lose"]["amount"]
        if "suggested_retention_investment" in cap:
            estimates["suggested_investment_usd"] = cap["suggested_retention_investment"]["amount"]
        recos.append(Recommendation(
            employee_token=token,
            title=kind.replace("_", " ").title(),
            rationale=rationale,
            drivers=drivers,
            estimates=estimates,
            proposed_action=ProposedAction(kind=kind, employee_token=token,
                                           params={"driver": top}),
        ).to_dict())

    insight = {"high_risk_count": len(rows),
               "reviewed": min(len(rows), _SCAN_CAP),
               "note": None if rows else "no high flight-risk people in scope"}
    tools.log_run(agent=AGENT, action="run", level=int(Level.RECOMMENDATION),
                  summary={"division": division, "high_risk_count": len(rows)},
                  result_count=len(recos))
    narrative = reasoning.narrate(
        tools, agent=AGENT, system=_SYSTEM,
        data_json=json.dumps({"scope": division or "all", "insight": insight,
                              "recommendations": recos}, default=str),
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
