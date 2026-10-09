"""Survey module — read seam for the builder (Step 2).

Token-only / audited reads over the seeded question library + templates. The library
is org-global reference content (no per-division scope), so any authenticated actor can
read it to build a survey; every read is still audited like the rest of the seam.
Campaign creation, distribution and confidential response capture land in later steps.
"""
from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from datetime import date, datetime, timezone
from typing import Any

from . import canonical
from .audit import write_audit
from .auth import Actor
from .survey_library import SCALES

# Map a response scale to the builder's question type.
_QTYPE_BY_SCALE = {"AGREE5": "likert", "FREQ5": "likert", "ENPS": "enps", "OPEN": "open_text"}

# Mirror the CHECK constraints so invalid input fails before it hits SQLite.
CAMPAIGN_TYPES = {"engagement", "pulse", "lifecycle", "enps", "manager_effectiveness",
                  "dei", "wellbeing", "change", "custom"}
CAMPAIGN_STATUSES = {"draft", "scheduled", "active", "closed", "archived"}
TRIGGER_EVENTS = {"hire", "termination", "role_change"}
# Audience fields resolvable from the base employees table (token-only, no PII).
_AUDIENCE_FIELDS = ("team", "manager_token", "employment_type")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _scope_division(actor: Actor, requested: str | None) -> str | None:
    """Mirror of query._scope_division: a manager is always pinned to their own division;
    an admin may target a requested division (or all when None)."""
    return requested if actor.is_admin else actor.division


def qtype_for_scale(scale: str | None) -> str:
    """Builder question type for a library scale (AGREE5/FREQ5 -> likert, etc.)."""
    return _QTYPE_BY_SCALE.get(scale or "", "likert")


def get_library(conn: sqlite3.Connection, *, actor: Actor) -> dict[str, Any]:
    """The full question bank for the builder: response scales + drivers with their items."""
    drivers = conn.execute(
        "SELECT code, name, description, is_outcome FROM survey_driver ORDER BY rowid").fetchall()
    items = conn.execute(
        "SELECT id, driver_code, text, scale, lifecycle_tag FROM survey_item "
        "WHERE active = 1 ORDER BY id").fetchall()

    by_driver: dict[str, list[dict[str, Any]]] = {}
    for it in items:
        by_driver.setdefault(it["driver_code"], []).append({
            "id": it["id"], "text": it["text"], "scale": it["scale"],
            "qtype": qtype_for_scale(it["scale"]), "lifecycle_tag": it["lifecycle_tag"]})

    out_drivers = [{
        "code": d["code"], "name": d["name"], "description": d["description"],
        "is_outcome": d["is_outcome"], "items": by_driver.get(d["code"], [])}
        for d in drivers]
    scales = [{"key": k, "label": lbl, "options": opts} for k, lbl, opts in SCALES]

    write_audit(conn, actor_email=actor.email, action="survey_library", result_count=len(items))
    return {"scales": scales, "drivers": out_drivers,
            "driver_count": len(out_drivers), "item_count": len(items)}


def list_templates(conn: sqlite3.Connection, *, actor: Actor) -> list[dict[str, Any]]:
    """All survey templates with their item counts (the builder's starting blueprints)."""
    rows = conn.execute(
        "SELECT t.id, t.key, t.title, t.type, t.cadence, t.description, t.builtin, "
        "COUNT(ti.item_id) AS item_count "
        "FROM survey_template t LEFT JOIN survey_template_item ti ON ti.template_id = t.id "
        "GROUP BY t.id ORDER BY t.id").fetchall()
    write_audit(conn, actor_email=actor.email, action="survey_templates", result_count=len(rows))
    return [dict(r) for r in rows]


