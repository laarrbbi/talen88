"""HTTP API for the data layer (:8000).

Thin wrappers over the query seam, identity-resolution seam, and auth. The ONLY
surface the frontend talks to. Employee/score reads go through `data.query`
(token-only, scoped, audited); real names come ONLY from `data.identity.resolve_names`
via the isolated `/identity/resolve` endpoint, which the UI calls at render time.

Security posture:
  - All DB access is parameterized (see query.py / identity.py).
  - Inputs validated by pydantic + the query seam; invalid input -> generic 400.
  - Error handlers return generic messages and never leak stack traces, tokens,
    PII, or key material. Internal detail is logged server-side only.
"""
from __future__ import annotations

import logging
import os
import pathlib
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import (
    Depends, FastAPI, File, Form, Header, HTTPException, Path, Query, Request, UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import analytics, auth, cv_parse, identity, manual_ingest, notifications, query, surveys
from .audit import write_audit
from .db import db_path, get_connection, migrate
from .security.crypto import KeyError_

logger = logging.getLogger("pulsescore.api")



@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """Startup: apply additive schema migrations to an existing database (no-op when
    current), so a persistent production database picks up new columns."""
    if db_path().exists():
        conn = get_connection()
        try:
            migrate(conn)
        finally:
            conn.close()
    yield


app = FastAPI(title="PulseScore Data API", version="0.2.0", lifespan=_lifespan)

# Failed-login throttle (per email, in-process).
_login_throttle = auth.LoginThrottle()

# Local dev only: the Vite app runs on :5173. Assume TLS termination in front in prod.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ----- safe error handling (no internal detail leaks to clients) -------------
@app.exception_handler(query.QueryValidationError)
async def _validation_handler(_req: Request, exc: query.QueryValidationError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(auth.AuthError)
async def _auth_handler(_req: Request, _exc: auth.AuthError):
    return JSONResponse(status_code=401, content={"detail": "authentication failed"})


@app.exception_handler(KeyError_)
async def _crypto_handler(_req: Request, _exc: KeyError_):
    logger.error("crypto/key error during request")  # no key/PII in the log line
    return JSONResponse(status_code=500, content={"detail": "internal error"})


@app.exception_handler(Exception)
async def _unhandled(_req: Request, exc: Exception):
    logger.error("unhandled error: %s", type(exc).__name__)  # type only, no payload
    return JSONResponse(status_code=500, content={"detail": "internal error"})


# ----- models ----------------------------------------------------------------
class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)


class ResolveRequest(BaseModel):
    tokens: list[str] = Field(default_factory=list, max_length=1000)


class IdentitySearchRequest(BaseModel):
    """Name typeahead for the UI person-picker. `q` is a partial name; matching happens
    behind the scoped, audited identity boundary. Scope is enforced server-side."""
    q: str = Field(min_length=1, max_length=120)
    limit: int = Field(default=8, ge=1, le=25)


class AgentAuditRequest(BaseModel):
    """An agent recording what it saw + proposed. Autonomy level is bounded to L1-L3;
    L4 (auto-execution) is rejected here as a defense-in-depth backstop."""
    agent: str = Field(min_length=1, max_length=64)
    action: str = Field(min_length=1, max_length=64)
    level: int = Field(ge=1, le=3)              # L4 is out of scope; rejected by range
    summary: dict[str, Any] = Field(default_factory=dict)
    result_count: int = Field(default=0, ge=0)


class NotificationRequest(BaseModel):
    """Create an in-app notification for an operator. Honored only if they opted in."""
    recipient_email: str = Field(min_length=3, max_length=254)
    kind: str = Field(min_length=1, max_length=64)
    payload: dict[str, Any] = Field(default_factory=dict)
    why_text: str | None = Field(default=None, max_length=300)


class SearchRequest(BaseModel):
    """Multi-field people search. `criteria` is a flat {field: value} map parsed by the
    caller (the Graph chat's LLM) into recognized canonical fields; unknown/walled fields
    are returned in `unsupported_fields`, never applied. Scope is enforced server-side."""
    criteria: dict[str, Any] = Field(default_factory=dict)
    limit: int | None = Field(default=None, ge=1, le=200)


class PrefRequest(BaseModel):
    """Set one per-kind opt-in for the calling operator (default channel in_app)."""
    kind: str = Field(min_length=1, max_length=64)
    opted_in: bool
    channel: str = Field(default="in_app", pattern="^(in_app|slack|teams|email|mobile)$")


class EmployeeCreateRequest(BaseModel):
    """Confirmed fields for a manually-added employee (Document Import). Names/email are the
    only PII and are encrypted on write; everything else is the canonical employee record.
    Light validation here; the data layer (manual_ingest) does full vocab/normalize checks."""
    full_name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=3, max_length=254)
    role: str = Field(min_length=1, max_length=120)
    title: str | None = Field(default=None, max_length=120)
    level: str = Field(min_length=1, max_length=32)            # int or grade label (e.g. "VP")
    division: str = Field(min_length=1, max_length=64)         # open vocab (custom allowed)
    team: str | None = Field(default=None, max_length=80)
    location: str = Field(min_length=1, max_length=80)
    employment_type: str = Field(min_length=1, max_length=32)
    status: str | None = Field(default="active", max_length=24)
    business_travel_frequency: str | None = Field(default=None, max_length=24)
    hire_date: str = Field(min_length=4, max_length=24)
    base_salary: float = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    manager_token: str | None = Field(default=None, max_length=32)
    # optional groups — all skippable. Validated/normalized in data.manual_ingest.
    bonus: float | None = Field(default=None, ge=0)
    equity: str | None = Field(default=None, max_length=200)
    pay_band: str | None = Field(default=None, max_length=40)
    last_raise_date: str | None = Field(default=None, max_length=24)
    performance_rating: float | None = Field(default=None, ge=0, le=5)
    goal_attainment: float | None = Field(default=None, ge=0, le=3)
    years_of_experience: int | None = Field(default=None, ge=0, le=70)
    prior_employer_count: int | None = Field(default=None, ge=0, le=60)
    total_working_years: float | None = Field(default=None, ge=0, le=70)
    time_since_last_promotion: int | None = Field(default=None, ge=0, le=600)
    # demographics — bias-audit only, never scored
    gender: str | None = Field(default=None, max_length=16)
    marital_status: str | None = Field(default=None, max_length=16)
    birth_date: str | None = Field(default=None, max_length=24)
    birth_year_band: str | None = Field(default=None, max_length=12)
    # education & skills
    education_level: str | None = Field(default=None, max_length=16)
    education_field: str | None = Field(default=None, max_length=24)
    education_institution: str | None = Field(default=None, max_length=120)
    skills: list[str] | None = Field(default=None, max_length=60)
    certifications: list[str] | None = Field(default=None, max_length=40)
    licenses: list[str] | None = Field(default=None, max_length=40)
    languages: list[str] | None = Field(default=None, max_length=30)
    hobbies: list[str] | None = Field(default=None, max_length=30)
    # lifecycle / compliance
    onboarding_status: str | None = Field(default=None, max_length=16)
    credential_name: str | None = Field(default=None, max_length=80)
    credential_expiry: str | None = Field(default=None, max_length=24)
    distance_from_home: float | None = Field(default=None, ge=0, le=30000)
    # profile photo (small data-URL thumbnail)
    photo: str | None = Field(default=None, max_length=1_400_000)


