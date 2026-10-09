"""Database connection factory.

DB-portability seam. The MVP uses SQLite; everything else in the data layer takes
a connection object and uses parameterized SQL, so swapping to Postgres means
changing only this module (e.g. return a psycopg connection and adapt `?` ->
`%s` placeholders). No query logic lives here.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

# Default on-disk location for the synthetic SQLite database.
DEFAULT_DB_PATH = Path(__file__).resolve().parent / "pulsescore.db"
SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"
# Additive canonical overlay (Talent88 schema v2). Applied AFTER the base schema; its
# tables reference employees(token) and never modify any base table or seam.
SCHEMA_CANONICAL_PATH = Path(__file__).resolve().parent / "schema_canonical.sql"
# Additive operational overlay (Team Pulse). Applied AFTER the canonical overlay; its
# single table references employees(token) and backs only the Dashboard pulse panel.
SCHEMA_OPS_PATH = Path(__file__).resolve().parent / "schema_ops.sql"
# Additive survey overlay (employee listening). Applied LAST; its tables reference
# employees(token) and each other, and write results into the existing `engagement`
# table. Never modifies a base/canonical/ops table.
SCHEMA_SURVEYS_PATH = Path(__file__).resolve().parent / "schema_surveys.sql"

# Survey tables to drop FIRST on reset (children before parents; all FK -> employees
# and/or another survey_* table, so they must drop before employees/divisions).
_SURVEY_TABLES = (
    "action_plan",
    "survey_response",
    "survey_invitation",
    "survey_question",
    "survey_campaign",
    "survey_template_item",
    "survey_template",
    "survey_item",
    "survey_driver",
)

# Canonical tables to drop FIRST on reset (all FK -> employees, so before the base
# drop list which removes employees/divisions). Order is within the canonical set.
_CANONICAL_TABLES = (
    "engagement_prefs",
    "interventions",
    "features_engineered",
    "label_attrition",
    "lifecycle",
    "external_market",
    "cv_trajectory",
    "collaboration_metadata",
    "manager_team",
    "engagement",
    "skills_credentials",
    "performance_division_signals",
    "performance",
    "career_mobility",
    "compensation",
    "employee_core",
)


def db_path() -> Path:
    """Resolve the active DB path (env override -> default)."""
    return Path(os.environ.get("PULSESCORE_DB", str(DEFAULT_DB_PATH)))


def get_connection(path: str | os.PathLike | None = None) -> sqlite3.Connection:
    """Open a connection with sane defaults (Row access, FK enforcement)."""
    target = Path(path) if path is not None else db_path()
    conn = sqlite3.connect(str(target))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Create all tables from schema.sql (idempotent reset: drops first)."""
    existing = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    for table in (
        *_SURVEY_TABLES,
        "employee_ops",
        *_CANONICAL_TABLES,
        "audit_log",
        "notifications",
        "notification_prefs",
        "reason_codes",
        "capital_metrics",
        "metric_scores",
        "scores",
        "feature_snapshots",
        "employee_skills",
        "skills",
        "employee_attributes",
        "users",
        "identities",
        "employees",
        "divisions",
    ):
        if table in existing:
            conn.execute(f"DROP TABLE {table}")
    conn.executescript(SCHEMA_PATH.read_text())
    conn.executescript(SCHEMA_CANONICAL_PATH.read_text())
    conn.executescript(SCHEMA_OPS_PATH.read_text())
    conn.executescript(SCHEMA_SURVEYS_PATH.read_text())
    conn.commit()