def get_template(conn: sqlite3.Connection, *, actor: Actor,
                 template_id: int | None = None, key: str | None = None) -> dict[str, Any]:
    """One template with its ordered questions resolved (what a campaign would freeze)."""
    if template_id is not None:
        row = conn.execute("SELECT * FROM survey_template WHERE id = ?", (template_id,)).fetchone()
    elif key is not None:
        row = conn.execute("SELECT * FROM survey_template WHERE key = ?", (key,)).fetchone()
    else:
        row = None

    if row is None:
        write_audit(conn, actor_email=actor.email, action="survey_template",
                    filters={"template_id": template_id, "key": key}, result_count=0)
        return {}

    questions = conn.execute(
        "SELECT ti.position, i.id AS item_id, i.driver_code, i.text, i.scale, "
        "d.name AS driver_name FROM survey_template_item ti "
        "JOIN survey_item i ON i.id = ti.item_id "
        "LEFT JOIN survey_driver d ON d.code = i.driver_code "
        "WHERE ti.template_id = ? ORDER BY ti.position", (row["id"],)).fetchall()
    out_q = [{
        "position": q["position"], "item_id": q["item_id"], "driver_code": q["driver_code"],
        "driver_name": q["driver_name"], "text": q["text"], "scale": q["scale"],
        "qtype": qtype_for_scale(q["scale"])} for q in questions]

    write_audit(conn, actor_email=actor.email, action="survey_template",
                filters={"template_id": row["id"]}, result_count=len(out_q))
    return {"id": row["id"], "key": row["key"], "title": row["title"], "type": row["type"],
            "cadence": row["cadence"], "description": row["description"],
            "builtin": row["builtin"], "questions": out_q}


# ============================================================================
# Campaigns + distribution (Step 3). Scoped + audited like the rest of the seam.
# A manager is pinned to their own division; an admin may target any/all.
# ============================================================================
def _resolve_tokens(conn: sqlite3.Connection, audience: dict[str, Any]) -> list[str]:
    """Resolve an audience spec to employee tokens off the base `employees` table.

    Honours division/team/manager_token/level/employment_type. The audience's division
    is taken as-is here — scope pinning happens at create time (it is baked into the
    stored audience_json), so launch/enroll resolve exactly what was authorized.
    """
    # People who have left are never surveyed.
    where = ["token NOT IN (SELECT ec.employee_token FROM employee_core ec "
             f"WHERE NOT ({canonical.current_employee_sql('ec')}))"]
    params: list[Any] = []
    if audience.get("division"):
        where.append("division = ?")
        params.append(audience["division"])
    for field in _AUDIENCE_FIELDS:
        if audience.get(field):
            where.append(f"{field} = ?")
            params.append(audience[field])
    if audience.get("level") is not None:
        where.append("level = ?")
        params.append(int(audience["level"]))
    rows = conn.execute(
        f"SELECT token FROM employees WHERE {' AND '.join(where)} ORDER BY token", params)
    return [r["token"] for r in rows]


def resolve_audience(conn: sqlite3.Connection, *, actor: Actor,
                     audience: dict[str, Any] | None) -> list[str]:
    """Scoped audience preview: pin the division to the actor, then resolve to tokens."""
    aud = dict(audience or {})
    aud["division"] = _scope_division(actor, aud.get("division"))
    return _resolve_tokens(conn, aud)


def _campaign_division(audience_json: str | None) -> str | None:
    try:
        return (json.loads(audience_json) or {}).get("division") if audience_json else None
    except (ValueError, TypeError):
        return None


def _can_access(actor: Actor, audience_json: str | None) -> bool:
    """Admin sees all; a manager sees only campaigns pinned to their own division."""
    if actor.is_admin:
        return True
    return _campaign_division(audience_json) == actor.division