def get_actor(authorization: str = Header(default="")) -> auth.Actor:
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    try:
        return auth.decode_token(authorization.split(" ", 1)[1].strip())
    except auth.AuthError:
        raise HTTPException(status_code=401, detail="authentication failed")


# ----- endpoints -------------------------------------------------------------
@app.post("/auth/login")
def login(req: LoginRequest) -> dict[str, Any]:
    if _login_throttle.is_locked(req.email):
        raise HTTPException(status_code=429,
                            detail="too many failed attempts; try again in 15 minutes")
    conn = get_connection()
    try:
        actor = auth.authenticate(conn, req.email, req.password)
        write_audit(conn, actor_email=actor.email, action="login",
                    filters={"ok": True}, result_count=1)
    except auth.AuthError:
        _login_throttle.record_failure(req.email)
        # Record the attempt without the password or a hint about which part was wrong.
        write_audit(conn, actor_email=req.email.strip().lower()[:254], action="login",
                    filters={"ok": False}, result_count=0)
        raise HTTPException(status_code=401, detail="authentication failed")
    finally:
        conn.close()
    _login_throttle.reset(req.email)
    return {"token": auth.issue_token(actor), "email": actor.email, "name": actor.name,
            "role": actor.role, "division": actor.division,
            "expires_in": auth.TOKEN_TTL_SECONDS}


