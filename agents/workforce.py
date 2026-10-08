"""Workforce-Planning agent (L1-L3).

Across a scope (a division, or everything for an admin), surfaces succession gaps and
projected shortages, models a simple scenario, and recommends hiring/redeployment.
  L1 insight  : succession risk (high flight-risk leaders) + ready-successor coverage.
  L2 reco     : hire/redeploy per exposed division.
  L3 action   : a prepared hiring/redeployment request for human approval.

Reads scores via the scoped tools (capped fetch); never invents data. Coverage it
cannot compute (e.g. beyond the scan cap) is reported as such, not guessed.
"""
from __future__ import annotations

import json

from . import reasoning
from .autonomy import Level
from .recommend import ProposedAction, Recommendation, panel_lookup
from .tools import DataTools

AGENT = "workforce_planning"
_HIGH_RISK = 70
_LEADER_LEVEL = 4
_READY = 70           # mobility_readiness bar for a "ready successor"
_SCAN_CAP = 80

_SYSTEM = (
    "You are Talent88's workforce-planning partner, talking with an HR manager. In plain "
    "language, walk them through succession exposure (high flight-risk leaders vs. ready "
    "successors) per division and the hire/redeploy moves you'd propose, with the reasoning — "
    "grounded ONLY in the scoped data provided. Describe the actions; never execute them, a "
    "human approves every action."
)


def run(tools: DataTools, *, division: str | None = None) -> dict:
    rows = tools.list_employees(division=division, sort="flight_risk:desc")
    scored = [r for r in rows if r.get("latest_score")]
    truncated = len(scored) > _SCAN_CAP

    # Ready-successor coverage per division needs the panel; fetch (capped).
    ready_by_div: dict[str, int] = {}
    for r in scored[:_SCAN_CAP]:
        panel = panel_lookup(tools.employee_360(r["token"]))
        if panel.get("mobility_readiness", {}).get("score", 0) >= _READY:
            ready_by_div[r["division"]] = ready_by_div.get(r["division"], 0) + 1

    # Succession risk: high flight-risk leaders, grouped by division.
    at_risk_leaders: dict[str, list[str]] = {}
    for r in scored:
        if r["level"] >= _LEADER_LEVEL and r["latest_score"]["flight_risk"] >= _HIGH_RISK:
            at_risk_leaders.setdefault(r["division"], []).append(r["token"])

    # Projected attrition (high-risk share) per division.
    by_div: dict[str, dict] = {}
    for r in scored:
        d = by_div.setdefault(r["division"], {"total": 0, "high_risk": 0})
        d["total"] += 1
        if r["latest_score"]["flight_risk"] >= _HIGH_RISK:
            d["high_risk"] += 1

    recos: list[dict] = []
    for div, leaders in at_risk_leaders.items():
        ready = ready_by_div.get(div, 0)
        exposed = len(leaders) > ready
        title = "Open succession hire" if exposed else "Develop internal successors"
        rationale = (f"{len(leaders)} high-risk leader(s) in {div} with {ready} ready "
                     f"successor(s) in scope — {'a gap' if exposed else 'covered, keep depth'}.")
        recos.append(Recommendation(
            employee_token=leaders[0],   # the most-at-risk leader anchors the action
            title=title,
            rationale=rationale,
            drivers=[{"label": "succession_gap", "direction": "increases",
                      "weight": 1.0}] if exposed else [],
            proposed_action=ProposedAction(
                kind="open_requisition" if exposed else "build_successor_plan",
                employee_token=leaders[0],
                params={"division": div, "at_risk_leaders": len(leaders),
                        "ready_successors": ready}),
        ).to_dict())

    scenario = {div: {"at_risk_leaders": len(t),
                      "ready_successors": ready_by_div.get(div, 0),
                      "uncovered": max(0, len(t) - ready_by_div.get(div, 0))}
                for div, t in at_risk_leaders.items()}
    insight = {"scope": division or "all", "by_division": by_div,
               "succession_scenario": scenario,
               "note": (f"coverage scanned for the top {_SCAN_CAP} only" if truncated
                        else None)}
    tools.log_run(agent=AGENT, action="run", level=int(Level.RECOMMENDATION),
                  summary={"division": division, "exposed_divisions": len(at_risk_leaders)},
                  result_count=len(recos))
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
