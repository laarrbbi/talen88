"""Survey question library + templates (seed content for the Survey module).

Original items written around standard, well-established employee-engagement
constructs (the same drivers Qualtrics / Culture Amp / Peakon / 15Five measure) —
not copied from any vendor instrument. Mirrors Talent88_Survey_Question_Library_v1.

This module is pure data + a seeder. `seed_survey_library(conn)` is idempotent
against a freshly-created (empty) survey schema and is called from generate.build()
so every generated DB carries the library. It inserts only into the survey_driver /
survey_item / survey_template / survey_template_item tables — never anything else.
"""
from __future__ import annotations

import sqlite3
from typing import Any

# Response scales (reference; surfaced to the builder UI later).
#   key, label, ordered options (None for eNPS 0–10 and free text)
SCALES: list[tuple[str, str, list[str] | None]] = [
    ("AGREE5", "5-point agreement",
     ["Strongly disagree", "Disagree", "Neither agree nor disagree", "Agree", "Strongly agree"]),
    ("ENPS", "0–10 likelihood to recommend", None),
    ("FREQ5", "frequency", ["Never", "Rarely", "Sometimes", "Often", "Always"]),
    ("OPEN", "free text", None),
]

# Drivers: (code, name, description, is_outcome). `engagement` is the outcome index;
# everything else is a driver that explains it.
DRIVERS: list[tuple[str, str, str, int]] = [
    ("engagement", "Engagement", "Overall engagement outcome index.", 1),
    ("enps", "eNPS", "Likelihood to recommend as a place to work.", 0),
    ("manager", "Manager Support", "Day-to-day manager relationship, feedback and fairness.", 0),
    ("recognition", "Recognition", "Whether good work is seen and recognized.", 0),
    ("growth", "Growth & Development", "Opportunities to learn, grow and progress.", 0),
    ("autonomy", "Autonomy & Empowerment", "Freedom and trust to do the work.", 0),
    ("purpose", "Purpose & Meaningful Work", "Meaning and line of sight to goals.", 0),
    ("leadership", "Leadership & Direction", "Confidence in senior leaders and direction.", 0),
    ("communication", "Communication & Transparency", "Open, honest information flow; voice.", 0),
    ("wellbeing", "Wellbeing & Workload", "Balance, manageable workload, care for wellbeing.", 0),
    ("belonging", "Belonging & Inclusion", "Belonging, fairness and being oneself (DEI).", 0),
    ("team", "Team & Collaboration", "Collaboration and reliance within the team.", 0),
    ("enablement", "Enablement", "Tools, resources and processes that enable work.", 0),
    ("reward", "Reward & Fairness", "Perceived pay fairness and benefits fit.", 0),
    ("goals", "Goals & Feedback", "Clarity of expectations and useful feedback.", 0),
    ("change", "Change Readiness", "Understanding of and support through change.", 0),
    ("onboarding", "Onboarding", "New-hire ramp: preparation, access, welcome.", 0),
    ("exit", "Exit", "Departure reasons and what could have retained.", 0),
    ("mgr_eff", "Manager Effectiveness", "360-lite rating of a specific manager.", 0),
]