@app.get("/me")
def me(actor: auth.Actor = Depends(get_actor)) -> dict[str, Any]:
    return {"email": actor.email, "name": actor.name, "role": actor.role,
            "division": actor.division}


@app.get("/employees")
def employees(
    actor: auth.Actor = Depends(get_actor),
    division: str | None = Query(default=None, max_length=64),
    manager_token: str | None = Query(default=None, max_length=32),
    risk_band: str | None = Query(default=None, pattern="^(high|medium|low)$"),
    comp_gap: float | None = Query(default=None, ge=0.0, le=1.0),
    sort: str | None = Query(default=None, max_length=32),
    limit: int | None = Query(default=None, ge=1, le=1000),
    include_former: bool = Query(default=False),
) -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        return query.get_employees(conn, actor=actor, division=division,
                                   manager_token=manager_token, risk_band=risk_band,
                                   comp_gap=comp_gap, sort=sort, limit=limit,
                                   include_former=include_former)
    finally:
        conn.close()


@app.post("/employees/search")
def employees_search(req: SearchRequest,
                     actor: auth.Actor = Depends(get_actor)) -> dict[str, Any]:
    """Scoped, audited multi-field people search (token-only). Powers the Company Graph's
    natural-language discovery. Permission scope is enforced inside the query seam, so a
    manager can never reach past their division — even by naming another one in criteria."""
    conn = get_connection()
    try:
        return query.search_people(conn, actor=actor, criteria=req.criteria, limit=req.limit)
    except query.QueryValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        conn.close()


@app.get("/employees/{token}")
def employee_history(token: str, actor: auth.Actor = Depends(get_actor)) -> dict[str, Any]:
    if len(token) > 32:
        raise HTTPException(status_code=400, detail="invalid token")
    conn = get_connection()
    try:
        record = query.get_employee_history(conn, token, actor=actor)
    finally:
        conn.close()
    if not record:
        raise HTTPException(status_code=404, detail="not found or out of scope")
    return record


@app.get("/employees/{token}/360")
def employee_360(token: str, actor: auth.Actor = Depends(get_actor)) -> dict[str, Any]:
    """Unified Employee-360 profile: core + 360 attributes + skills + the full
    8-score/capital panel (each with reason codes) + retention trend. Token-only,
    scoped + audited inside the query seam. Names are NOT included; the UI resolves
    them separately via /identity/resolve for authorized records only."""
    if len(token) > 32:
        raise HTTPException(status_code=400, detail="invalid token")
    conn = get_connection()
    try:
        record = query.get_employee_360(conn, token, actor=actor)
    finally:
        conn.close()
    if not record:
        raise HTTPException(status_code=404, detail="not found or out of scope")
    return record


