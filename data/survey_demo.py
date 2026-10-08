"""Demo survey activity (showcase seed). Gated behind the generator — production
starts with the library only and an empty campaign/response set.

Creates a realistic spread of campaigns across every state — a closed annual census
(which writes results into `engagement`), an active collecting pulse, a draft, and
active onboarding/exit lifecycle campaigns with enrolments — then synthesizes
plausible responses. This is what lights up the Surveys screen and the Analytics
"Engagement" tab. All of it goes through the same audited seam as a live campaign.
"""
from __future__ import annotations

import random
import sqlite3
from typing import Any

from .auth import Actor
from .surveys import (
    close_campaign, create_campaign, enroll_lifecycle, launch_campaign, submit_response,
)

_ADMIN = Actor(email="admin@pulsescore.local", name="Admin", role="admin", division=None)

# Per-driver base mean on the 1–5 AGREE5 scale (so driver scores vary realistically —
# manager/team high, reward/growth lower — rather than everything reading the same).
_DRIVER_BASE: dict[str, float] = {
    "engagement": 3.8, "manager": 4.0, "recognition": 3.3, "growth": 3.2, "autonomy": 4.0,
    "purpose": 3.9, "leadership": 3.4, "communication": 3.5, "wellbeing": 3.4, "belonging": 3.9,
    "team": 4.1, "enablement": 3.6, "reward": 3.0, "goals": 3.8, "change": 3.3,
    "onboarding": 4.0, "mgr_eff": 3.7, "exit": 2.8,
}
_OPEN_PHRASES = [
    "More opportunities for career growth would help.",
    "My manager is supportive and gives helpful feedback.",
    "Compensation could be more competitive with the market.",
    "Workload has been heavy this quarter.",
    "I appreciate the flexibility and remote options.",
    "Recognition for good work is inconsistent.",
    "Communication from leadership could be clearer.",
    "Great team and a collaborative culture.",
    "Career progression feels slow in my area.",
    "Benefits are good but pay lags behind.",
]


def _template_id(conn: sqlite3.Connection, key: str) -> int:
    return conn.execute("SELECT id FROM survey_template WHERE key = ?", (key,)).fetchone()[0]


def _respond_sample(conn: sqlite3.Connection, rng: random.Random, cid: int,
                    candidates: list[str], *, rate: float, exit_bias: bool = False) -> int:
    qs = conn.execute(
        "SELECT id, driver_code, scale FROM survey_question WHERE campaign_id = ? ORDER BY position",
        (cid,)).fetchall()
    n = 0
    for tok in candidates:
        if rng.random() > rate:
            continue
        answers: list[dict[str, Any]] = []
        for q in qs:
            if q["scale"] in ("AGREE5", "FREQ5"):
                base = _DRIVER_BASE.get(q["driver_code"], 3.6) - (1.0 if exit_bias else 0.0)
                val = min(5, max(1, round(rng.gauss(base, 0.9))))
                answers.append({"question_id": q["id"], "numeric_value": val})
            elif q["scale"] == "ENPS":
                pool = [6, 5, 4, 3, 2, 7, 8] if exit_bias else [10, 9, 9, 8, 8, 7, 7, 6, 5, 4]
                answers.append({"question_id": q["id"], "numeric_value": rng.choice(pool)})
            elif q["scale"] == "OPEN":
                answers.append({"question_id": q["id"], "text_value": rng.choice(_OPEN_PHRASES)})
        submit_response(conn, token=tok, campaign_id=cid, answers=answers)
        n += 1
    return n


def seed_demo_survey_activity(conn: sqlite3.Connection, rng: random.Random | None = None,
                              *, close_date: str = "2026-06-01") -> dict[str, Any]:
    """Create the demo campaigns + responses. Assumes the library is already seeded.

    `close_date` is the engagement snapshot the closed census writes onto — the generator
    passes its LATEST monthly snapshot date so the survey refreshes the newest engagement
    reading (an UPSERT onto the existing grid) rather than adding an off-grid snapshot.
    """
    rng = rng or random.Random(7)
    tokens = [r["token"] for r in conn.execute("SELECT token FROM employees ORDER BY token")]

    # 1) Annual census — closed; writes per-respondent results into `engagement`.
    census = create_campaign(conn, actor=_ADMIN, title="Annual Engagement Census 2026",
                             type="engagement", template_id=_template_id(conn, "annual_census"),
                             cadence="annual")
    launch_campaign(conn, actor=_ADMIN, campaign_id=census["id"])
    _respond_sample(conn, rng, census["id"], tokens, rate=0.80)
    close_campaign(conn, actor=_ADMIN, campaign_id=census["id"], as_of_date=close_date)

    # 2) Q2 pulse — active, still collecting.
    pulse = create_campaign(conn, actor=_ADMIN, title="Q2 Engagement Pulse", type="pulse",
                            template_id=_template_id(conn, "quarterly_pulse"), cadence="quarterly")
    launch_campaign(conn, actor=_ADMIN, campaign_id=pulse["id"])
    _respond_sample(conn, rng, pulse["id"], tokens, rate=0.68)

    # 3) Manager effectiveness — draft (not yet sent).
    create_campaign(conn, actor=_ADMIN, title="Manager Effectiveness — H1",
                    type="manager_effectiveness",
                    template_id=_template_id(conn, "manager_effectiveness"), cadence="semiannual")

    # 4) Onboarding — active lifecycle (hire trigger); enrol the most recent hires.
    onb = create_campaign(conn, actor=_ADMIN, title="Onboarding — Day 1", type="lifecycle",
                          template_id=_template_id(conn, "onboarding_day1"),
                          trigger_event="hire", trigger_offset_days=1)
    launch_campaign(conn, actor=_ADMIN, campaign_id=onb["id"])
    recent = [r["token"] for r in conn.execute(
        "SELECT token FROM employees ORDER BY hire_date DESC LIMIT 14")]
    for tok in recent:
        enroll_lifecycle(conn, event="hire", token=tok)
    _respond_sample(conn, rng, onb["id"], recent, rate=0.6)

    # 5) Exit experience — active lifecycle (termination trigger); enrol leavers.
    ex = create_campaign(conn, actor=_ADMIN, title="Exit Experience", type="lifecycle",
                         template_id=_template_id(conn, "exit_survey"), trigger_event="termination")
    launch_campaign(conn, actor=_ADMIN, campaign_id=ex["id"])
    leavers = [r["employee_token"] for r in conn.execute(
        "SELECT employee_token FROM label_attrition")]
    for tok in leavers:
        enroll_lifecycle(conn, event="termination", token=tok)
    _respond_sample(conn, rng, ex["id"], leavers, rate=0.7, exit_bias=True)

    conn.commit()
    return {"campaigns": 5, "census_id": census["id"], "pulse_id": pulse["id"],
            "onboarding_id": onb["id"], "exit_id": ex["id"]}