# Items: (driver_code, text, scale, lifecycle_tag). Kept in driver order so a
# template can reference an item by (driver_code, index-within-driver).
ITEMS: list[tuple[str, str, str, str | None]] = [
    # ENGAGEMENT (outcome index)
    ("engagement", "I am proud to work for this organization.", "AGREE5", None),
    ("engagement", "I would recommend this organization as a great place to work.", "AGREE5", None),
    ("engagement", "I rarely think about looking for a job at another organization.", "AGREE5", None),
    ("engagement", "I feel motivated to do my best work here.", "AGREE5", None),
    # eNPS
    ("enps", "How likely are you to recommend this organization as a place to work?", "ENPS", None),
    ("enps", "What is the main reason for your score?", "OPEN", None),
    # MANAGER SUPPORT
    ("manager", "My manager genuinely cares about my wellbeing.", "AGREE5", None),
    ("manager", "My manager gives me useful feedback on my work.", "AGREE5", None),
    ("manager", "My manager supports my growth and development.", "AGREE5", None),
    ("manager", "I trust my manager to act fairly.", "AGREE5", None),
    # RECOGNITION
    ("recognition", "I receive recognition when I do good work.", "AGREE5", None),
    ("recognition", "The right people are recognized for their contributions here.", "AGREE5", None),
    # GROWTH & DEVELOPMENT
    ("growth", "I have good opportunities to learn and grow here.", "AGREE5", None),
    ("growth", "I can see a path to progress my career at this organization.", "AGREE5", None),
    ("growth", "I have access to the development I need to do my job well.", "AGREE5", None),
    # AUTONOMY & EMPOWERMENT
    ("autonomy", "I have the freedom to decide how to do my work.", "AGREE5", None),
    ("autonomy", "I am trusted to make decisions in my role.", "AGREE5", None),
    # PURPOSE & MEANINGFUL WORK
    ("purpose", "My work gives me a sense of meaning and purpose.", "AGREE5", None),
    ("purpose", "I understand how my work contributes to the organization's goals.", "AGREE5", None),
    # LEADERSHIP & DIRECTION
    ("leadership", "I have confidence in the decisions made by senior leaders.", "AGREE5", None),
    ("leadership", "Leaders communicate a clear vision for the future.", "AGREE5", None),
    ("leadership", "Leadership keeps people informed about what's happening.", "AGREE5", None),
    # COMMUNICATION & TRANSPARENCY
    ("communication", "Important information is shared openly and honestly here.", "AGREE5", None),
    ("communication", "I feel comfortable speaking up and sharing my opinions.", "AGREE5", None),
    # WELLBEING & WORKLOAD
    ("wellbeing", "I am able to maintain a healthy balance between work and personal life.", "AGREE5", None),
    ("wellbeing", "My workload is manageable.", "AGREE5", None),
    ("wellbeing", "This organization genuinely cares about employee wellbeing.", "AGREE5", None),
    # BELONGING & INCLUSION (DEI)
    ("belonging", "I feel a sense of belonging at this organization.", "AGREE5", None),
    ("belonging", "People here are treated fairly regardless of their background.", "AGREE5", None),
    ("belonging", "I can be myself at work.", "AGREE5", None),
    # TEAM & COLLABORATION
    ("team", "People on my team collaborate well together.", "AGREE5", None),
    ("team", "I can rely on my colleagues when I need help.", "AGREE5", None),
    # ENABLEMENT
    ("enablement", "I have the tools and resources I need to do my job effectively.", "AGREE5", None),
    ("enablement", "Processes here help rather than hinder me getting work done.", "AGREE5", None),
    # REWARD & FAIRNESS
    ("reward", "I am paid fairly for the work I do.", "AGREE5", None),
    ("reward", "Benefits here meet my needs.", "AGREE5", None),
    # GOALS & FEEDBACK
    ("goals", "I know what is expected of me at work.", "AGREE5", None),
    ("goals", "I receive feedback that helps me improve.", "AGREE5", None),
    # CHANGE READINESS
    ("change", "I understand the reasons behind the changes happening here.", "AGREE5", None),
    ("change", "I feel supported through the changes taking place.", "AGREE5", None),
    # ONBOARDING (lifecycle)
    ("onboarding", "My onboarding prepared me well for my role.", "AGREE5", "onboarding"),
    ("onboarding", "I had the equipment and access I needed on day one.", "AGREE5", "onboarding"),
    ("onboarding", "My team made me feel welcome.", "AGREE5", "onboarding"),
    ("onboarding", "I understand what is expected of me in my first months.", "AGREE5", "onboarding"),
    # EXIT (lifecycle)
    ("exit", "What is the primary reason you decided to leave?", "OPEN", "exit"),
    ("exit", "The organization delivered on what was promised when I joined.", "AGREE5", "exit"),
    ("exit", "I would consider returning to this organization in the future.", "AGREE5", "exit"),
    ("exit", "What could we have done to keep you?", "OPEN", "exit"),
    # MANAGER EFFECTIVENESS / 360-lite (lifecycle)
    ("mgr_eff", "This manager sets clear expectations.", "AGREE5", "mgr_eff"),
    ("mgr_eff", "This manager gives feedback that helps the team improve.", "AGREE5", "mgr_eff"),
    ("mgr_eff", "This manager supports the team's development.", "AGREE5", "mgr_eff"),
    ("mgr_eff", "This manager makes fair and timely decisions.", "AGREE5", "mgr_eff"),
    ("mgr_eff", "This manager creates an environment where people can speak up.", "AGREE5", "mgr_eff"),
]

