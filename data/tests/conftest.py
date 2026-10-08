"""Shared fixtures: an in-memory synthetic DB seeded with scores + identities.

Scores are normally written by the refresh step (model_service); we seed a small
deterministic set here so the data-layer tests stay independent of it. An ephemeral
encryption key is set for the test session so the identity table can be exercised.
"""
from __future__ import annotations

import os
import sqlite3

import pytest
from cryptography.fernet import Fernet

# Set an ephemeral data key BEFORE anything imports/uses crypto.
os.environ.setdefault("PULSESCORE_DATA_KEY", Fernet.generate_key().decode())

from data import auth, identity  # noqa: E402
from data.db import init_db  # noqa: E402

# Deterministic tokens for the seed set (so tests can reference them).
T_TECH_HEAD = "emp_tech00000001"
T_ADA = "emp_tech00000002"
T_BO = "emp_tech00000003"
T_OPS_HEAD = "emp_ops000000004"
T_CY = "emp_ops000000005"


@pytest.fixture()
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    init_db(c)
    _seed(c)
    yield c
    c.close()


def _seed(c: sqlite3.Connection) -> None:
    c.execute("INSERT INTO divisions (id, name) VALUES (1, 'technology'), (2, 'operations')")
    emps = [
        (T_TECH_HEAD, "Head of Eng", 6, "technology", "Tech Leadership", None, "Tech Head"),
        (T_ADA, "SWE", 2, "technology", "Tech Team A", T_TECH_HEAD, "Ada Lovelace"),
        (T_BO, "Senior SWE", 3, "technology", "Tech Team A", T_TECH_HEAD, "Bo Bauer"),
        (T_OPS_HEAD, "Head of Ops", 6, "operations", "Ops Leadership", None, "Ops Head"),
        (T_CY, "Ops Analyst", 1, "operations", "Ops Team A", T_OPS_HEAD, "Cy Costa"),
    ]
    for token, role, level, div, team, mgr, name in emps:
        c.execute(
            "INSERT INTO employees (token, role, level, division, team, manager_token, "
            "location, hire_date, employment_type) VALUES (?,?,?,?,?,?,?,?,?)",
            (token, role, level, div, team, mgr, "New York", "2020-01-01", "full_time"),
        )
        identity.store_identity(c, token, name, f"{name.replace(' ', '.').lower()}@example.com")

    date = "2026-06-01"
    for token, comp_gap in [(T_TECH_HEAD, 0.05), (T_ADA, 0.25), (T_BO, 0.10),
                            (T_OPS_HEAD, 0.30), (T_CY, 0.02)]:
        c.execute(
            "INSERT INTO feature_snapshots (employee_token, as_of_date, comp_gap, "
            "months_since_promotion, tenure_months, manager_changes_12mo) VALUES (?,?,?,?,?,?)",
            (token, date, comp_gap, 12, 36, 1),
        )

    c.execute("INSERT INTO skills (id, name, family) VALUES "
              "(1, 'Python', 'Engineering'), (2, 'SQL', 'Data'), "
              "(3, 'People Leadership', 'Leadership')")
    # 360 attributes (T_BO withholds consent so engagement is gated off downstream).
    attrs = [(T_TECH_HEAD, 4.6, 80, 10, 6, 1), (T_ADA, 3.4, 65, 40, 0, 0),
             (T_BO, 3.8, 70, 30, 0, 0), (T_OPS_HEAD, 4.4, 78, 12, 5, 1),
             (T_CY, 3.0, 55, 50, 0, 0)]
    for token, perf, eng, learn, span, _ in attrs:
        consent = 0 if token == T_BO else 1
        c.execute(
            "INSERT INTO employee_attributes (employee_token, as_of_date, perf_rating, "
            "engagement_pulse, learning_hours_12mo, internal_moves, span_of_control, "
            "consented_signals) VALUES (?,?,?,?,?,?,?,?)",
            (token, date, perf, eng, learn, 0, span, consent),
        )
    for token in (T_TECH_HEAD, T_ADA, T_BO):
        c.execute("INSERT INTO employee_skills (employee_token, skill_id, proficiency) "
                  "VALUES (?,?,?)", (token, 1, 3))
    c.execute("INSERT INTO employee_skills (employee_token, skill_id, proficiency) "
              "VALUES (?,?,?)", (T_TECH_HEAD, 3, 5))
    scores = [(T_TECH_HEAD, 20, 90), (T_ADA, 85, 80), (T_BO, 55, 60),
              (T_OPS_HEAD, 75, 40), (T_CY, 30, 50)]
    for token, fr, vs in scores:
        c.execute("INSERT INTO scores (employee_token, as_of_date, flight_risk, value_score, "
                  "risk_trend) VALUES (?,?,?,?,?)", (token, date, fr, vs, 3))
        c.execute("INSERT INTO reason_codes (employee_token, as_of_date, label, direction, "
                  "weight) VALUES (?,?,?,?,?)", (token, date, "comp_below_band", "increases", 0.4))
    c.commit()


@pytest.fixture()
def admin() -> auth.Actor:
    return auth.Actor(email="admin@x", name="Admin", role="admin", division=None)


@pytest.fixture()
def tech_manager() -> auth.Actor:
    return auth.Actor(email="tm@x", name="Tech Head", role="manager", division="technology")
