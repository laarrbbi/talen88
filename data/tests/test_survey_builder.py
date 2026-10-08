"""Step 2 (Survey module): builder read-seam over the question library + templates."""
from __future__ import annotations

from data.audit import verify_chain
from data.survey_library import seed_survey_library
from data.surveys import get_library, get_template, list_templates, qtype_for_scale


def test_qtype_for_scale_mapping():
    assert qtype_for_scale("AGREE5") == "likert"
    assert qtype_for_scale("FREQ5") == "likert"
    assert qtype_for_scale("ENPS") == "enps"
    assert qtype_for_scale("OPEN") == "open_text"
    assert qtype_for_scale(None) == "likert"


def test_library_shape(conn, admin):
    seed_survey_library(conn)
    lib = get_library(conn, actor=admin)
    assert lib["driver_count"] == 19
    assert lib["item_count"] == 53
    assert len(lib["scales"]) == 4
    # the outcome index is present and flagged
    eng = next(d for d in lib["drivers"] if d["code"] == "engagement")
    assert eng["is_outcome"] == 1 and len(eng["items"]) == 4
    # every item carries a builder qtype
    assert all("qtype" in it for d in lib["drivers"] for it in d["items"])


def test_library_qtypes_resolved(conn, admin):
    seed_survey_library(conn)
    lib = get_library(conn, actor=admin)
    enps = next(d for d in lib["drivers"] if d["code"] == "enps")
    qtypes = {it["qtype"] for it in enps["items"]}
    assert "enps" in qtypes and "open_text" in qtypes


def test_list_templates(conn, admin):
    seed_survey_library(conn)
    tpls = list_templates(conn, actor=admin)
    assert len(tpls) == 12
    keys = {t["key"] for t in tpls}
    assert {"annual_census", "exit_survey", "manager_effectiveness"} <= keys
    assert all(t["item_count"] >= 2 for t in tpls)


def test_get_template_by_id_and_key(conn, admin):
    seed_survey_library(conn)
    by_key = get_template(conn, actor=admin, key="manager_effectiveness")
    assert by_key["title"] == "Manager Effectiveness"
    assert len(by_key["questions"]) == 5
    # questions come back ordered and typed
    assert [q["position"] for q in by_key["questions"]] == [0, 1, 2, 3, 4]
    assert all(q["qtype"] == "likert" for q in by_key["questions"])
    # same template resolves by id
    by_id = get_template(conn, actor=admin, template_id=by_key["id"])
    assert by_id["key"] == "manager_effectiveness"


def test_get_template_unknown_returns_empty(conn, admin):
    seed_survey_library(conn)
    assert get_template(conn, actor=admin, template_id=9999) == {}


def test_exit_template_has_open_text(conn, admin):
    seed_survey_library(conn)
    exit_tpl = get_template(conn, actor=admin, key="exit_survey")
    assert any(q["qtype"] == "open_text" for q in exit_tpl["questions"])


def test_reads_are_audited_and_chain_intact(conn, admin):
    seed_survey_library(conn)
    get_library(conn, actor=admin)
    list_templates(conn, actor=admin)
    get_template(conn, actor=admin, key="quarterly_pulse")
    actions = {r[0] for r in conn.execute("SELECT DISTINCT action FROM audit_log")}
    assert {"survey_library", "survey_templates", "survey_template"} <= actions
    assert verify_chain(conn) is True


def test_manager_can_read_global_library(conn, tech_manager):
    # The library is org-global reference content — a division manager can read it to
    # build a survey (no division scoping applies to the question bank).
    seed_survey_library(conn)
    lib = get_library(conn, actor=tech_manager)
    assert lib["driver_count"] == 19
