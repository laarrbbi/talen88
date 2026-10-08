"""Generator coherence: the synthetic build is structurally sound and stable.

The generator backs every screen and every agent run, so its invariants are a
contract: the headcount/divisions are exact, the reporting tree is a single
connected hierarchy rooted at the CEO (the Company Graph renders from it), every
employee carries the full set of monthly snapshots (so trends are computable), the
login users exist, identities are resolvable, and consent gating is demonstrable
(some tokens withhold monitoring consent, and collaboration metadata exists ONLY
for consenting tokens). This runs against a throwaway DB so it never touches the
dev database.
"""
from __future__ import annotations

import sqlite3

import pytest

from data import generate, identity
from data.auth import Actor
from data.db import get_connection

EXPECTED_TOTAL = 396  # 120 + 90 + 80 + 60 + 45 division headcounts + 1 CEO
EXPECTED_HEADCOUNT = {
    "operations": 120,
    "technology": 90,
    "front office": 80,
    "risk & compliance": 60,
    "corporate": 46,  # 45 + the CEO, who sits in corporate
}


@pytest.fixture(scope="module")
def built_db(tmp_path_factory) -> sqlite3.Connection:
    """Build a full synthetic DB into a throwaway file and hand back a connection."""
    db_file = tmp_path_factory.mktemp("gen") / "coherence.db"
    import os

    prev = os.environ.get("PULSESCORE_DB")
    os.environ["PULSESCORE_DB"] = str(db_file)
    try:
        generate.build(seed=7)
        conn = get_connection()
        yield conn
        conn.close()
    finally:
        if prev is None:
            os.environ.pop("PULSESCORE_DB", None)
        else:
            os.environ["PULSESCORE_DB"] = prev


def test_headcount_and_divisions_exact(built_db):
    total = built_db.execute("SELECT COUNT(*) FROM employees").fetchone()[0]
    assert total == EXPECTED_TOTAL

    divs = {r["name"] for r in built_db.execute("SELECT name FROM divisions")}
    assert divs == set(EXPECTED_HEADCOUNT)

    by_div = dict(built_db.execute(
        "SELECT division, COUNT(*) FROM employees GROUP BY division").fetchall())
    assert by_div == EXPECTED_HEADCOUNT


def test_single_connected_hierarchy_rooted_at_ceo(built_db):
    rows = built_db.execute("SELECT token, manager_token FROM employees").fetchall()
    mgr = {r["token"]: r["manager_token"] for r in rows}

    roots = [t for t, m in mgr.items() if m is None]
    assert len(roots) == 1, "exactly one org root (the CEO)"
    root = roots[0]

    # Every manager_token references a real employee (no dangling edges).
    for token, m in mgr.items():
        if m is not None:
            assert m in mgr, f"{token} reports to unknown manager {m}"

    # Walking up from every node terminates at the root with no cycle.
    for token in mgr:
        seen, cur, hops = set(), token, 0
        while mgr[cur] is not None:
            assert cur not in seen, f"cycle detected at {cur}"
            seen.add(cur)
            cur = mgr[cur]
            hops += 1
            assert hops < 50, "hierarchy unexpectedly deep — likely a cycle"
        assert cur == root


def test_six_monthly_snapshots_per_employee(built_db):
    dates = [r[0] for r in built_db.execute(
        "SELECT DISTINCT as_of_date FROM feature_snapshots ORDER BY as_of_date")]
    assert dates == generate.SNAPSHOT_DATES
    assert len(dates) >= 6

    # Every employee has exactly one row per snapshot date (trend is computable).
    counts = built_db.execute(
        "SELECT employee_token, COUNT(DISTINCT as_of_date) c FROM feature_snapshots "
        "GROUP BY employee_token").fetchall()
    assert len(counts) == EXPECTED_TOTAL
    assert all(r["c"] == len(generate.SNAPSHOT_DATES) for r in counts)


def test_login_users_seeded(built_db):
    emails = {r[0] for r in built_db.execute("SELECT email FROM users")}
    assert "admin@pulsescore.local" in emails
    # One manager per division (slug = first word of the division name).
    for slug in ("operations", "technology", "front", "risk", "corporate"):
        assert f"{slug}.manager@pulsescore.local" in emails


def test_identities_resolvable_for_all_tokens(built_db):
    tokens = [r[0] for r in built_db.execute("SELECT token FROM employees")]
    admin = Actor(email="admin@pulsescore.local", name="Admin", role="admin", division=None)
    names = identity.resolve_names(built_db, tokens, actor=admin)
    assert len(names) == len(tokens)
    assert all(isinstance(v, str) and v for v in names.values())

    # No analytics table leaks the PII: employees is token-keyed, no name/email column.
    cols = {r[1] for r in built_db.execute("PRAGMA table_info(employees)")}
    assert not cols & {"name", "full_name", "email"}


def test_consent_mix_and_gating_demonstrable(built_db):
    consents = [r[0] for r in built_db.execute(
        "SELECT monitoring_consent FROM engagement_prefs")]
    assert consents, "engagement_prefs must be populated (it carries the consent flag)"
    # A realistic mix — both opted-in and opted-out exist, so gating is observable.
    assert any(c == 1 for c in consents)
    assert any(c == 0 for c in consents)

    # Collaboration metadata exists ONLY for tokens that consented to monitoring.
    leak = built_db.execute(
        "SELECT COUNT(*) FROM collaboration_metadata cm "
        "JOIN engagement_prefs ep ON ep.employee_token = cm.employee_token "
        "WHERE ep.monitoring_consent = 0").fetchone()[0]
    assert leak == 0, "collaboration metadata present for a non-consenting token"


def test_some_leavers_labeled_for_training(built_db):
    leavers = built_db.execute(
        "SELECT COUNT(*) FROM label_attrition WHERE attrition = 1").fetchone()[0]
    assert leavers > 0, "expected a subset of labeled leavers for the FR training label"


def test_rebuild_is_deterministic(tmp_path_factory):
    """Same seed -> identical employee tokens + hierarchy (no drift between runs)."""
    import os

    def _tokens(seed: int) -> list[tuple]:
        db_file = tmp_path_factory.mktemp("det") / f"s{seed}.db"
        prev = os.environ.get("PULSESCORE_DB")
        os.environ["PULSESCORE_DB"] = str(db_file)
        try:
            generate.build(seed=seed)
            conn = get_connection()
            rows = conn.execute(
                "SELECT token, manager_token, division, level FROM employees "
                "ORDER BY token").fetchall()
            conn.close()
            return [tuple(r) for r in rows]
        finally:
            if prev is None:
                os.environ.pop("PULSESCORE_DB", None)
            else:
                os.environ["PULSESCORE_DB"] = prev

    assert _tokens(7) == _tokens(7)