# Templates: each `items` entry references an item by (driver_code, index-within-driver).
TEMPLATES: list[dict[str, Any]] = [
    {"key": "annual_census", "title": "Annual Engagement Census", "type": "engagement",
     "cadence": "annual", "description": "Full census: engagement index + eNPS + one item from every driver.",
     "items": [("engagement", 0), ("engagement", 1), ("engagement", 2), ("engagement", 3),
               ("enps", 0), ("enps", 1), ("manager", 0), ("recognition", 0), ("growth", 0),
               ("autonomy", 0), ("purpose", 0), ("leadership", 0), ("communication", 0),
               ("wellbeing", 0), ("belonging", 0), ("team", 0), ("enablement", 0),
               ("reward", 0), ("goals", 0)]},
    {"key": "quarterly_pulse", "title": "Quarterly Pulse", "type": "pulse",
     "cadence": "quarterly", "description": "Short recurring pulse: engagement index + eNPS + rotating drivers.",
     "items": [("engagement", 0), ("engagement", 1), ("enps", 0),
               ("manager", 0), ("growth", 0), ("wellbeing", 0), ("recognition", 0)]},
    {"key": "monthly_pulse", "title": "Monthly Mini-Pulse", "type": "pulse",
     "cadence": "monthly", "description": "Minimal monthly check: one engagement item + eNPS + one driver.",
     "items": [("engagement", 0), ("enps", 0), ("manager", 0)]},
    {"key": "onboarding_day1", "title": "Onboarding — Day 1", "type": "lifecycle",
     "cadence": "triggered", "description": "Triggered at hire +1 day: equipment/access and welcome.",
     "items": [("onboarding", 1), ("onboarding", 2)]},
    {"key": "onboarding_30_90", "title": "Onboarding — 30/90 Day", "type": "lifecycle",
     "cadence": "triggered", "description": "Triggered at hire +30/+90 days: onboarding + engagement + manager.",
     "items": [("onboarding", 0), ("onboarding", 1), ("onboarding", 2), ("onboarding", 3),
               ("engagement", 0), ("engagement", 1), ("manager", 0), ("manager", 1)]},
    {"key": "exit_survey", "title": "Exit Survey", "type": "lifecycle",
     "cadence": "triggered", "description": "Triggered at termination: exit reasons + reward + manager + growth.",
     "items": [("exit", 0), ("exit", 1), ("exit", 2), ("exit", 3),
               ("reward", 0), ("manager", 0), ("growth", 0)]},
    {"key": "enps_only", "title": "eNPS Only", "type": "enps",
     "cadence": "monthly", "description": "eNPS plus an open follow-up.",
     "items": [("enps", 0), ("enps", 1)]},
    {"key": "manager_effectiveness", "title": "Manager Effectiveness", "type": "manager_effectiveness",
     "cadence": "semiannual", "description": "360-lite rating of a specific manager.",
     "items": [("mgr_eff", 0), ("mgr_eff", 1), ("mgr_eff", 2), ("mgr_eff", 3), ("mgr_eff", 4)]},
    {"key": "dei_belonging", "title": "DEI / Belonging", "type": "dei",
     "cadence": "annual", "description": "Belonging + speaking up + pay fairness.",
     "items": [("belonging", 0), ("belonging", 1), ("belonging", 2),
               ("communication", 1), ("reward", 0)]},
    {"key": "wellbeing_check", "title": "Wellbeing Check", "type": "wellbeing",
     "cadence": "quarterly", "description": "Balance, workload and care for wellbeing.",
     "items": [("wellbeing", 0), ("wellbeing", 1), ("wellbeing", 2), ("manager", 0)]},
    {"key": "change_pulse", "title": "Change Pulse", "type": "change",
     "cadence": "event", "description": "Event-driven: change readiness + leadership informing + open comms.",
     "items": [("change", 0), ("change", 1), ("leadership", 2), ("communication", 0)]},
    {"key": "stay_interview", "title": "Stay Interview (pulse)", "type": "pulse",
     "cadence": "targeted", "description": "Targeted at at-risk staff: engagement + growth + manager + reward + purpose.",
     "items": [("engagement", 0), ("growth", 0), ("manager", 0), ("reward", 0), ("purpose", 0)]},
]


def seed_survey_library(conn: sqlite3.Connection) -> dict[str, int]:
    """Insert the drivers, items and templates into an empty survey schema.

    Idempotent against a fresh DB (init_db drops the survey tables first). Returns
    a small count dict for callers/tests. Inserts ONLY into survey_driver /
    survey_item / survey_template / survey_template_item.
    """
    for code, name, desc, outcome in DRIVERS:
        conn.execute(
            "INSERT INTO survey_driver (code, name, description, is_outcome) VALUES (?,?,?,?)",
            (code, name, desc, outcome))

    # Insert items in order, tracking each item's id by (driver_code, index-within-driver)
    # so templates can reference items positionally without fragile text matching.
    item_id: dict[tuple[str, int], int] = {}
    per_driver: dict[str, int] = {}
    for driver_code, text, scale, lifecycle in ITEMS:
        idx = per_driver.get(driver_code, 0)
        cur = conn.execute(
            "INSERT INTO survey_item (driver_code, text, scale, lifecycle_tag) VALUES (?,?,?,?)",
            (driver_code, text, scale, lifecycle))
        item_id[(driver_code, idx)] = int(cur.lastrowid)
        per_driver[driver_code] = idx + 1

    n_links = 0
    for t in TEMPLATES:
        cur = conn.execute(
            "INSERT INTO survey_template (key, title, type, cadence, description) VALUES (?,?,?,?,?)",
            (t["key"], t["title"], t["type"], t["cadence"], t.get("description")))
        tid = int(cur.lastrowid)
        for pos, (dc, idx) in enumerate(t["items"]):
            conn.execute(
                "INSERT INTO survey_template_item (template_id, item_id, position) VALUES (?,?,?)",
                (tid, item_id[(dc, idx)], pos))
            n_links += 1

    conn.commit()
    return {"drivers": len(DRIVERS), "items": len(ITEMS),
            "templates": len(TEMPLATES), "template_items": n_links}