def create_campaign(
    conn: sqlite3.Connection, *, actor: Actor, title: str, type: str,
    template_id: int | None = None, item_ids: list[int] | None = None,
    audience: dict[str, Any] | None = None, cadence: str | None = None,
    trigger_event: str | None = None, trigger_offset_days: int | None = None,
    anonymous: bool = False, min_threshold: int = 5,
    opens_at: str | None = None, closes_at: str | None = None,
) -> dict[str, Any]:
    """Create a draft campaign and FREEZE its questions (a later library edit never
    mutates an already-built campaign). Questions come from a template and/or explicit
    item ids. The audience division is pinned to the actor's scope."""
    if type not in CAMPAIGN_TYPES:
        raise ValueError(f"invalid campaign type: {type}")
    if trigger_event is not None and trigger_event not in TRIGGER_EVENTS:
        raise ValueError(f"invalid trigger_event: {trigger_event}")

    aud = dict(audience or {})
    aud["division"] = _scope_division(actor, aud.get("division"))  # pin to scope
    aud_json = json.dumps(aud, sort_keys=True)

    cur = conn.execute(
        "INSERT INTO survey_campaign (template_id, title, type, status, cadence, "
        "audience_json, trigger_event, trigger_offset_days, anonymous, min_threshold, "
        "opens_at, closes_at, created_by, created_ts) "
        "VALUES (?,?,?, 'draft', ?,?,?,?,?,?,?,?,?,?)",
        (template_id, title, type, cadence, aud_json, trigger_event, trigger_offset_days,
         1 if anonymous else 0, int(min_threshold), opens_at, closes_at, actor.email, _now()))
    cid = int(cur.lastrowid)

    # Freeze questions: template items first (in order), then any extra explicit items.
    position = 0
    sources: list[sqlite3.Row] = []
    if template_id is not None:
        sources.extend(conn.execute(
            "SELECT i.id, i.driver_code, i.text, i.scale FROM survey_template_item ti "
            "JOIN survey_item i ON i.id = ti.item_id WHERE ti.template_id = ? "
            "ORDER BY ti.position", (template_id,)).fetchall())
    for iid in (item_ids or []):
        r = conn.execute(
            "SELECT id, driver_code, text, scale FROM survey_item WHERE id = ?", (iid,)).fetchone()
        if r is not None:
            sources.append(r)
    for r in sources:
        conn.execute(
            "INSERT INTO survey_question (campaign_id, item_id, driver_code, position, text, "
            "qtype, scale, required) VALUES (?,?,?,?,?,?,?,1)",
            (cid, r["id"], r["driver_code"], position, r["text"],
             qtype_for_scale(r["scale"]), r["scale"]))
        position += 1

    conn.commit()
    write_audit(conn, actor_email=actor.email, action="survey_create_campaign",
                filters={"campaign_id": cid, "type": type, "division": aud["division"],
                         "questions": position}, result_count=position)
    return {"id": cid, "status": "draft", "questions": position,
            "division": aud["division"], "type": type}


def _counts(conn: sqlite3.Connection, cid: int) -> dict[str, int]:
    # `question_count` is distinct from the `questions` LIST returned by get_campaign,
    # so spreading these counts never clobbers that list.
    q = conn.execute("SELECT COUNT(*) FROM survey_question WHERE campaign_id = ?", (cid,)).fetchone()[0]
    inv = conn.execute("SELECT COUNT(*) FROM survey_invitation WHERE campaign_id = ?", (cid,)).fetchone()[0]
    resp = conn.execute(
        "SELECT COUNT(DISTINCT employee_token) FROM survey_response WHERE campaign_id = ?",
        (cid,)).fetchone()[0]
    return {"question_count": q, "invited": inv, "responded": resp}


def list_campaigns(conn: sqlite3.Connection, *, actor: Actor,
                   status: str | None = None) -> list[dict[str, Any]]:
    """Campaigns visible to the actor (admin: all; manager: own-division), with counts."""
    rows = conn.execute(
        "SELECT id, title, type, status, cadence, audience_json, trigger_event, anonymous, "
        "min_threshold, opens_at, closes_at, created_ts FROM survey_campaign "
        "ORDER BY id DESC").fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        if not _can_access(actor, r["audience_json"]):
            continue
        if status is not None and r["status"] != status:
            continue
        c = _counts(conn, r["id"])
        out.append({
            "id": r["id"], "title": r["title"], "type": r["type"], "status": r["status"],
            "cadence": r["cadence"], "division": _campaign_division(r["audience_json"]),
            "trigger_event": r["trigger_event"], "anonymous": r["anonymous"],
            "min_threshold": r["min_threshold"], "opens_at": r["opens_at"],
            "closes_at": r["closes_at"], "created_ts": r["created_ts"], **c})
    write_audit(conn, actor_email=actor.email, action="survey_campaigns",
                filters={"status": status}, result_count=len(out))
    return out


