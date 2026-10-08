"""Agent layer — FastAPI service (:8002).

The agents call the data layer's query API + scoring service AS TOOLS (see tools.py);
they have no DB access and no privileged path. The caller's bearer token is forwarded
to the data layer, so every agent inherits permission scope + audit. Autonomy is
capped at L3 (human approval) everywhere — there is no L4 / auto-execution.

All five agents mount on this one service. The read-only Conversational agent answers
at L1; Retention / Career / Learning / Workforce-Planning surface L1 insight + L2
recommendations, each carrying an inert L3 proposed action a human can approve via
`/agents/{name}/approve` — which only records the approval in the audit trail. No
endpoint executes a real HR/comp side effect: there is no L4 anywhere.
"""
from __future__ import annotations

import logging

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import career, conversational, dispatch, learning, retention, search, workforce
from .autonomy import MAX_LEVEL, AutonomyError, Level, enforce
from .tools import DataTools, ToolError

logger = logging.getLogger("pulsescore.agents")

app = FastAPI(title="PulseScore Agent Layer", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(AutonomyError)
async def _autonomy_handler(_req: Request, exc: AutonomyError):
    return JSONResponse(status_code=403, content={"detail": str(exc)})


@app.exception_handler(ToolError)
async def _tool_handler(_req: Request, _exc: ToolError):
    logger.error("data tool call failed")  # no token/PII in the log line
    return JSONResponse(status_code=502, content={"detail": "upstream data error"})


@app.exception_handler(Exception)
async def _unhandled(_req: Request, exc: Exception):
    logger.error("unhandled error: %s", type(exc).__name__)
    return JSONResponse(status_code=500, content={"detail": "internal error"})


def _bearer(authorization: str) -> str:
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    return authorization.split(" ", 1)[1].strip()


class ChatTurn(BaseModel):
    """One prior message in the conversation, replayed to the model so follow-ups work."""
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(min_length=1, max_length=2000)


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    history: list[ChatTurn] = Field(default_factory=list, max_length=20)


class SearchRequest(BaseModel):
    """A natural-language people-search for the Company Graph; carries the conversation so
    follow-ups ('and which of those are available?') work."""
    question: str = Field(min_length=1, max_length=500)
    history: list[ChatTurn] = Field(default_factory=list, max_length=20)


class ChatRequest(BaseModel):
    """One turn of the unified Agents chat. The dispatcher decides which agent answers.

    `person_token` (optional) carries a person the manager picked in the UI — only ever an
    opaque token; names never reach this layer. `division` (optional) is a UI-picked scope for
    the division agents. Both are bounded; scope is still enforced server-side by the seam."""
    question: str = Field(min_length=1, max_length=500)
    history: list[ChatTurn] = Field(default_factory=list, max_length=20)
    person_token: str | None = Field(default=None, max_length=80)
    division: str | None = Field(default=None, max_length=80)


class RunRequest(BaseModel):
    """Optional scope for division-scoped agents; ignored by single-token agents."""
    division: str | None = Field(default=None, max_length=80)
    token: str | None = Field(default=None, max_length=80)


class ApproveRequest(BaseModel):
    """A human approving one L3 proposed action. Records intent; runs no side effect."""
    kind: str = Field(min_length=1, max_length=80)
    employee_token: str = Field(min_length=1, max_length=80)
    level: int = Field(default=int(Level.APPROVE), ge=1, le=int(MAX_LEVEL))
    params: dict = Field(default_factory=dict)


# Division-scoped agents take {division}; single-token agents take {token}.
_SCOPED_AGENTS = {"retention": retention, "workforce_planning": workforce}
_TOKEN_AGENTS = {"career": career, "learning": learning}

# Map an approved action to a notification kind (opt-in, created on the data layer).
_NOTIFY_KIND = {
    "compensation_review": "retention_approved",
    "promotion_review": "promotion_ready",
    "retention_conversation": "retention_approved",
    "growth_conversation": "retention_approved",
    "stabilize_reporting_line": "retention_approved",
    "open_requisition": "succession_gap",
    "build_successor_plan": "succession_gap",
    "enroll_learning_path": "learning_recommended",
    "assign_mentor": "learning_recommended",
}


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "max_autonomy_level": int(MAX_LEVEL)}


