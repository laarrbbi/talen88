"""Team Pulse seam: the operational "what's happening" digest behind the Dashboard panel.

`query.get_team_pulse` carries the same guarantees as the rest of the seam — a manager
only ever sees their own division, every call is audited, and nothing but a token crosses
the boundary. It adds one hard privacy rule of its own:

  * LEAVE IS NEUTRAL — availability + a return date ONLY. There is no leave-reason column
    in `employee_ops` and none in the payload; a manager sees who is away and when they're
    back, never why. This test asserts the schema gives reasons nowhere to live.

Runs against the shared in-memory fixture DB (conftest), with a few `employee_ops` rows
inserted here so the date-windowed buckets are deterministic.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from data.query import QueryValidationError, get_team_pulse
from .conftest import T_ADA, T_BO, T_CY, T_OPS_HEAD, T_TECH_HEAD

TODAY = date.today()


def _iso(days: int) -> str:
    return (TODAY + timedelta(days=days)).isoformat()


def _seed_ops(conn) -> None:
    """A coherent operational snapshot across the 5 seeded employees."""
    rows = [
        # token, birthday_md, availability, pto_return, on_leave, leave_return,
        # onboarding, role_note, role_date, cred_name, cred_expiry
        (T_TECH_HEAD, TODAY.strftime("%m-%d"), "in_office", None, 0, None,
         "complete", None, None, None, None),                      # birthday today
        (T_ADA, _iso(40)[5:], "out", _iso(3), 0, None,
         "complete", "Promotion pending", _iso(10), "Series 7", _iso(12)),  # PTO + role + cred
        (T_BO, _iso(50)[5:], "remote", None, 1, _iso(60),
         "complete", None, None, None, None),                      # extended leave (neutral)
        (T_OPS_HEAD, _iso(60)[5:], "in_office", None, 0, None,
         "complete", None, None, "Work permit", _iso(200)),        # cred far out (excluded)
        (T_CY, _iso(80)[5:], "remote", None, 0, None,
         "in_progress", None, None, None, None),                   # onboarding
    ]
    for r in rows:
        conn.execute(
            "INSERT INTO employee_ops (employee_token, birthday_md, availability, "
            "pto_return_date, on_extended_leave, leave_return_date, onboarding_status, "
            "role_change_note, role_change_date, credential_name, credential_expiry) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)", r)
    conn.commit()


# ---- scope -------------------------------------------------------------------
def test_admin_sees_whole_company(conn, admin):
    res = get_team_pulse(conn, actor=admin, horizon_days=30)
    assert res["team_size"] == 5
    assert res["scope"] == {"division": None, "manager_token": None}


def test_manager_scoped_to_own_division(conn, tech_manager):
    res = get_team_pulse(conn, actor=tech_manager, horizon_days=30)
    assert res["team_size"] == 3                       # only technology
    assert res["scope"]["division"] == "technology"


def test_manager_cannot_widen_scope(conn, tech_manager):
    """Asking for another division is clamped to the manager's own — never widened."""
    res = get_team_pulse(conn, actor=tech_manager, division="operations", horizon_days=30)
    assert res["scope"]["division"] == "technology" and res["team_size"] == 3


# ---- buckets -----------------------------------------------------------------
def test_birthday_window(conn, admin):
    _seed_ops(conn)
    res = get_team_pulse(conn, actor=admin, horizon_days=30)
    today_b = [b for b in res["birthdays"] if b["in_days"] == 0]
    assert any(b["token"] == T_TECH_HEAD for b in today_b)


def test_pto_role_and_credential_surface(conn, admin):
    _seed_ops(conn)
    res = get_team_pulse(conn, actor=admin, horizon_days=30)
    assert any(p["token"] == T_ADA and p["return_date"] == _iso(3) for p in res["on_pto"])
    assert any(r["token"] == T_ADA and r["note"] == "Promotion pending" for r in res["role_changes"])
    assert any(c["token"] == T_ADA and c["name"] == "Series 7" for c in res["credentials"])
    # A credential expiring 200 days out is beyond the 30-day horizon — excluded.
    assert all(c["token"] != T_OPS_HEAD for c in res["credentials"])


def test_onboarding_surfaces(conn, admin):
    _seed_ops(conn)
    res = get_team_pulse(conn, actor=admin, horizon_days=30)
    assert any(o["token"] == T_CY and o["status"] == "in_progress" for o in res["onboarding"])


# ---- the privacy rule --------------------------------------------------------
def test_leave_is_neutral_return_date_only(conn, admin):
    """Extended leave exposes a return date and nothing else — never a reason."""
    _seed_ops(conn)
    res = get_team_pulse(conn, actor=admin, horizon_days=30)
    leave = [x for x in res["on_leave"] if x["token"] == T_BO]
    assert leave and leave[0]["return_date"] == _iso(60)
    forbidden = {"reason", "leave_reason", "diagnosis", "health", "medical", "note"}
    for item in res["on_leave"]:
        assert forbidden.isdisjoint(item.keys())


def test_schema_has_no_leave_reason_column(conn):
    """Defense in depth: the table itself offers no column to store a reason in."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(employee_ops)").fetchall()}
    for forbidden in ("reason", "leave_reason", "diagnosis", "health", "medical", "condition"):
        assert forbidden not in cols


def test_no_person_names_cross_the_boundary(conn, admin):
    """Every bucket item is token-keyed; no real name/email leaks (credential `name` is
    the credential's name, not a person's)."""
    _seed_ops(conn)
    res = get_team_pulse(conn, actor=admin, horizon_days=30)
    for bucket in ("birthdays", "anniversaries", "on_pto", "on_leave", "onboarding",
                   "role_changes", "credentials"):
        for item in res[bucket]:
            assert item["token"].startswith("emp_")
            assert "full_name" not in item and "email" not in item


# ---- audit + validation ------------------------------------------------------
def test_call_is_audited(conn, admin):
    get_team_pulse(conn, actor=admin, horizon_days=30)
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM audit_log WHERE action = 'get_team_pulse'"
    ).fetchone()
    assert row["n"] >= 1


def test_horizon_out_of_range_rejected(conn, admin):
    with pytest.raises(QueryValidationError):
        get_team_pulse(conn, actor=admin, horizon_days=0)
    with pytest.raises(QueryValidationError):
        get_team_pulse(conn, actor=admin, horizon_days=999)