def get_campaign(conn: sqlite3.Connection, *, actor: Actor, campaign_id: int) -> dict[str, Any]:
    """One campaign with its frozen questions + counts (scoped + audited)."""
    r = conn.execute("SELECT * FROM survey_campaign WHERE id = ?", (campaign_id,)).fetchone()
    if r is None or not _can_access(actor, r["audience_json"]):
        write_audit(conn, actor_email=actor.email, action="survey_campaign",
                    filters={"campaign_id": campaign_id, "denied": r is not None}, result_count=0)
        return {}
    questions = [
        {"id": q["id"], "position": q["position"], "driver_code": q["driver_code"],
         "text": q["text"], "qtype": q["qtype"], "scale": q["scale"], "required": q["required"]}
        for q in conn.execute(
            "SELECT id, position, driver_code, text, qtype, scale, required FROM survey_question "
            "WHERE campaign_id = ? ORDER BY position", (campaign_id,)).fetchall()]
    try:
        audience = json.loads(r["audience_json"]) if r["audience_json"] else {}
    except (ValueError, TypeError):
        audience = {}
    write_audit(conn, actor_email=actor.email, action="survey_campaign",
                filters={"campaign_id": campaign_id}, result_count=1)
    return {"id": r["id"], "title": r["title"], "type": r["type"], "status": r["status"],
            "cadence": r["cadence"], "audience": audience,
            "division": audience.get("division"), "trigger_event": r["trigger_event"],
            "trigger_offset_days": r["trigger_offset_days"], "anonymous": r["anonymous"],
            "min_threshold": r["min_threshold"], "opens_at": r["opens_at"],
            "closes_at": r["closes_at"], "created_ts": r["created_ts"],
            "questions": questions, **_counts(conn, r["id"])}


def set_campaign_status(conn: sqlite3.Connection, *, actor: Actor, campaign_id: int,
                        status: str) -> dict[str, Any]:
    """Move a campaign through its lifecycle (draft → scheduled → active → closed → archived)."""
    if status not in CAMPAIGN_STATUSES:
        raise ValueError(f"invalid status: {status}")
    r = conn.execute("SELECT audience_json FROM survey_campaign WHERE id = ?", (campaign_id,)).fetchone()
    if r is None or not _can_access(actor, r["audience_json"]):
        write_audit(conn, actor_email=actor.email, action="survey_set_status",
                    filters={"campaign_id": campaign_id, "denied": r is not None}, result_count=0)
        return {}
    conn.execute("UPDATE survey_campaign SET status = ? WHERE id = ?", (status, campaign_id))
    conn.commit()
    write_audit(conn, actor_email=actor.email, action="survey_set_status",
                filters={"campaign_id": campaign_id, "status": status}, result_count=1)
    return {"id": campaign_id, "status": status}


def launch_campaign(conn: sqlite3.Connection, *, actor: Actor, campaign_id: int) -> dict[str, Any]:
    """Distribute a campaign: resolve its audience to tokens, create invitations, set active.

    For lifecycle/trigger campaigns (trigger_event set) launching only opens the campaign —
    recipients enrol per HRIS event via enroll_lifecycle, not as a one-time blast.
    """
    r = conn.execute("SELECT * FROM survey_campaign WHERE id = ?", (campaign_id,)).fetchone()
    if r is None or not _can_access(actor, r["audience_json"]):
        write_audit(conn, actor_email=actor.email, action="survey_launch",
                    filters={"campaign_id": campaign_id, "denied": r is not None}, result_count=0)
        return {}
    ts = _now()
    invited = 0
    if r["trigger_event"] is None:
        try:
            audience = json.loads(r["audience_json"]) if r["audience_json"] else {}
        except (ValueError, TypeError):
            audience = {}
        for token in _resolve_tokens(conn, audience):
            cur = conn.execute(
                "INSERT OR IGNORE INTO survey_invitation (campaign_id, employee_token, status, "
                "invited_ts) VALUES (?,?, 'invited', ?)", (campaign_id, token, ts))
            invited += cur.rowcount
    conn.execute("UPDATE survey_campaign SET status = 'active', opens_at = COALESCE(opens_at, ?) "
                 "WHERE id = ?", (ts, campaign_id))
    conn.commit()
    write_audit(conn, actor_email=actor.email, action="survey_launch",
                filters={"campaign_id": campaign_id, "invited": invited,
                         "trigger": r["trigger_event"]}, result_count=invited)
    return {"id": campaign_id, "status": "active", "invited": invited,
            "trigger_event": r["trigger_event"]}


