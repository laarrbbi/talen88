"""Manual / CV-assisted employee creation (Document Import write path).

Proves create_employee lands a COMPLETE real record across identity + base + canonical +
compensation, that the new person shows up in the same reads the rest of the app uses
(get_employees, resolve_names, the 360), that divisions are data-driven (custom names work
and register), and that the guards hold: admin-only, duplicate-email, bad vocab, valid
manager, required fields.
"""
from __future__ import annotations

import json

import pytest

from data import identity, manual_ingest, query
from data.manual_ingest import ManualIngestError
from data.tests.conftest import T_ADA, T_TECH_HEAD


def _payload(**overrides):
    base = {
        "full_name": "Grace Hopper",
        "email": "grace.hopper@example.com",
        "role": "Compiler Engineer",
        "title": "Principal Engineer",
        "level": "VP",                      # grade label -> normalized to 4
        "division": "Growth Engineering",   # custom (outside the seed five)
        "team": "Compilers",
        "location": "New York",
        "employment_type": "Full Time",     # alias -> full_time
        "status": "Active",                 # alias -> active
        "hire_date": "03/15/2021",          # US format -> ISO
        "base_salary": 185000,
        "currency": "usd",                  # case-folded -> USD
    }
    base.update(overrides)
    return base


# --- the happy path lands every table -----------------------------------------------

def test_create_lands_all_four_tables(conn, admin):
    out = manual_ingest.create_employee(conn, actor=admin, payload=_payload())
    tok = out["token"]
    assert tok.startswith("emp_")

    base = conn.execute("SELECT * FROM employees WHERE token = ?", (tok,)).fetchone()
    assert base["role"] == "Compiler Engineer" and base["level"] == 4
    assert base["division"] == "Growth Engineering"
    assert base["hire_date"] == "2021-03-15" and base["employment_type"] == "full_time"

    core = conn.execute("SELECT * FROM employee_core WHERE employee_token = ?", (tok,)).fetchone()
    assert core["title"] == "Principal Engineer" and core["status"] == "active"
    assert core["division"] == "Growth Engineering"

    comp = conn.execute("SELECT * FROM compensation WHERE employee_token = ?", (tok,)).fetchone()
    assert comp["base_salary"] == 185000.0 and comp["currency"] == "USD"

    ident = conn.execute("SELECT * FROM identities WHERE token = ?", (tok,)).fetchone()
    assert ident is not None  # PII stored (encrypted)


def test_name_resolves_through_the_identity_boundary(conn, admin):
    out = manual_ingest.create_employee(conn, actor=admin, payload=_payload())
    names = identity.resolve_names(conn, [out["token"]], actor=admin)
    assert names[out["token"]] == "Grace Hopper"


def test_created_employee_appears_in_get_employees(conn, admin):
    out = manual_ingest.create_employee(conn, actor=admin, payload=_payload())
    tokens = {r["token"] for r in query.get_employees(conn, actor=admin)}
    assert out["token"] in tokens


def test_custom_division_is_registered(conn, admin):
    manual_ingest.create_employee(conn, actor=admin, payload=_payload(division="Sales EMEA"))
    names = {d["name"] for d in manual_ingest.list_divisions(conn)}
    assert "Sales EMEA" in names


def test_defaults_team_and_title_when_omitted(conn, admin):
    out = manual_ingest.create_employee(
        conn, actor=admin, payload=_payload(title=None, team=None, division="Ops X"))
    core = conn.execute("SELECT title, team FROM employee_core WHERE employee_token = ?",
                        (out["token"],)).fetchone()
    assert core["title"] == out["role"]      # title defaults to role
    assert core["team"] == "Ops X"           # team defaults to division


# --- optional groups (work history / experience · performance & bonus) --------------

def test_optional_groups_land_in_their_tables(conn, admin):
    out = manual_ingest.create_employee(conn, actor=admin, payload=_payload(
        bonus=20000, equity="2,000 RSUs vesting 2027",
        years_of_experience=12, prior_employer_count=3, total_working_years=12,
        performance_rating=4.3, goal_attainment=0.9,
    ))
    tok = out["token"]

    comp = conn.execute("SELECT bonus_history, equity_deferred FROM compensation WHERE employee_token=?",
                        (tok,)).fetchone()
    assert json.loads(comp["bonus_history"])[0]["amount"] == 20000
    assert "RSUs" in json.loads(comp["equity_deferred"])["note"]

    cvt = conn.execute("SELECT years_of_experience, prior_employer_count FROM cv_trajectory "
                       "WHERE employee_token=?", (tok,)).fetchone()
    assert cvt["years_of_experience"] == 12 and cvt["prior_employer_count"] == 3

    cm = conn.execute("SELECT total_working_years, tenure FROM career_mobility WHERE employee_token=?",
                      (tok,)).fetchone()
    assert cm["total_working_years"] == 12 and cm["tenure"] is not None

    perf = conn.execute("SELECT review_ratings, goal_attainment FROM performance WHERE employee_token=?",
                        (tok,)).fetchone()
    assert json.loads(perf["review_ratings"])[0]["score"] == 4.3
    assert perf["goal_attainment"] == 0.9


