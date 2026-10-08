"""Conversational agent (read-only, Level 1).

Answers natural-language workforce questions by translating them into calls on the
query seam — the same tool a human or the UI would use. It is deliberately the first
and safest agent: it never recommends or acts, only surfaces what the data already
says, scoped to the caller.

INTELLIGENCE SEAM (rule-based now, LLM later):
    `plan()` is a transparent, deterministic intent+slot parser — no third-party
    cloud, data stays local, behavior is auditable and testable. A future LLM-backed
    planner drops in behind this same function: it would emit the SAME structured
    `Plan` (intent + tool + filters), so the executor, scoping, and audit are
    unchanged. The agent reads the model's reason codes; it never invents scores.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from . import reasoning
from .autonomy import Level
from .tools import DataTools

AGENT = "conversational"

_SYSTEM = (
    "You are Talent88's read-only workforce analyst — a helpful colleague an HR manager can "
    "talk to in plain language. Greet them, follow the thread of the conversation (including "
    "earlier turns), and explain what the numbers mean. You answer ONLY from the structured, "
    "token-keyed results retrieved for them this turn; you never recommend or take action "
    "(this is a read-only, insight-level agent)."
)

# Thresholds the planner uses when a question implies a band but gives no number.
_COMP_GAP_MIN = 0.15        # "underpaid" => at least a 15% gap below band
_VALUE_HIGH = 70            # "key person" / "valuable"
_MOBILITY_READY = 70        # "ready for promotion"
_PANEL_SCAN_CAP = 60        # max per-employee 360 fetches for panel-based intents


@dataclass
class Plan:
    intent: str
    tool: str
    filters: dict = field(default_factory=dict)
    note: str | None = None     # set when we cannot fully answer (missing data etc.)


def plan(question: str) -> Plan:
    """Deterministically map a question to a query-seam plan. Pure + testable."""
    q = question.lower()
    risk = any(w in q for w in ("high-risk", "high risk", "flight risk", "at risk",
                                "attrition", "leaving", "quit", "churn", "burnout"))
    underpaid = any(w in q for w in ("underpaid", "comp gap", "below band",
                                     "below market", "pay gap"))
    promotion = any(w in q for w in ("promotion", "promote", "ready to advance"))
    key_person = any(w in q for w in ("key person", "key talent", "high value",
                                      "valuable", "most valuable", "flight risk talent"))
    workforce = any(w in q for w in ("biggest risk", "workforce risk", "where are my",
                                     "team risk", "overall risk", "summary"))

    if risk and underpaid:
        return Plan("risk_and_underpaid", "list_employees",
                    {"risk_band": "high", "comp_gap": _COMP_GAP_MIN, "sort": "flight_risk:desc"})
    if promotion:
        return Plan("promotion_ready", "panel_scan",
                    {"metric": "mobility_readiness", "threshold": _MOBILITY_READY})
    if underpaid:
        return Plan("underpaid", "list_employees",
                    {"comp_gap": _COMP_GAP_MIN, "sort": "comp_gap:desc"})
    if risk:
        return Plan("high_risk", "list_employees",
                    {"risk_band": "high", "sort": "flight_risk:desc"})
    if key_person:
        return Plan("key_person", "list_employees", {"sort": "value_score:desc"})
    if workforce:
        return Plan("workforce_risks", "dashboard")
    return Plan("unknown", "none",
                note="I can answer questions about flight risk, comp gaps, key talent, "
                     "promotion readiness, and overall workforce risk.")


def answer(tools: DataTools, question: str,
           history: list[dict] | None = None) -> dict:
    """Run the plan against the (scoped) data tools and return a structured answer.

    Always logs what it saw to the audit trail. Never invents data: when a needed
    signal is missing it says so in `note` rather than guessing. `history` (prior
    user/assistant turns) lets the model follow the thread for follow-ups; it is replayed
    to the model only — it never widens the permission scope of the data fetched here.
    """
    p = plan(question)
    matched: list[dict] = []
    note = p.note

    # A greeting / chit-chat / off-topic message with no workforce intent. Don't dead-end:
    # converse naturally (or fall back to an honest capability note) — still audited, still
    # grounded (there is simply no data to invent because nothing was matched).
    if p.tool == "none":
        tools.log_run(agent=AGENT, action=p.intent, level=int(Level.INSIGHT),
                      summary={"question": question}, result_count=0)
        answer = _narrate(tools, question, p.intent,
                          {"question": question, "intent": p.intent, "matched": [],
                           "note": note}, history=history) or (note or _phrase(p.intent, None, []))
        return {"intent": p.intent, "question": question, "matched": [],
                "note": note, "answer": answer}

    if p.tool == "list_employees":
        rows = tools.list_employees(**{k: v for k, v in p.filters.items()})
        if p.intent == "key_person":
            rows = [r for r in rows if (r.get("latest_score") or {}).get("value_score", 0)
                    >= _VALUE_HIGH]
        matched = rows
    elif p.tool == "panel_scan":
        matched, note = _panel_scan(tools, p)
    elif p.tool == "dashboard":
        dash = tools.dashboard()
        result = {"intent": p.intent, "question": question, "summary": dash["headline"],
                  "by_division": dash["by_division"], "matched": [], "note": note}
        tools.log_run(agent=AGENT, action=p.intent, level=int(Level.INSIGHT),
                      summary={"question": question}, result_count=dash["headline"]["high_risk_count"])
        template = _phrase(p.intent, dash["headline"], [])
        result["answer"] = _narrate(tools, question, p.intent,
                                    {"question": question, "summary": dash["headline"],
                                     "by_division": dash["by_division"]},
                                    history=history) or template
        return result

    tools.log_run(agent=AGENT, action=p.intent, level=int(Level.INSIGHT),
                  summary={"question": question, "filters": p.filters},
                  result_count=len(matched))
    template = _phrase(p.intent, None, matched)
    answer = _narrate(tools, question, p.intent,
                      {"question": question, "intent": p.intent, "matched": matched,
                       "note": note}, history=history) or template
    return {"intent": p.intent, "question": question, "matched": matched,
            "note": note, "answer": answer}


def _caller(tools: DataTools) -> dict | None:
    """Who is asking (role + division), so the model can address them naturally. Best-effort:
    the data has already been scoped by the seam, so a failure here never affects safety."""
    try:
        me = tools.me()
        return {"role": me.get("role"), "division": me.get("division")}
    except Exception:
        return None


def _narrate(tools: DataTools, question: str, intent: str, data: dict,
             history: list[dict] | None = None) -> str | None:
    """Phrase the (already-scoped, token-only) answer via the LLM, or None to fall back
    to the deterministic template. The model sees only tokens + this question + prior turns."""
    payload = {"question": question, **{k: v for k, v in data.items() if k != "question"}}
    return reasoning.narrate(tools, agent=AGENT, system=_SYSTEM,
                             data_json=json.dumps(payload, default=str),
                             history=history, caller=_caller(tools), conversational=True)


def _panel_scan(tools: DataTools, p: Plan) -> tuple[list[dict], str | None]:
    """Promotion-readiness etc. need the panel, which the list view doesn't carry.
    Fetch 360 for the (already-scoped) candidate set, capped, reading the metric."""
    metric, threshold = p.filters["metric"], p.filters["threshold"]
    candidates = tools.list_employees(sort="value_score:desc")
    truncated = len(candidates) > _PANEL_SCAN_CAP
    out: list[dict] = []
    for c in candidates[:_PANEL_SCAN_CAP]:
        full = tools.employee_360(c["token"])
        panel = {m["metric"]: m["score"] for m in full.get("panel", [])}
        score = panel.get(metric)
        if score is None:
            continue
        if score >= threshold:
            out.append({**c, metric: score})
    out.sort(key=lambda r: r[metric], reverse=True)
    note = (f"scanned the top {_PANEL_SCAN_CAP} by value; widen filters to scan more"
            if truncated else None)
    if not out and note is None:
        note = f"no one in scope currently meets the {metric.replace('_', ' ')} bar"
    return out, note


def _phrase(intent: str, headline: dict | None, matched: list[dict]) -> str:
    """A short natural-language summary. Plain templating — no model needed."""
    n = len(matched)
    if intent == "workforce_risks" and headline:
        return (f"{headline['high_risk_count']} of {headline['total_scored']} people are "
                f"high flight-risk; {headline['act_now_count']} are high-risk AND high-value "
                "(act now).")
    phrases = {
        "risk_and_underpaid": f"{n} people are high flight-risk and underpaid.",
        "underpaid": f"{n} people are underpaid relative to band.",
        "high_risk": f"{n} people are high flight-risk.",
        "key_person": f"{n} people are high-value key talent.",
        "promotion_ready": f"{n} people look ready for promotion (high mobility readiness).",
        "unknown": "I couldn't map that to a workforce query.",
    }
    return phrases.get(intent, f"{n} matching people.")
