"""CV text extraction + deterministic field suggestion (Document Import).

Covers the format dispatch (txt always; docx when python-docx is present) and the
heuristics that turn CV text into suggested employee fields — proving nothing is invented
(only what is present is returned) and the same input is stable.
"""
from __future__ import annotations

import pytest

from data import cv_parse
from data.cv_parse import UnsupportedDocument

SAMPLE_CV = """GRACE HOPPER
Principal Software Engineer
grace.hopper@navy.mil | +1 (202) 555-0173 | Washington, DC

Summary
Compiler pioneer with 25 years of experience building systems.

Skills: Python, COBOL, SQL, Distributed Systems

Education
Ph.D. in Mathematics, Yale University
"""


def test_extract_text_plaintext_roundtrips():
    raw = SAMPLE_CV.encode("utf-8")
    assert "Principal Software Engineer" in cv_parse.extract_text("cv.txt", raw)


def test_extract_text_rejects_unsupported_extension():
    with pytest.raises(UnsupportedDocument):
        cv_parse.extract_text("photo.png", b"\x89PNG")


def test_suggest_fields_finds_real_values_only():
    s = cv_parse.suggest_fields(SAMPLE_CV)
    fields = s["fields"]
    assert fields["email"]["value"] == "grace.hopper@navy.mil"
    assert fields["full_name"]["value"] == "Grace Hopper"      # ALL-CAPS header title-cased
    assert "Engineer" in fields["role"]["value"]
    assert "202" in fields["phone"]["value"] and "555" in fields["phone"]["value"]
    assert "Python" in s["skills"] and "SQL" in s["skills"]
    assert s["years_experience"] == 25
    assert s["education_level"] == "doctorate"
    # Every suggestion carries a confidence in (0, 1].
    assert all(0 < f["confidence"] <= 1 for f in fields.values())


def test_suggest_fields_omits_what_it_cannot_find():
    s = cv_parse.suggest_fields("just some prose with no contact details at all")
    assert "email" not in s["fields"]
    assert "full_name" not in s["fields"]
    assert s["years_experience"] is None


def test_suggest_fields_is_deterministic():
    assert cv_parse.suggest_fields(SAMPLE_CV) == cv_parse.suggest_fields(SAMPLE_CV)


JOHN_CV = """JOHN A. SMITH
Senior Backend Engineer
john.smith@example.com | +1 (415) 555-0142 | San Francisco, CA

Skills: Python, Go, PostgreSQL, Kubernetes, AWS

Education
M.S. in Computer Science, Stanford University
"""


def test_name_tolerates_middle_initial_and_skips_role_line():
    s = cv_parse.suggest_fields(JOHN_CV)
    # The header has a middle initial; the line below is a role, not the name.
    assert s["fields"]["full_name"]["value"] == "John A. Smith"
    assert s["fields"]["role"]["value"] == "Senior Backend Engineer"


def test_inline_skills_do_not_swallow_the_next_section():
    s = cv_parse.suggest_fields(JOHN_CV)
    assert s["skills"] == ["Python", "Go", "PostgreSQL", "Kubernetes", "AWS"]
    assert "Education" not in s["skills"]
    assert s["education_level"] == "master"


CONTRACT = """EMPLOYMENT CONTRACT

This agreement is made between Acme Corp and the Employee.
Position: Senior Risk Analyst
Employment type: Permanent (full-time)
Start date: 15/03/2021
Annual base salary: £72,000
"""

REVIEW = """Annual Performance Review — 2025

Employee: Dana Lee
Overall rating: 4.2 / 5
Goal attainment: 95%
Strengths: delivery, mentoring.
"""


def test_contract_extracts_pay_date_and_type():
    s = cv_parse.suggest_fields(CONTRACT, doc_type="contract")
    f = s["fields"]
    assert f["base_salary"]["value"] == 72000.0
    assert f["currency"]["value"] == "GBP"
    assert f["hire_date"]["value"] == "2021-03-15"
    assert f["employment_type"]["value"] == "full_time"
    assert f["role"]["value"] == "Senior Risk Analyst"
    # the document title ("EMPLOYMENT CONTRACT") must NOT be read as the person's name
    assert "full_name" not in f


def test_review_extracts_rating_goal_and_labeled_name():
    s = cv_parse.suggest_fields(REVIEW, doc_type="review")
    f = s["fields"]
    assert f["performance_rating"]["value"] == 4.2
    assert f["goal_attainment"]["value"] == 0.95
    assert f["full_name"]["value"] == "Dana Lee"   # from the "Employee:" label


def test_auto_detect_classifies_document():
    assert cv_parse.detect_doc_type("", CONTRACT) == "contract"
    assert cv_parse.detect_doc_type("", REVIEW) == "review"
    assert cv_parse.detect_doc_type("jane_resume.pdf", "") == "cv"
    # auto path on suggest_fields picks the type and the matching fields
    assert cv_parse.suggest_fields(CONTRACT, doc_type="auto")["doc_type"] == "contract"
    assert "base_salary" in cv_parse.suggest_fields(CONTRACT)["fields"]


CV_RICH = """JANE DOE
Senior Data Scientist
jane.doe@example.com

Education
M.S. in Computer Science, Stanford University

Skills: Python, SQL, Machine Learning
Languages: English, French, Spanish
Certifications: AWS Certified, CFA
"""


def test_cv_extracts_languages_certs_university_and_field():
    s = cv_parse.suggest_fields(CV_RICH)
    assert "English" in s["languages"] and "French" in s["languages"]
    assert "AWS Certified" in s["certifications"] and "CFA" in s["certifications"]
    assert s["fields"]["education_institution"]["value"] == "Stanford University"
    assert s["fields"]["education_field"]["value"] == "technical_degree"
    assert s["education_level"] == "master"


def test_contract_extracts_pay_hire_date_and_type():
    contract = (
        "EMPLOYMENT CONTRACT\nThis agreement is between Acme and the employee.\n"
        "Position: Senior Analyst\nAnnual base salary: £85,000\n"
        "Start date: 2024-09-01\nThis is a permanent full-time position."
    )
    s = cv_parse.suggest_fields(contract, doc_type="contract")
    assert s["doc_type"] == "contract"
    assert s["fields"]["base_salary"]["value"] == 85000.0
    assert s["fields"]["currency"]["value"] == "GBP"
    assert s["fields"]["hire_date"]["value"] == "2024-09-01"
    assert s["fields"]["employment_type"]["value"] == "full_time"


def test_review_extracts_rating_and_goal():
    review = "PERFORMANCE REVIEW\nOverall rating: 4.3 / 5\nGoal attainment: 92%"
    s = cv_parse.suggest_fields(review, doc_type="review")
    assert s["fields"]["performance_rating"]["value"] == 4.3
    assert s["fields"]["goal_attainment"]["value"] == 0.92


def test_auto_detect_classifies_by_content():
    assert cv_parse.detect_doc_type("", "PERFORMANCE REVIEW\nGoal attainment: 80%") == "review"
    assert cv_parse.detect_doc_type("contract_2024.pdf", "anything") == "contract"


def test_extract_text_reads_docx_when_available(tmp_path):
    docx = pytest.importorskip("docx")
    p = tmp_path / "cv.docx"
    doc = docx.Document()
    doc.add_paragraph("Ada Lovelace")
    doc.add_paragraph("Senior Data Analyst")
    doc.add_paragraph("ada@example.com")
    doc.save(p)
    text = cv_parse.extract_text("cv.docx", p.read_bytes())
    assert "Senior Data Analyst" in text
    assert cv_parse.suggest_fields(text)["fields"]["email"]["value"] == "ada@example.com"