def test_no_optional_groups_writes_no_extra_rows(conn, admin):
    # The whole point: a profile saves with only the required core — extras are skippable.
    out = manual_ingest.create_employee(conn, actor=admin, payload=_payload())
    tok = out["token"]
    for table in ("cv_trajectory", "career_mobility", "performance"):
        n = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE employee_token=?", (tok,)).fetchone()[0]
        assert n == 0, f"{table} should have no row when nothing optional was supplied"
    comp = conn.execute("SELECT bonus_history, equity_deferred FROM compensation WHERE employee_token=?",
                        (tok,)).fetchone()
    assert comp["bonus_history"] is None and comp["equity_deferred"] is None


def test_bad_optional_value_rejected(conn, admin):
    with pytest.raises(ManualIngestError):
        manual_ingest.create_employee(conn, actor=admin, payload=_payload(performance_rating=9))


# --- guards -------------------------------------------------------------------------

def test_only_admin_may_create(conn, tech_manager):
    with pytest.raises(PermissionError):
        manual_ingest.create_employee(conn, actor=tech_manager, payload=_payload())


def test_duplicate_email_is_rejected(conn, admin):
    manual_ingest.create_employee(conn, actor=admin, payload=_payload())
    with pytest.raises(ManualIngestError):
        manual_ingest.create_employee(conn, actor=admin, payload=_payload(full_name="Imposter"))


def test_bad_employment_type_rejected(conn, admin):
    with pytest.raises(ManualIngestError):
        manual_ingest.create_employee(conn, actor=admin, payload=_payload(employment_type="intern"))


def test_unsupported_currency_rejected(conn, admin):
    with pytest.raises(ManualIngestError):
        manual_ingest.create_employee(conn, actor=admin, payload=_payload(currency="XYZ"))


def test_missing_required_field_rejected(conn, admin):
    with pytest.raises(ManualIngestError):
        manual_ingest.create_employee(conn, actor=admin, payload=_payload(role=""))


def test_non_positive_salary_rejected(conn, admin):
    with pytest.raises(ManualIngestError):
        manual_ingest.create_employee(conn, actor=admin, payload=_payload(base_salary=0))


def test_unknown_manager_token_rejected(conn, admin):
    with pytest.raises(ManualIngestError):
        manual_ingest.create_employee(conn, actor=admin, payload=_payload(manager_token="emp_nope"))


def test_valid_manager_token_accepted(conn, admin):
    out = manual_ingest.create_employee(
        conn, actor=admin, payload=_payload(manager_token=T_TECH_HEAD))
    base = conn.execute("SELECT manager_token FROM employees WHERE token = ?",
                        (out["token"],)).fetchone()
    assert base["manager_token"] == T_TECH_HEAD


# --- helpers ------------------------------------------------------------------------

def test_find_token_by_email_matches_existing_seed(conn):
    # The seed stores ada.lovelace@example.com for T_ADA.
    assert manual_ingest.find_token_by_email(conn, "ADA.LOVELACE@example.com") == T_ADA
    assert manual_ingest.find_token_by_email(conn, "nobody@nowhere.com") is None


def test_ensure_dynamic_divisions_is_noop_on_relaxed_schema(conn, admin):
    # The test DB is built from the already-relaxed schema, so there is no CHECK to drop.
    assert manual_ingest.ensure_dynamic_divisions(conn) is False
    # And a custom division still inserts fine afterward.
    out = manual_ingest.create_employee(
        conn, actor=admin, payload=_payload(division="Brand New Division"))
    assert out["division"] == "Brand New Division"


def test_list_divisions_reports_headcount(conn, admin):
    before = {d["name"]: d["headcount"] for d in manual_ingest.list_divisions(conn)}
    assert before.get("technology", 0) >= 1  # seeded employees exist
    manual_ingest.create_employee(conn, actor=admin, payload=_payload(division="technology"))
    after = {d["name"]: d["headcount"] for d in manual_ingest.list_divisions(conn)}
    assert after["technology"] == before["technology"] + 1


# --- full field set: every optional group lands in its canonical table ---------------