@app.post("/identity/resolve")
def resolve(req: ResolveRequest, actor: auth.Actor = Depends(get_actor)) -> dict[str, str]:
    """Resolve tokens -> real names, for authorized records only. Audited per call.

    This is the single, isolated name-resolution boundary. Locking it down (e.g.
    disabling, or requiring a stricter role) affects no query/scoring logic.
    """
    conn = get_connection()
    try:
        return identity.resolve_names(conn, req.tokens, actor=actor)
    finally:
        conn.close()


@app.post("/identity/photos")
def resolve_photos(req: ResolveRequest, actor: auth.Actor = Depends(get_actor)) -> dict[str, str]:
    """Resolve tokens -> profile-picture data URLs, for authorized records only. The same
    isolated identity boundary as /identity/resolve (scoped + audited); tokens with no photo
    are simply omitted, so the UI falls back to the generated avatar."""
    conn = get_connection()
    try:
        return identity.resolve_photos(conn, req.tokens, actor=actor)
    finally:
        conn.close()


@app.post("/identity/search")
def identity_search(req: IdentitySearchRequest,
                    actor: auth.Actor = Depends(get_actor)) -> list[dict[str, Any]]:
    """Name->token typeahead for the UI person-picker, scoped + audited.

    Mirrors /identity/resolve's permission scope (admin: all; manager: own division).
    Names never leave this boundary except as the picker's own display rows; the chosen
    person is carried onward to the agents only as an opaque token.
    """
    conn = get_connection()
    try:
        return identity.search_identities(conn, req.q, actor=actor, limit=req.limit)
    finally:
        conn.close()


@app.post("/audit/agent")
def audit_agent(req: AgentAuditRequest, actor: auth.Actor = Depends(get_actor)) -> dict[str, Any]:
    """Append an agent's run to the tamper-evident audit trail (what it saw + proposed).
    Agents have no DB access, so this is the single, scoped, signed write path. The
    DB still owns the hash chain; the level is bounded to L1-L3 (no auto-execution)."""
    conn = get_connection()
    try:
        write_audit(conn, actor_email=actor.email, action=f"agent:{req.agent}:{req.action}",
                    filters={"level": req.level, **req.summary}, result_count=req.result_count)
    finally:
        conn.close()
    return {"logged": True}


@app.get("/dashboard")
def dashboard(actor: auth.Actor = Depends(get_actor)) -> dict[str, Any]:
    conn = get_connection()
    try:
        return query.get_dashboard_metrics(conn, actor=actor)
    finally:
        conn.close()


@app.get("/team_pulse")
def team_pulse(
    actor: auth.Actor = Depends(get_actor),
    division: str | None = Query(default=None, max_length=64),
    manager_token: str | None = Query(default=None, max_length=32),
    horizon_days: int = Query(default=30, ge=1, le=90),
) -> dict[str, Any]:
    """Operational "Team Pulse" digest for the Dashboard (token-only, scoped + audited).

    Reuses the query seam's scope (manager: own division; admin: all) and audit. The
    payload carries only neutral operational signals — never a leave reason or any
    health detail (see query.get_team_pulse)."""
    conn = get_connection()
    try:
        return query.get_team_pulse(conn, actor=actor, division=division,
                                    manager_token=manager_token, horizon_days=horizon_days)
    finally:
        conn.close()


@app.get("/divisions")
def divisions(actor: auth.Actor = Depends(get_actor)) -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        rows = conn.execute("SELECT id, name FROM divisions ORDER BY name").fetchall()
    finally:
        conn.close()
    out = [{"id": r["id"], "name": r["name"]} for r in rows]
    return out if actor.is_admin else [d for d in out if d["name"] == actor.division]


# ----- survey module: builder (read seam) ------------------------------------
@app.get("/surveys/library")
def surveys_library(actor: auth.Actor = Depends(get_actor)) -> dict[str, Any]:
    """The science-backed question bank (scales + drivers + items) for the builder."""
    conn = get_connection()
    try:
        return surveys.get_library(conn, actor=actor)
    finally:
        conn.close()


