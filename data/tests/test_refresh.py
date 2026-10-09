"""Tests for the score-refresh wiring (data layer <- scoring seam).

Uses an injected in-process score backend (the real heuristic) so the persistence
+ trend logic is exercised without a running HTTP server.
"""
from __future__ import annotations

import dataclasses
import sqlite3

import pytest

from data.audit import verify_chain
from data.db import init_db
from data.query import get_employees
from data.refresh import refresh_scores
from model_service.model_loader import load_model

from data import auth


def _local_batch(as_of_date, rows):
    scorer = load_model()
    return [dataclasses.asdict(r) for r in scorer.score(rows)]


@pytest.fixture()
def conn_two_dates():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_db(c)
    c.execute("INSERT INTO divisions (id, name) VALUES (1, 'technology')")
    c.execute("INSERT INTO employees (token, role, level, division, team, manager_token, "
              "location, hire_date, employment_type) VALUES "
              "('emp_a','SWE',2,'technology','A',NULL,'NY','2020-01-01','full_time')")
    # Two snapshots; comp gap worsens, so flight risk should rise -> positive trend.
    c.execute("INSERT INTO feature_snapshots VALUES ('emp_a','2026-01-01',0.05,6,40,0)")
    c.execute("INSERT INTO feature_snapshots VALUES ('emp_a','2026-02-01',0.30,8,42,2)")
    c.commit()
    yield c
    c.close()


def test_refresh_populates_scores_and_reason_codes(conn_two_dates):
    n = refresh_scores(conn_two_dates, score_batch=_local_batch)
    assert n == 2
    scores = conn_two_dates.execute(
        "SELECT as_of_date, flight_risk FROM scores ORDER BY as_of_date").fetchall()
    assert len(scores) == 2
    rc = conn_two_dates.execute("SELECT COUNT(*) FROM reason_codes").fetchone()[0]
    assert rc > 0


def test_refresh_computes_trend_across_snapshots(conn_two_dates):
    refresh_scores(conn_two_dates, score_batch=_local_batch)
    rows = conn_two_dates.execute(
        "SELECT as_of_date, flight_risk, risk_trend FROM scores ORDER BY as_of_date").fetchall()
    assert rows[0]["risk_trend"] == 0  # first period has no prior
    assert rows[1]["risk_trend"] == rows[1]["flight_risk"] - rows[0]["flight_risk"]
    assert rows[1]["risk_trend"] > 0   # worsening comp gap -> rising risk


def test_refresh_is_idempotent(conn_two_dates):
    refresh_scores(conn_two_dates, score_batch=_local_batch)
    refresh_scores(conn_two_dates, score_batch=_local_batch)
    assert conn_two_dates.execute("SELECT COUNT(*) FROM scores").fetchone()[0] == 2


def test_query_seam_sees_scores_after_refresh(conn_two_dates):
    refresh_scores(conn_two_dates, score_batch=_local_batch)
    admin = auth.Actor(email="a@x", name="A", role="admin", division=None)
    rows = get_employees(conn_two_dates, actor=admin)
    assert rows[0]["latest_score"] is not None
    assert rows[0]["reason_codes"]


def test_refresh_audited_and_chain_intact(conn_two_dates):
    refresh_scores(conn_two_dates, score_batch=_local_batch)
    assert conn_two_dates.execute(
        "SELECT COUNT(*) FROM audit_log WHERE action='refresh_scores'").fetchone()[0] == 1
    assert verify_chain(conn_two_dates) is True


def test_refresh_populates_panel_and_capital(conn_two_dates):
    refresh_scores(conn_two_dates, score_batch=_local_batch)
    metrics = {r[0] for r in conn_two_dates.execute(
        "SELECT DISTINCT metric FROM metric_scores").fetchall()}
    # retention_risk + the rest of the panel land in metric_scores.
    assert "retention_risk" in metrics and "skills_depth" in metrics
    cap = conn_two_dates.execute("SELECT COUNT(*) FROM capital_metrics").fetchone()[0]
    assert cap > 0
    # capital dollar figures are flagged as modeled estimates.
    est = conn_two_dates.execute(
        "SELECT is_estimate FROM capital_metrics WHERE metric='cost_to_lose' LIMIT 1"
    ).fetchone()[0]
    assert est == 1


def test_refresh_reason_codes_tagged_by_metric(conn_two_dates):
    refresh_scores(conn_two_dates, score_batch=_local_batch)
    metrics = {r[0] for r in conn_two_dates.execute(
        "SELECT DISTINCT metric FROM reason_codes").fetchall()}
    assert "retention_risk" in metrics and len(metrics) > 1


def test_refresh_panel_idempotent(conn_two_dates):
    refresh_scores(conn_two_dates, score_batch=_local_batch)
    first = conn_two_dates.execute("SELECT COUNT(*) FROM metric_scores").fetchone()[0]
    refresh_scores(conn_two_dates, score_batch=_local_batch)
    assert conn_two_dates.execute("SELECT COUNT(*) FROM metric_scores").fetchone()[0] == first