def test_create_lands_all_optional_groups(conn, admin):
    out = manual_ingest.create_employee(conn, actor=admin, payload=_payload(
        # demographics (bias-audit only)
        birth_date="1990-07-15", gender="female", marital_status="single",
        # education & skills
        education_level="master", education_field="technical_degree",
        education_institution="MIT", skills=["Python", "SQL"],
        certifications=["CFA", "PMP"], licenses=["Series 7"],
        languages=["English", "French"], hobbies=["Chess", "Cycling"],
        # compensation extras
        bonus=20000, equity="1000 RSUs", pay_band="P4", last_raise_date="2025-01-01",
        # work history
        years_of_experience=12, prior_employer_count=3, total_working_years=12,
        time_since_last_promotion=18,
        # performance (goal_attainment is a 0-1 fraction)
        performance_rating=4.2, goal_attainment=0.95,
        # lifecycle / compliance
        onboarding_status="in_progress", credential_name="Work permit",
        credential_expiry="2026-12-31", distance_from_home=12.5,
    ))
    tok = out["token"]

    core = conn.execute("SELECT gender, birth_year_band, marital_status FROM employee_core "
                        "WHERE employee_token=?", (tok,)).fetchone()
    assert core["gender"] == "female"
    assert core["birth_year_band"] == "1990-1999"   # derived from the birth date
    assert core["marital_status"] == "single"

    sc = conn.execute("SELECT * FROM skills_credentials WHERE employee_token=?", (tok,)).fetchone()
    assert sc["education_level"] == "master" and sc["education_institution"] == "MIT"
    assert json.loads(sc["skills"]) == ["Python", "SQL"]
    assert json.loads(sc["languages"]) == ["English", "French"]
    assert json.loads(sc["hobbies"]) == ["Chess", "Cycling"]
    assert json.loads(sc["certifications"]) == ["CFA", "PMP"]
    assert json.loads(sc["licenses"]) == [{"name": "Series 7"}]

    ops = conn.execute("SELECT birthday_md, credential_name, onboarding_status FROM employee_ops "
                       "WHERE employee_token=?", (tok,)).fetchone()
    assert ops["birthday_md"] == "07-15" and ops["credential_name"] == "Work permit"
    assert ops["onboarding_status"] == "in_progress"

    comp = conn.execute("SELECT bonus_history, pay_band, last_raise_date FROM compensation "
                        "WHERE employee_token=?", (tok,)).fetchone()
    assert comp["pay_band"] == "P4" and comp["last_raise_date"] == "2025-01-01"
    assert json.loads(comp["bonus_history"])[0]["amount"] == 20000

    cm = conn.execute("SELECT time_since_last_promotion FROM career_mobility "
                      "WHERE employee_token=?", (tok,)).fetchone()
    assert cm["time_since_last_promotion"] == 18

    life = conn.execute("SELECT distance_from_home, onboarding_status FROM lifecycle "
                        "WHERE employee_token=?", (tok,)).fetchone()
    assert life["distance_from_home"] == 12.5


def test_photo_is_stored_and_resolved_through_the_boundary(conn, admin):
    url = "data:image/png;base64,iVBORw0KGgo="
    out = manual_ingest.create_employee(conn, actor=admin, payload=_payload(photo=url))
    photos = identity.resolve_photos(conn, [out["token"]], actor=admin)
    assert photos[out["token"]] == url


def test_employee_with_no_photo_is_omitted_from_resolve(conn, admin):
    out = manual_ingest.create_employee(conn, actor=admin, payload=_payload())
    assert identity.resolve_photos(conn, [out["token"]], actor=admin) == {}


def test_non_image_photo_rejected(conn, admin):
    with pytest.raises(ManualIngestError):
        manual_ingest.create_employee(conn, actor=admin, payload=_payload(photo="not-an-image"))


def test_bad_demographic_vocab_rejected(conn, admin):
    with pytest.raises(ManualIngestError):
        manual_ingest.create_employee(conn, actor=admin, payload=_payload(gender="X"))


def test_optional_groups_omitted_writes_no_rows(conn, admin):
    out = manual_ingest.create_employee(conn, actor=admin, payload=_payload())
    tok = out["token"]
    for table in ("skills_credentials", "cv_trajectory", "career_mobility",
                  "performance", "lifecycle", "employee_ops"):
        n = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE employee_token=?", (tok,)).fetchone()[0]
        assert n == 0, f"{table} should be empty when nothing was supplied"


def test_ensure_extra_columns_noop_on_current_schema(conn):
    # The test DB is built from the current schema, which already has the new columns.
    assert manual_ingest.ensure_extra_columns(conn) is False
