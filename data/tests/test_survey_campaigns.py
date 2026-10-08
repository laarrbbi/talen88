"""Step 3 (Survey module): campaigns, audience scoping, distribution, HRIS triggers."""
from __future__ import annotations

import sqlite3

import pytest

from data.audit import verify_chain
from data.survey_library import seed_survey_library
from data.surveys import (
    create_campaign, enroll_lifecycle, get_campaign, launch_campaign,
    list_campaigns, resolve_audience, set_campaign_status,
)

from .conftest import T_ADA, T_CY


def _tpl(conn: sqlite3.Connection, key: str) -> int:
    return conn.execute("SELECT id FROM survey_template WHERE key = ?", (key,)).fetchone()[0]


def test_create_campaign_freezes_questions(conn, admin):
    seed_survey_library(conn)
    res = create_campaign(conn, actor=admin, title="Mgr Eff H1", type="manager_effectiveness",
                          template_id=_tpl(conn, "manager_effectiveness"))
    assert res["status"] == "draft" and res["questions"] == 5
    rows = conn.execute(
        "SELECT position, qtype FROM survey_question WHERE campaign_id = ? ORDER BY position",
        (res["id"],)).fetchall()
    assert [r["position"] for r in rows] == [0, 1, 2, 3, 4]
    assert all(r["qtype"] == "likert" for r in rows)


def test_get_campaign_returns_question_list_and_counts(conn, admin):
    seed_survey_library(conn)
    c = create_campaign(conn, actor=admin, title="Mgr Eff", type="manager_effectiveness",
                        template_id=_tpl(conn, "manager_effectiveness"))
    camp = get_campaign(conn, actor=admin, campaign_id=c["id"])
    # the questions LIST is preserved (not clobbered by the count) ...
    assert isinstance(camp["questions"], list) and len(camp["questions"]) == 5
    assert camp["questions"][0]["qtype"] == "likert"
    # ... and the scalar counts come back under their own keys.
    assert camp["question_count"] == 5 and camp["invited"] == 0 and camp["responded"] == 0


def test_create_campaign_rejects_bad_type(conn, admin):
    seed_survey_library(conn)
    with pytest.raises(ValueError):
        create_campaign(conn, actor=admin, title="x", type="not_a_type")


def test_manager_is_pinned_to_own_division(conn, tech_manager):
    seed_survey_library(conn)
    # Manager asks for operations; scope pins it back to technology.
    res = create_campaign(conn, actor=tech_manager, title="Pulse", type="pulse",
                          template_id=_tpl(conn, "quarterly_pulse"),
                          audience={"division": "operations"})
    assert res["division"] == "technology"
    camp = get_campaign(conn, actor=tech_manager, campaign_id=res["id"])
    assert camp["division"] == "technology"


def test_resolve_audience_scoping(conn, admin, tech_manager):
    seed_survey_library(conn)
    assert len(resolve_audience(conn, actor=admin, audience={"division": "technology"})) == 3
    assert len(resolve_audience(conn, actor=admin, audience={"division": "operations"})) == 2
    # A manager requesting operations is still resolved within technology only.
    toks = resolve_audience(conn, actor=tech_manager, audience={"division": "operations"})
    assert len(toks) == 3 and T_CY not in toks


def test_list_and_get_are_scoped(conn, admin, tech_manager):
    seed_survey_library(conn)
    tech = create_campaign(conn, actor=admin, title="Tech census", type="engagement",
                           template_id=_tpl(conn, "annual_census"), audience={"division": "technology"})
    ops = create_campaign(conn, actor=admin, title="Ops census", type="engagement",
                          template_id=_tpl(conn, "annual_census"), audience={"division": "operations"})
    admin_ids = {c["id"] for c in list_campaigns(conn, actor=admin)}
    assert {tech["id"], ops["id"]} <= admin_ids
    mgr_ids = {c["id"] for c in list_campaigns(conn, actor=tech_manager)}
    assert tech["id"] in mgr_ids and ops["id"] not in mgr_ids
    # Direct cross-division get is denied for the manager.
    assert get_campaign(conn, actor=tech_manager, campaign_id=ops["id"]) == {}


def test_launch_creates_invitations_and_is_idempotent(conn, admin):
    seed_survey_library(conn)
    c = create_campaign(conn, actor=admin, title="Tech pulse", type="pulse",
                        template_id=_tpl(conn, "quarterly_pulse"), audience={"division": "technology"})
    first = launch_campaign(conn, actor=admin, campaign_id=c["id"])
    assert first["status"] == "active" and first["invited"] == 3
    # Re-launching does not duplicate invitations.
    second = launch_campaign(conn, actor=admin, campaign_id=c["id"])
    assert second["invited"] == 0
    total = conn.execute("SELECT COUNT(*) FROM survey_invitation WHERE campaign_id = ?",
                         (c["id"],)).fetchone()[0]
    assert total == 3


def test_set_status_transitions_and_validates(conn, admin):
    seed_survey_library(conn)
    c = create_campaign(conn, actor=admin, title="x", type="pulse",
                        template_id=_tpl(conn, "monthly_pulse"))
    assert set_campaign_status(conn, actor=admin, campaign_id=c["id"], status="closed")["status"] == "closed"
    with pytest.raises(ValueError):
        set_campaign_status(conn, actor=admin, campaign_id=c["id"], status="bogus")


def test_lifecycle_hire_enrolment(conn, admin):
    seed_survey_library(conn)
    # Company-wide onboarding campaign, triggered on hire.
    c = create_campaign(conn, actor=admin, title="Onboarding D1", type="lifecycle",
                        template_id=_tpl(conn, "onboarding_day1"),
                        trigger_event="hire", trigger_offset_days=1)
    launched = launch_campaign(conn, actor=admin, campaign_id=c["id"])
    assert launched["invited"] == 0  # trigger campaigns enrol per-event, not as a blast

    enr = enroll_lifecycle(conn, event="hire", token=T_ADA)
    assert c["id"] in enr["enrolled_campaigns"]
    assert conn.execute(
        "SELECT COUNT(*) FROM survey_invitation WHERE campaign_id = ? AND employee_token = ?",
        (c["id"], T_ADA)).fetchone()[0] == 1
    # Idempotent: a second hire event does not double-enrol.
    again = enroll_lifecycle(conn, event="hire", token=T_ADA)
    assert again["enrolled_campaigns"] == []


def test_lifecycle_respects_audience_scope(conn, admin):
    seed_survey_library(conn)
    # Onboarding scoped to operations: a technology hire must NOT be enrolled.
    c = create_campaign(conn, actor=admin, title="Ops onboarding", type="lifecycle",
                        template_id=_tpl(conn, "onboarding_day1"),
                        audience={"division": "operations"}, trigger_event="hire")
    launch_campaign(conn, actor=admin, campaign_id=c["id"])
    enr = enroll_lifecycle(conn, event="hire", token=T_ADA)  # T_ADA is technology
    assert c["id"] not in enr["enrolled_campaigns"]


def test_campaign_ops_audited_and_chain_intact(conn, admin):
    seed_survey_library(conn)
    c = create_campaign(conn, actor=admin, title="x", type="pulse",
                        template_id=_tpl(conn, "monthly_pulse"), audience={"division": "technology"})
    launch_campaign(conn, actor=admin, campaign_id=c["id"])
    enroll_lifecycle(conn, event="termination", token=T_ADA)
    actions = {r[0] for r in conn.execute("SELECT DISTINCT action FROM audit_log")}
    assert {"survey_create_campaign", "survey_launch", "survey_enroll_lifecycle"} <= actions
    assert verify_chain(conn) is True
