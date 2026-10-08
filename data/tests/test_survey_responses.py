"""Step 4 (Survey module): confidential response capture, threshold, write-back."""
from __future__ import annotations

import json
import sqlite3

import pytest

from data.audit import verify_chain
from data.survey_library import seed_survey_library
from data.surveys import (
    campaign_results, close_campaign, create_campaign, launch_campaign, submit_response,
)

from .conftest import T_ADA, T_BO, T_CY, T_OPS_HEAD, T_TECH_HEAD

ALL_TOKENS = [T_TECH_HEAD, T_ADA, T_BO, T_OPS_HEAD, T_CY]


def _tpl(conn: sqlite3.Connection, key: str) -> int:
    return conn.execute("SELECT id FROM survey_template WHERE key = ?", (key,)).fetchone()[0]


def _questions(conn: sqlite3.Connection, cid: int):
    return conn.execute(
        "SELECT id, scale FROM survey_question WHERE campaign_id = ? ORDER BY position",
        (cid,)).fetchall()


def _answer_all(conn, cid, token, *, likert=5, enps=9):
    answers = []
    for q in _questions(conn, cid):
        if q["scale"] == "ENPS":
            answers.append({"question_id": q["id"], "numeric_value": enps})
        elif q["scale"] == "OPEN":
            answers.append({"question_id": q["id"], "text_value": "great manager and growth"})
        else:
            answers.append({"question_id": q["id"], "numeric_value": likert})
    return submit_response(conn, token=token, campaign_id=cid, answers=answers)


def _company_campaign(conn, admin, key="monthly_pulse"):
    c = create_campaign(conn, actor=admin, title="Pulse", type="pulse", template_id=_tpl(conn, key))
    launch_campaign(conn, actor=admin, campaign_id=c["id"])
    return c["id"]


def test_submit_is_immutable_and_marks_completed(conn, admin):
    seed_survey_library(conn)
    cid = _company_campaign(conn, admin)
    n = len(_questions(conn, cid))
    first = _answer_all(conn, cid, T_ADA)
    assert first["saved"] == n
    # re-answering is a no-op (responses immutable)
    assert _answer_all(conn, cid, T_ADA)["saved"] == 0
    inv = conn.execute(
        "SELECT status FROM survey_invitation WHERE campaign_id = ? AND employee_token = ?",
        (cid, T_ADA)).fetchone()["status"]
    assert inv == "completed"


def test_submit_requires_active_campaign(conn, admin):
    seed_survey_library(conn)
    c = create_campaign(conn, actor=admin, title="Draft", type="pulse",
                        template_id=_tpl(conn, "monthly_pulse"))  # not launched -> draft
    with pytest.raises(ValueError):
        submit_response(conn, token=T_ADA, campaign_id=c["id"], answers=[])


def test_results_suppressed_below_threshold(conn, admin):
    seed_survey_library(conn)
    cid = _company_campaign(conn, admin)  # min_threshold default 5, invites all 5
    for tok in ALL_TOKENS[:4]:            # only 4 respond
        _answer_all(conn, cid, tok)
    res = campaign_results(conn, actor=admin, campaign_id=cid)
    assert res["respondents"] == 4 and res["suppressed"] is True
    assert res["engagement_score"] is None and res["drivers"] == []
    # the 5th response clears the threshold
    _answer_all(conn, cid, ALL_TOKENS[4])
    res2 = campaign_results(conn, actor=admin, campaign_id=cid)
    assert res2["respondents"] == 5 and res2["suppressed"] is False


def test_results_scores_and_enps(conn, admin):
    seed_survey_library(conn)
    cid = _company_campaign(conn, admin)
    for tok in ALL_TOKENS:
        _answer_all(conn, cid, tok, likert=5, enps=9)
    res = campaign_results(conn, actor=admin, campaign_id=cid)
    assert res["engagement_score"] == 100.0      # all "strongly agree" -> 100
    assert res["enps_score"] == 100              # all promoters
    drivers = {d["code"]: d for d in res["drivers"]}
    assert drivers["manager"]["score"] == 100.0 and drivers["manager"]["n"] == 5


def test_results_rbac_cross_division_denied(conn, admin, tech_manager):
    seed_survey_library(conn)
    c = create_campaign(conn, actor=admin, title="Ops", type="pulse",
                        template_id=_tpl(conn, "monthly_pulse"), audience={"division": "operations"})
    launch_campaign(conn, actor=admin, campaign_id=c["id"])
    assert campaign_results(conn, actor=tech_manager, campaign_id=c["id"]) == {}


def test_close_writes_back_into_engagement(conn, admin):
    seed_survey_library(conn)
    cid = _company_campaign(conn, admin)
    for tok in ALL_TOKENS:
        _answer_all(conn, cid, tok, likert=5, enps=9)
    out = close_campaign(conn, actor=admin, campaign_id=cid, as_of_date="2026-06-15")
    assert out["status"] == "closed" and out["engagement_rows_written"] == 5
    assert out["enps_score"] == 100 and out["response_rate"] == 1.0
    row = conn.execute(
        "SELECT engagement_score, enps_score, engagement_driver_scores, survey_response_rate "
        "FROM engagement WHERE employee_token = ? AND as_of_date = ?",
        (T_ADA, "2026-06-15")).fetchone()
    assert row["engagement_score"] == 100.0 and row["enps_score"] == 100
    assert row["survey_response_rate"] == 1.0
    ds = json.loads(row["engagement_driver_scores"])
    assert ds["engagement"] == 100.0 and ds["manager"] == 100.0
    # campaign is now closed
    assert conn.execute("SELECT status FROM survey_campaign WHERE id = ?", (cid,)).fetchone()[0] == "closed"


def test_response_capture_audited_and_chain_intact(conn, admin):
    seed_survey_library(conn)
    cid = _company_campaign(conn, admin)
    for tok in ALL_TOKENS:
        _answer_all(conn, cid, tok)
    campaign_results(conn, actor=admin, campaign_id=cid)
    close_campaign(conn, actor=admin, campaign_id=cid)
    actions = {r[0] for r in conn.execute("SELECT DISTINCT action FROM audit_log")}
    assert {"survey_submit_response", "survey_results", "survey_close"} <= actions
    assert verify_chain(conn) is True