@app.get("/surveys/templates")
def surveys_templates(actor: auth.Actor = Depends(get_actor)) -> list[dict[str, Any]]:
    """The seeded survey templates (blueprints) with their item counts."""
    conn = get_connection()
    try:
        return surveys.list_templates(conn, actor=actor)
    finally:
        conn.close()


@app.get("/surveys/templates/{template_id}")
def surveys_template(template_id: int = Path(ge=1),
                     actor: auth.Actor = Depends(get_actor)) -> dict[str, Any]:
    """One template with its ordered questions resolved."""
    conn = get_connection()
    try:
        tpl = surveys.get_template(conn, actor=actor, template_id=template_id)
    finally:
        conn.close()
    if not tpl:
        raise HTTPException(status_code=404, detail="template not found")
    return tpl


class CampaignCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    type: str
    template_id: int | None = None
    item_ids: list[int] | None = None
    audience: dict[str, Any] | None = None
    cadence: str | None = Field(default=None, max_length=32)
    trigger_event: str | None = None
    trigger_offset_days: int | None = Field(default=None, ge=0, le=3650)
    anonymous: bool = False
    min_threshold: int = Field(default=5, ge=1, le=1000)
    opens_at: str | None = Field(default=None, max_length=32)
    closes_at: str | None = Field(default=None, max_length=32)


class StatusUpdate(BaseModel):
    status: str = Field(max_length=20)


class HrisEvent(BaseModel):
    event: str = Field(max_length=20)
    token: str = Field(max_length=32)