def enroll_lifecycle(conn: sqlite3.Connection, *, event: str, token: str,
                     actor_email: str = "system:hris") -> dict[str, Any]:
    """HRIS-event trigger: enrol a token into every active campaign whose trigger matches.

    hire → onboarding survey, termination → exit survey. The token must fall inside the
    campaign's (already scope-pinned) audience. Idempotent per (campaign, token).
    """
    if event not in TRIGGER_EVENTS:
        raise ValueError(f"invalid event: {event}")
    ts = _now()
    enrolled: list[int] = []
    camps = conn.execute(
        "SELECT id, audience_json FROM survey_campaign "
        "WHERE trigger_event = ? AND status = 'active'", (event,)).fetchall()
    for c in camps:
        try:
            audience = json.loads(c["audience_json"]) if c["audience_json"] else {}
        except (ValueError, TypeError):
            audience = {}
        if token in _resolve_tokens(conn, audience):
            cur = conn.execute(
                "INSERT OR IGNORE INTO survey_invitation (campaign_id, employee_token, status, "
                "invited_ts) VALUES (?,?, 'invited', ?)", (c["id"], token, ts))
            if cur.rowcount:
                enrolled.append(c["id"])
    conn.commit()
    write_audit(conn, actor_email=actor_email, action="survey_enroll_lifecycle",
                filters={"event": event, "token": token, "campaigns": enrolled},
                result_count=len(enrolled))
    return {"event": event, "token": token, "enrolled_campaigns": enrolled}


# ============================================================================
# Confidential response capture + scoring + write-back to `engagement` (Step 4).
# Responses are token-linked but read ONLY in aggregate above min_threshold; the
# per-respondent signal still feeds the engagement table (the FR model's input),
# exactly as a survey-platform connector would land it.
# ============================================================================
_STOPWORDS = frozenset(
    "the a an and or but to of for in on at it is are be we i my our you your they them this that "
    "with as so if not no do does did have has had can could would should will more most very just "
    "about than then there here what when how who which their his her its also been being too".split())


def _rescale_5(v: float) -> float:
    """AGREE5 / FREQ5 raw 1–5 -> 0–100."""
    return (float(v) - 1.0) / 4.0 * 100.0


def _enps(values: list[float]) -> int:
    """eNPS from 0–10 scores: %promoters (9–10) − %detractors (0–6), in −100..100."""
    n = len(values)
    if n == 0:
        return 0
    proms = sum(1 for v in values if v >= 9)
    detr = sum(1 for v in values if v <= 6)
    return round((proms - detr) / n * 100)


def _themes(texts: list[str], top: int = 6) -> list[str]:
    """Lightweight open-text themes: stopword-filtered word frequency. (AI theme
    extraction + top-vs-bottom-cohort comparison are a later, richer pass.)"""
    counts: Counter[str] = Counter()
    for t in texts:
        for w in re.findall(r"[a-z']{3,}", (t or "").lower()):
            if w not in _STOPWORDS:
                counts[w] += 1
    return [w for w, _ in counts.most_common(top)]


