"""Step 1 (Survey module foundation): additive schema + question-library seed.

Verifies the survey overlay loads, is strictly additive (base/canonical tables and
their FK target are intact), enforces the token FK, and that seed_survey_library
lands the full drivers/items/templates set wired to the question library.
"""
from __future__ import annotations

import sqlite3

import pytest

from data import survey_library
from data.survey_library import seed_survey_library

SURVEY_TABLES = [
    "survey_driver", "survey_item", "survey_template", "survey_template_item",
    "survey_campaign", "survey_question", "survey_invitation", "survey_response",
    "action_plan",
]


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_survey_tables_created_by_init(conn):
    have = _tables(conn)
    missing = [t for t in SURVEY_TABLES if t not in have]
    assert not missing, f"missing survey tables: {missing}"


def test_overlay_is_additive(conn):
    # The base + canonical tables (and the engagement write-back target) still exist,
    # and the employee seed is untouched by loading the survey overlay.
    have = _tables(conn)
    for t in ("employees", "engagement", "scores", "employee_core", "audit_log"):
        assert t in have
    assert conn.execute("SELECT COUNT(*) FROM employees").fetchone()[0] > 0


def test_seed_counts_match_library(conn):
    res = seed_survey_library(conn)
    assert res["drivers"] == len(survey_library.DRIVERS) == 19
    assert res["items"] == len(survey_library.ITEMS)
    assert res["templates"] == len(survey_library.TEMPLATES) == 12
    assert res["template_items"] > 0
    # and the rows actually landed
    assert conn.execute("SELECT COUNT(*) FROM survey_driver").fetchone()[0] == 19
    assert conn.execute("SELECT COUNT(*) FROM survey_item").fetchone()[0] == len(survey_library.ITEMS)
    assert conn.execute("SELECT COUNT(*) FROM survey_template").fetchone()[0] == 12


def test_seed_does_not_touch_engagement(conn):
    # The library seed is reference content only — it must never write a response or
    # an engagement row (that happens later, when a campaign closes).
    before = conn.execute("SELECT COUNT(*) FROM engagement").fetchone()[0]
    seed_survey_library(conn)
    after = conn.execute("SELECT COUNT(*) FROM engagement").fetchone()[0]
    assert before == after
    assert conn.execute("SELECT COUNT(*) FROM survey_response").fetchone()[0] == 0


def test_engagement_is_the_single_outcome_driver(conn):
    seed_survey_library(conn)
    outcomes = [r["code"] for r in conn.execute(
        "SELECT code FROM survey_driver WHERE is_outcome = 1")]
    assert outcomes == ["engagement"]


def test_every_template_has_valid_items(conn):
    seed_survey_library(conn)
    rows = conn.execute(
        "SELECT t.key, COUNT(ti.item_id) AS n FROM survey_template t "
        "LEFT JOIN survey_template_item ti ON ti.template_id = t.id GROUP BY t.id"
    ).fetchall()
    assert len(rows) == 12
    for r in rows:
        assert r["n"] >= 2, f"template {r['key']} has too few items"
    # No dangling item references.
    dangling = conn.execute(
        "SELECT COUNT(*) FROM survey_template_item ti "
        "LEFT JOIN survey_item i ON i.id = ti.item_id WHERE i.id IS NULL").fetchone()[0]
    assert dangling == 0


def test_known_templates_carry_expected_scales(conn):
    seed_survey_library(conn)
    # The annual census includes an eNPS-scaled item …
    enps = conn.execute(
        "SELECT COUNT(*) FROM survey_template t "
        "JOIN survey_template_item ti ON ti.template_id = t.id "
        "JOIN survey_item i ON i.id = ti.item_id "
        "WHERE t.key = 'annual_census' AND i.scale = 'ENPS'").fetchone()[0]
    assert enps == 1
    # … and the exit survey carries open-text reason questions.
    opens = conn.execute(
        "SELECT COUNT(*) FROM survey_template t "
        "JOIN survey_template_item ti ON ti.template_id = t.id "
        "JOIN survey_item i ON i.id = ti.item_id "
        "WHERE t.key = 'exit_survey' AND i.scale = 'OPEN'").fetchone()[0]
    assert opens >= 1


def test_token_fk_enforced_on_invitation(conn):
    cid = conn.execute(
        "INSERT INTO survey_campaign (title, type, created_ts) VALUES (?,?,?)",
        ("Pulse", "pulse", "2026-06-15")).lastrowid
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO survey_invitation (campaign_id, employee_token) VALUES (?,?)",
            (cid, "emp_does_not_exist"))