@app.post("/surveys/campaigns")
def surveys_create_campaign(req: CampaignCreate,
                            actor: auth.Actor = Depends(get_actor)) -> dict[str, Any]:
    """Create a draft campaign and freeze its questions (scoped to the actor's division)."""
    conn = get_connection()
    try:
        return surveys.create_campaign(conn, actor=actor, **req.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        conn.close()


@app.get("/surveys/campaigns")
def surveys_list_campaigns(actor: auth.Actor = Depends(get_actor),
                           status: str | None = Query(default=None, max_length=20)) -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        return surveys.list_campaigns(conn, actor=actor, status=status)
    finally:
        conn.close()


@app.get("/surveys/campaigns/{campaign_id}")
def surveys_get_campaign(campaign_id: int = Path(ge=1),
                         actor: auth.Actor = Depends(get_actor)) -> dict[str, Any]:
    conn = get_connection()
    try:
        camp = surveys.get_campaign(conn, actor=actor, campaign_id=campaign_id)
    finally:
        conn.close()
    if not camp:
        raise HTTPException(status_code=404, detail="campaign not found")
    return camp


@app.post("/surveys/campaigns/{campaign_id}/status")
def surveys_set_status(req: StatusUpdate, campaign_id: int = Path(ge=1),
                       actor: auth.Actor = Depends(get_actor)) -> dict[str, Any]:
    conn = get_connection()
    try:
        res = surveys.set_campaign_status(conn, actor=actor, campaign_id=campaign_id, status=req.status)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        conn.close()
    if not res:
        raise HTTPException(status_code=404, detail="campaign not found")
    return res


@app.post("/surveys/campaigns/{campaign_id}/launch")
def surveys_launch(campaign_id: int = Path(ge=1),
                   actor: auth.Actor = Depends(get_actor)) -> dict[str, Any]:
    """Distribute: resolve audience → invitations, set active (token-only, audited)."""
    conn = get_connection()
    try:
        res = surveys.launch_campaign(conn, actor=actor, campaign_id=campaign_id)
    finally:
        conn.close()
    if not res:
        raise HTTPException(status_code=404, detail="campaign not found")
    return res


@app.post("/surveys/hris_event")
def surveys_hris_event(req: HrisEvent,
                       actor: auth.Actor = Depends(get_actor)) -> dict[str, Any]:
    """HRIS-event trigger (admin only): hire → onboarding, termination → exit enrolment."""
    if not actor.is_admin:
        raise HTTPException(status_code=403, detail="admin only")
    conn = get_connection()
    try:
        return surveys.enroll_lifecycle(conn, event=req.event, token=req.token,
                                        actor_email=actor.email)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        conn.close()


class RespondRequest(BaseModel):
    token: str = Field(max_length=32)
    answers: list[dict[str, Any]]


@app.post("/surveys/campaigns/{campaign_id}/respond")
def surveys_respond(req: RespondRequest, campaign_id: int = Path(ge=1),
                    actor: auth.Actor = Depends(get_actor)) -> dict[str, Any]:
    """Capture a respondent's answers (token-linked, immutable). Production authenticates
    the respondent by a signed survey link; here the body carries the respondent token."""
    conn = get_connection()
    try:
        return surveys.submit_response(conn, token=req.token, campaign_id=campaign_id,
                                       answers=req.answers)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        conn.close()


@app.get("/surveys/campaigns/{campaign_id}/results")
def surveys_results(campaign_id: int = Path(ge=1),
                    actor: auth.Actor = Depends(get_actor)) -> dict[str, Any]:
    """Confidential aggregate results — suppressed below the min-reporting threshold."""
    conn = get_connection()
    try:
        res = surveys.campaign_results(conn, actor=actor, campaign_id=campaign_id)
    finally:
        conn.close()
    if not res:
        raise HTTPException(status_code=404, detail="campaign not found")
    return res


@app.post("/surveys/campaigns/{campaign_id}/close")
def surveys_close(campaign_id: int = Path(ge=1),
                  actor: auth.Actor = Depends(get_actor)) -> dict[str, Any]:
    """Close a campaign and write results into the engagement table (feeds the FR model)."""
    conn = get_connection()
    try:
        res = surveys.close_campaign(conn, actor=actor, campaign_id=campaign_id)
    finally:
        conn.close()
    if not res:
        raise HTTPException(status_code=404, detail="campaign not found")
    return res


# ----- engagement layer: in-app, opt-in notifications ------------------------
@app.get("/notifications")
def list_notifications(actor: auth.Actor = Depends(get_actor),
                       unread_only: bool = Query(default=False)) -> list[dict[str, Any]]:
    """The caller's OWN notifications (scoped to actor.email). Newest first."""
    conn = get_connection()
    try:
        return notifications.list_for(conn, actor_email=actor.email,
                                      unread_only=unread_only)
    finally:
        conn.close()


@app.post("/notifications")
def create_notification(req: NotificationRequest,
                        actor: auth.Actor = Depends(get_actor)) -> dict[str, Any]:
    """Create a notification — honored only if the recipient opted into the kind.

    A non-admin may only address a notification to themselves; this prevents one
    operator from pushing into another's inbox. The opt-in check is enforced in
    `notifications.create` (nothing is written when the recipient opted out).
    """
    if not actor.is_admin and req.recipient_email.strip().lower() != actor.email:
        raise HTTPException(status_code=403, detail="cannot notify another recipient")
    conn = get_connection()
    try:
        return notifications.create(conn, recipient_email=req.recipient_email.strip().lower(),
                                    kind=req.kind, payload=req.payload, why_text=req.why_text)
    finally:
        conn.close()


@app.post("/notifications/{notif_id}/read")
def read_notification(notif_id: int,
                      actor: auth.Actor = Depends(get_actor)) -> dict[str, Any]:
    """Mark one of the caller's own notifications read. 404 if it is not theirs."""
    conn = get_connection()
    try:
        ok = notifications.mark_read(conn, actor_email=actor.email, notif_id=notif_id)
    finally:
        conn.close()
    if not ok:
        raise HTTPException(status_code=404, detail="not found")
    return {"read": True, "id": notif_id}


@app.get("/notification_prefs")
def get_notification_prefs(actor: auth.Actor = Depends(get_actor)) -> list[dict[str, Any]]:
    """All notification kinds + the caller's opt-in state (default off)."""
    conn = get_connection()
    try:
        return notifications.get_prefs(conn, actor_email=actor.email)
    finally:
        conn.close()


@app.put("/notification_prefs")
def set_notification_pref(req: PrefRequest,
                          actor: auth.Actor = Depends(get_actor)) -> dict[str, Any]:
    """Set one per-kind opt-in for the caller. Unknown kinds -> 400."""
    conn = get_connection()
    try:
        res = notifications.set_pref(conn, actor_email=actor.email, kind=req.kind,
                                     opted_in=req.opted_in, channel=req.channel)
    finally:
        conn.close()
    if not res.get("updated"):
        raise HTTPException(status_code=400, detail="unknown notification kind")
    return res


# ----- analytics: aggregate, exploratory GOLD-layer views (token-only) --------
# Eight read-only endpoints behind the Analytics section. Each reuses the query
# seam's scope + audit through data.analytics, enforces minimum-segment-size
# suppression server-side, and returns a data_status so the UI renders the right
# state (live chart vs locked "connect a source" card) instead of faking data.
def _analytics_filters(
    division: str | None = Query(default=None, max_length=64),
    manager: str | None = Query(default=None, max_length=32),
    location: str | None = Query(default=None, max_length=64),
    tenure_band: str | None = Query(default=None, max_length=8),
    level: int | None = Query(default=None, ge=1, le=10),
    date_range: str | None = Query(default=None, max_length=24),
) -> dict[str, Any]:
    return {"division": division, "manager": manager, "location": location,
            "tenure_band": tenure_band, "level": level, "date_range": date_range}


@app.get("/analytics/turnover")
def analytics_turnover(actor: auth.Actor = Depends(get_actor),
                       f: dict[str, Any] = Depends(_analytics_filters)) -> dict[str, Any]:
    conn = get_connection()
    try:
        return analytics.get_turnover(conn, actor=actor, **f)
    finally:
        conn.close()


@app.get("/analytics/drivers")
def analytics_drivers(actor: auth.Actor = Depends(get_actor),
                      f: dict[str, Any] = Depends(_analytics_filters)) -> dict[str, Any]:
    conn = get_connection()
    try:
        return analytics.get_drivers(conn, actor=actor, **f)
    finally:
        conn.close()


@app.get("/analytics/managers")
def analytics_managers(actor: auth.Actor = Depends(get_actor),
                       f: dict[str, Any] = Depends(_analytics_filters)) -> dict[str, Any]:
    conn = get_connection()
    try:
        return analytics.get_managers(conn, actor=actor, **f)
    finally:
        conn.close()


@app.get("/analytics/fairness")
def analytics_fairness(actor: auth.Actor = Depends(get_actor),
                       metric: str = Query(default="flight_risk", max_length=24)) -> dict[str, Any]:
    conn = get_connection()
    try:
        return analytics.get_fairness(conn, actor=actor, metric=metric)
    finally:
        conn.close()


@app.get("/analytics/cost")
def analytics_cost(actor: auth.Actor = Depends(get_actor),
                   f: dict[str, Any] = Depends(_analytics_filters),
                   scenario_n: int = Query(default=10, ge=1, le=100)) -> dict[str, Any]:
    conn = get_connection()
    try:
        return analytics.get_cost(conn, actor=actor, scenario_n=scenario_n, **f)
    finally:
        conn.close()


@app.get("/analytics/compensation")
def analytics_compensation(actor: auth.Actor = Depends(get_actor),
                           f: dict[str, Any] = Depends(_analytics_filters)) -> dict[str, Any]:
    conn = get_connection()
    try:
        return analytics.get_compensation(conn, actor=actor, **f)
    finally:
        conn.close()


@app.get("/analytics/engagement")
def analytics_engagement(actor: auth.Actor = Depends(get_actor),
                         f: dict[str, Any] = Depends(_analytics_filters)) -> dict[str, Any]:
    conn = get_connection()
    try:
        return analytics.get_engagement(conn, actor=actor, **f)
    finally:
        conn.close()


@app.get("/analytics/forecast")
def analytics_forecast(actor: auth.Actor = Depends(get_actor),
                       f: dict[str, Any] = Depends(_analytics_filters)) -> dict[str, Any]:
    conn = get_connection()
    try:
        return analytics.get_forecast(conn, actor=actor, **f)
    finally:
        conn.close()


# ----- document import: real manual / CV-assisted employee creation -----------
# The Document Import surface's write path. Unlike the HRIS-enrichment ingestion
# (data.ingestion, which matches existing people), this MINTS a brand-new employee
# from confirmed fields across identity + base + canonical + compensation, so the
# record appears immediately in the Employees list, Profile, and headcount/comp
# analytics. Admin-only; audited. Divisions are data-driven (custom names allowed).
@app.get("/import/divisions")
def import_divisions(actor: auth.Actor = Depends(get_actor)) -> list[dict[str, Any]]:
    """Registered divisions + headcount for the add-employee picker (existing + custom)."""
    conn = get_connection()
    try:
        return manual_ingest.list_divisions(conn)
    finally:
        conn.close()


@app.post("/import/parse_document")
async def import_parse_document(actor: auth.Actor = Depends(get_actor),
                                file: UploadFile = File(...),
                                doc_type: str = Form("auto")) -> dict[str, Any]:
    """Extract text from an uploaded employee document (CV / contract / offer / payslip /
    review — PDF / Word / text) and suggest the fields it carries. `doc_type` defaults to
    "auto" (classified from the file). Read-only: nothing is written. Admin-only."""
    if not actor.is_admin:
        raise HTTPException(status_code=403, detail="only an admin may import employees")
    raw = await file.read()
    if len(raw) > 8 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="file too large (max 8 MB)")
    try:
        text = cv_parse.extract_text(file.filename or "", raw)
    except cv_parse.UnsupportedDocument as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    result = cv_parse.suggest_fields(text, doc_type=doc_type, filename=file.filename or "")
    result["filename"] = file.filename
    return result