def submit_response(conn: sqlite3.Connection, *, token: str, campaign_id: int,
                    answers: list[dict[str, Any]]) -> dict[str, Any]:
    """Capture one respondent's answers (token-linked, immutable per question).

    The campaign must be active. Answers for questions not on the campaign are ignored;
    re-answering a question is a no-op (responses are immutable). Marks the invitation
    completed. In production the respondent is authenticated by a signed survey link; the
    token here stands in for that identity.
    """
    camp = conn.execute("SELECT status FROM survey_campaign WHERE id = ?", (campaign_id,)).fetchone()
    if camp is None:
        raise ValueError("campaign not found")
    if camp["status"] != "active":
        raise ValueError("campaign is not open for responses")

    valid_q = {r["id"] for r in conn.execute(
        "SELECT id FROM survey_question WHERE campaign_id = ?", (campaign_id,))}
    ts = _now()
    saved = 0
    for a in answers:
        qid = a.get("question_id")
        if qid not in valid_q:
            continue
        cur = conn.execute(
            "INSERT OR IGNORE INTO survey_response (campaign_id, question_id, employee_token, "
            "numeric_value, choice_value, text_value, submitted_ts) VALUES (?,?,?,?,?,?,?)",
            (campaign_id, qid, token, a.get("numeric_value"), a.get("choice_value"),
             a.get("text_value"), ts))
        saved += cur.rowcount
    conn.execute(
        "UPDATE survey_invitation SET status = 'completed', completed_ts = ? "
        "WHERE campaign_id = ? AND employee_token = ?", (ts, campaign_id, token))
    conn.commit()
    write_audit(conn, actor_email=f"respondent:{token}", action="survey_submit_response",
                filters={"campaign_id": campaign_id, "saved": saved}, result_count=saved)
    return {"campaign_id": campaign_id, "saved": saved}


def campaign_results(conn: sqlite3.Connection, *, actor: Actor, campaign_id: int) -> dict[str, Any]:
    """Confidential aggregate results: driver scores (0–100), eNPS, response rate.

    Role-based: a manager only sees their own division's campaigns. Confidentiality:
    if the respondent count is below the campaign's minimum-reporting threshold the whole
    result is suppressed; a single driver is suppressed if it has fewer responses than the
    threshold (small-cell de-anonymization guard, same rule as analytics suppression).
    """
    camp = conn.execute("SELECT * FROM survey_campaign WHERE id = ?", (campaign_id,)).fetchone()
    if camp is None or not _can_access(actor, camp["audience_json"]):
        write_audit(conn, actor_email=actor.email, action="survey_results",
                    filters={"campaign_id": campaign_id, "denied": camp is not None}, result_count=0)
        return {}

    threshold = camp["min_threshold"]
    invited = conn.execute(
        "SELECT COUNT(*) FROM survey_invitation WHERE campaign_id = ?", (campaign_id,)).fetchone()[0]
    respondents = conn.execute(
        "SELECT COUNT(DISTINCT employee_token) FROM survey_response WHERE campaign_id = ?",
        (campaign_id,)).fetchone()[0]
    rate = round(respondents / invited, 3) if invited else None
    base = {"campaign_id": campaign_id, "title": camp["title"], "type": camp["type"],
            "status": camp["status"], "invited": invited, "respondents": respondents,
            "response_rate": rate, "min_threshold": threshold,
            "division": _campaign_division(camp["audience_json"])}

    if respondents < threshold:
        write_audit(conn, actor_email=actor.email, action="survey_results",
                    filters={"campaign_id": campaign_id, "suppressed": True}, result_count=0)
        return {**base, "suppressed": True, "engagement_score": None, "enps_score": None,
                "drivers": [], "themes": []}

    names = {r["code"]: r["name"] for r in conn.execute("SELECT code, name FROM survey_driver")}
    by_driver: dict[str, list[float]] = {}
    enps_vals: list[float] = []
    texts: list[str] = []
    for r in conn.execute(
        "SELECT q.driver_code AS dc, q.scale AS scale, r.numeric_value AS nv, r.text_value AS tv "
        "FROM survey_response r JOIN survey_question q ON q.id = r.question_id "
        "WHERE r.campaign_id = ?", (campaign_id,)):
        if r["scale"] in ("AGREE5", "FREQ5") and r["nv"] is not None:
            by_driver.setdefault(r["dc"], []).append(_rescale_5(r["nv"]))
        elif r["scale"] == "ENPS" and r["nv"] is not None:
            enps_vals.append(r["nv"])
        if r["tv"]:
            texts.append(r["tv"])

    drivers = [{"code": code, "name": names.get(code, code),
                "score": round(sum(v) / len(v), 1), "n": len(v)}
               for code, v in sorted(by_driver.items()) if len(v) >= threshold]
    eng_vals = by_driver.get("engagement", [])
    engagement_score = round(sum(eng_vals) / len(eng_vals), 1) if len(eng_vals) >= threshold else None
    enps_score = _enps(enps_vals) if len(enps_vals) >= threshold else None

    write_audit(conn, actor_email=actor.email, action="survey_results",
                filters={"campaign_id": campaign_id, "respondents": respondents}, result_count=respondents)
    return {**base, "suppressed": False, "engagement_score": engagement_score,
            "enps_score": enps_score, "drivers": drivers, "themes": _themes(texts)}


