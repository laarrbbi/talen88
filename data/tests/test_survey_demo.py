"""Demo survey activity seeder (showcase) — structural smoke test."""
from __future__ import annotations

import json
import random

from data.survey_demo import seed_demo_survey_activity
from data.survey_library import seed_survey_library


def test_demo_seed_creates_campaign_spread_and_writes_engagement(conn):
    seed_survey_library(conn)
    res = seed_demo_survey_activity(conn, random.Random(1))
    assert res["campaigns"] == 5

    statuses = {r["title"]: r["status"]
                for r in conn.execute("SELECT title, status FROM survey_campaign")}
    assert statuses["Annual Engagement Census 2026"] == "closed"
    assert statuses["Q2 Engagement Pulse"] == "active"
    assert statuses["Manager Effectiveness — H1"] == "draft"
    assert statuses["Onboarding — Day 1"] == "active"

    # The closed census wrote per-respondent results into the engagement table, onto the
    # default latest-snapshot date (2026-06-01).
    n = conn.execute("SELECT COUNT(*) FROM engagement WHERE as_of_date = '2026-06-01'").fetchone()[0]
    assert n >= 1
    row = conn.execute(
        "SELECT engagement_driver_scores, survey_response_rate FROM engagement "
        "WHERE as_of_date = '2026-06-01' LIMIT 1").fetchone()
    assert json.loads(row["engagement_driver_scores"])  # non-empty driver map
    assert row["survey_response_rate"] is not None

    # The active pulse collected responses without being closed.
    pulse_responses = conn.execute(
        "SELECT COUNT(*) FROM survey_response WHERE campaign_id = ?", (res["pulse_id"],)).fetchone()[0]
    assert pulse_responses >= 1