@app.post("/import/employee")
def import_employee(req: EmployeeCreateRequest,
                    actor: auth.Actor = Depends(get_actor)) -> dict[str, Any]:
    """Create one real employee from confirmed fields. Admin-only; audited.
    400 on bad input (vocab/date/duplicate), 403 for non-admins."""
    conn = get_connection()
    try:
        return manual_ingest.create_employee(conn, actor=actor, payload=req.model_dump())
    except PermissionError:
        raise HTTPException(status_code=403, detail="only an admin may add employees")
    except manual_ingest.ManualIngestError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    finally:
        conn.close()


# ---- Single-origin production wiring ----------------------------------------
# When deployed as a single container the data API proxies the internal agents
# service (port 8002) at /agents/* and serves the built React SPA at /.
# In local dev neither route is registered (app/dist doesn't exist, and the
# agents service is addressed directly by the Vite dev proxy).

_AGENTS_INTERNAL = os.environ.get("AGENTS_INTERNAL_URL", "http://127.0.0.1:8002")
_STATIC_DIR = pathlib.Path(__file__).resolve().parent.parent / "app" / "dist"


@app.api_route("/agents/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def _proxy_agents_svc(path: str, request: Request) -> Response:
    """Transparent proxy to the internal agents service — single public origin."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.request(
            method=request.method,
            url=f"{_AGENTS_INTERNAL}/agents/{path}",
            headers={k: v for k, v in request.headers.items() if k.lower() != "host"},
            content=await request.body(),
            params=dict(request.query_params),
        )
    skip = {"content-encoding", "transfer-encoding", "content-length"}
    return Response(
        content=resp.content,
        status_code=resp.status_code,
        headers={k: v for k, v in resp.headers.items() if k.lower() not in skip},
    )


if _STATIC_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(_STATIC_DIR), html=True), name="spa")