def close_campaign(conn: sqlite3.Connection, *, actor: Actor, campaign_id: int,
                   as_of_date: str | None = None) -> dict[str, Any]:
    """Close a campaign and WRITE RESULTS INTO the existing `engagement` table.

    Per respondent: their own engagement_score (0–100) and engagement_driver_scores; the
    cohort eNPS, open-text themes and response_rate are stamped on each respondent's snapshot
    — the exact shape a survey-platform connector lands, so the FR model and the Analytics
    Engagement tab consume it unchanged. Confidentiality is enforced on the *reporting* read
    (campaign_results), not here: the per-employee signal is the person's own submission.
    """
    camp = conn.execute("SELECT audience_json FROM survey_campaign WHERE id = ?", (campaign_id,)).fetchone()
    if camp is None or not _can_access(actor, camp["audience_json"]):
        write_audit(conn, actor_email=actor.email, action="survey_close",
                    filters={"campaign_id": campaign_id, "denied": camp is not None}, result_count=0)
        return {}

    as_of = as_of_date or date.today().isoformat()
    invited = conn.execute(
        "SELECT COUNT(*) FROM survey_invitation WHERE campaign_id = ?", (campaign_id,)).fetchone()[0]

    per_tok: dict[str, dict[str, list[float]]] = {}
    cohort_enps: list[float] = []
    cohort_texts: list[str] = []
    for r in conn.execute(
        "SELECT r.employee_token AS tok, q.driver_code AS dc, q.scale AS scale, "
        "r.numeric_value AS nv, r.text_value AS tv FROM survey_response r "
        "JOIN survey_question q ON q.id = r.question_id WHERE r.campaign_id = ?", (campaign_id,)):
        slot = per_tok.setdefault(r["tok"], {})
        if r["scale"] in ("AGREE5", "FREQ5") and r["nv"] is not None:
            slot.setdefault(r["dc"], []).append(_rescale_5(r["nv"]))
        elif r["scale"] == "ENPS" and r["nv"] is not None:
            cohort_enps.append(r["nv"])
        if r["tv"]:
            cohort_texts.append(r["tv"])

    responded = len(per_tok)
    rate = round(responded / invited, 3) if invited else None
    enps_score = _enps(cohort_enps) if cohort_enps else None
    themes_json = json.dumps(_themes(cohort_texts))

    written = 0
    for tok, drivers in per_tok.items():
        driver_scores = {dc: round(sum(v) / len(v), 1) for dc, v in drivers.items()}
        eng = driver_scores.get("engagement")
        conn.execute(
            "INSERT INTO engagement (employee_token, as_of_date, engagement_score, enps_score, "
            "engagement_driver_scores, survey_open_text_themes, survey_response_rate, survey_date) "
            "VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT(employee_token, as_of_date) DO UPDATE SET "
            "engagement_score=excluded.engagement_score, enps_score=excluded.enps_score, "
            "engagement_driver_scores=excluded.engagement_driver_scores, "
            "survey_open_text_themes=excluded.survey_open_text_themes, "
            "survey_response_rate=excluded.survey_response_rate, survey_date=excluded.survey_date",
            (tok, as_of, eng, enps_score, json.dumps(driver_scores), themes_json, rate, as_of))
        written += 1

    conn.execute("UPDATE survey_campaign SET status = 'closed', closes_at = COALESCE(closes_at, ?) "
                 "WHERE id = ?", (_now(), campaign_id))
    conn.commit()
    write_audit(conn, actor_email=actor.email, action="survey_close",
                filters={"campaign_id": campaign_id, "as_of_date": as_of, "written": written},
                result_count=written)
    return {"id": campaign_id, "status": "closed", "as_of_date": as_of,
            "respondents": responded, "engagement_rows_written": written,
            "enps_score": enps_score, "response_rate": rate}