@app.get("/agents")
def list_agents() -> dict:
    """Catalog of available agents + the autonomy ceiling (no L4 anywhere)."""
    return {
        "max_autonomy_level": int(MAX_LEVEL),
        "agents": [
            {"name": "conversational", "max_level": 1, "status": "active", "scope": "ask",
             "description": "Read-only natural-language Q&A over the workforce data."},
            {"name": "retention", "max_level": 3, "status": "active", "scope": "division",
             "description": "High flight-risk people + a grounded intervention per person."},
            {"name": "career", "max_level": 3, "status": "active", "scope": "token",
             "description": "Promotion readiness + skill growth for one employee."},
            {"name": "learning", "max_level": 3, "status": "active", "scope": "token",
             "description": "Skill gaps + learning paths weighted by learning velocity."},
            {"name": "workforce_planning", "max_level": 3, "status": "active",
             "scope": "division",
             "description": "Succession gaps + hire/redeploy recommendations across a scope."},
        ],
    }


@app.post("/agents/conversational/ask")
def conversational_ask(req: AskRequest, authorization: str = Header(default="")) -> dict:
    """Ask the read-only conversational agent. Scoped to the caller; audited."""
    tools = DataTools(_bearer(authorization))
    history = [{"role": t.role, "content": t.content} for t in req.history]
    return conversational.answer(tools, req.question, history=history)


@app.post("/agents/search/people")
def search_people(req: SearchRequest, authorization: str = Header(default="")) -> dict:
    """Natural-language people search for the Company Graph. The LLM parses the request into
    structured criteria over the canonical record; the scoped query tool retrieves matches
    (a manager can never reach past their division). Token-only; honest about missing fields."""
    tools = DataTools(_bearer(authorization))
    history = [{"role": t.role, "content": t.content} for t in req.history]
    return search.run(tools, req.question, history=history)


@app.post("/agents/chat")
def agents_chat(req: ChatRequest, authorization: str = Header(default="")) -> dict:
    """The unified conversational surface. One message in, one agent's grounded answer out.

    The dispatcher classifies the request and routes it to the read-only conversational
    agent, people-search, or one of the four recommending agents — forwarding the caller's
    bearer token so scope + audit are inherited. Recommendations come back inert (L3); nothing
    is executed here. See agents/dispatch.py for the full set of guarantees.
    """
    tools = DataTools(_bearer(authorization))
    history = [{"role": t.role, "content": t.content} for t in req.history]
    return dispatch.dispatch(tools, req.question, history=history,
                             person_token=req.person_token, division=req.division)


@app.post("/agents/{name}/run")
def agent_run(name: str, req: RunRequest, authorization: str = Header(default="")) -> dict:
    """Run one L1-L3 agent. Returns insight + recommendations (inert L3 actions).

    The caller's bearer token is forwarded to the data layer, so the agent inherits
    permission scope + audit. Nothing here is executed — recommendations are proposals.
    """
    tools = DataTools(_bearer(authorization))
    if name in _SCOPED_AGENTS:
        return _SCOPED_AGENTS[name].run(tools, division=req.division)
    if name in _TOKEN_AGENTS:
        if not req.token:
            raise HTTPException(status_code=422, detail="this agent requires a token")
        return _TOKEN_AGENTS[name].run(tools, token=req.token)
    raise HTTPException(status_code=404, detail="unknown agent")


@app.post("/agents/{name}/approve")
def agent_approve(name: str, req: ApproveRequest,
                  authorization: str = Header(default="")) -> dict:
    """L3: a human approves a proposed action. Records the approval; runs NO side effect.

    This is the autonomy ceiling. `enforce` rejects anything above L3, and the only
    write is an audit row (and, once the engagement layer is wired, an opt-in
    notification). Comp/HR systems are never touched by this service.
    """
    if name not in _SCOPED_AGENTS and name not in _TOKEN_AGENTS:
        raise HTTPException(status_code=404, detail="unknown agent")
    enforce(req.level)  # defense in depth: never above L3
    tools = DataTools(_bearer(authorization))
    tools.log_run(agent=name, action=f"approve:{req.kind}", level=req.level,
                  summary={"kind": req.kind, "employee_token": req.employee_token,
                           "params": req.params}, result_count=1)

    # Engagement: notify the approver (opt-in; data layer drops it if not opted in).
    notified = {"created": False, "reason": "no_kind_mapping"}
    notify_kind = _NOTIFY_KIND.get(req.kind)
    if notify_kind:
        me = tools.me()
        notified = tools.notify(
            recipient_email=me["email"], kind=notify_kind,
            payload={"agent": name, "action": req.kind,
                     "employee_token": req.employee_token},
            why_text=f"You approved a {req.kind.replace('_', ' ')} via the {name} agent.")

    return {"agent": name, "approved": {"kind": req.kind,
                                        "employee_token": req.employee_token,
                                        "level": req.level},
            "executed": False, "notified": notified,
            "note": "approval recorded — no system action performed (L3 ceiling)"}
