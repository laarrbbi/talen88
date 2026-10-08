"""Phase 6 — VALIDATION. The pipeline fails loudly and writes NOTHING on bad input.

Covers the four failure classes from the plan: a missing required field, an out-of-range
value, a broken referential link (token not in employees), and a suspicious volume drop.
Each must raise `ValidationError`; the end-to-end cases also assert the canonical target
stayed empty (no partial write).
"""
from __future__ import annotations

import csv

import pytest

from data.ingestion.adapters import WORKDAY_ADAPTER
from data.ingestion.pipeline import run_ingestion
from data.ingestion.validate import ValidationError, validate

WORKDAY_HEADER = ["Email", "Job Profile", "Business Title", "Management Level",
                  "Cost Center", "Supervisory Organization", "Location", "Worker Type",
                  "Travel", "Hire Date", "Position Status"]


def _good_row(email="ada.lovelace@example.com", **over):
    row = {
        "Email": email, "Job Profile": "Software Engineer", "Business Title": "Engineer",
        "Management Level": "L3", "Cost Center": "technology",
        "Supervisory Organization": "Tech Team A", "Location": "New York",
        "Worker Type": "Full Time", "Travel": "Travel_Rarely", "Hire Date": "03/15/2021",
        "Position Status": "Active",
    }
    row.update(over)
    return row


def _write_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=WORKDAY_HEADER)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return path


def test_missing_required_field_fails_and_writes_nothing(conn, tmp_path):
    path = _write_csv(tmp_path / "wd.csv", [_good_row(**{"Position Status": ""})])
    with pytest.raises(ValidationError):
        run_ingestion(conn, path, WORKDAY_ADAPTER)
    assert conn.execute("SELECT COUNT(*) FROM employee_core").fetchone()[0] == 0


def test_out_of_range_value_fails(conn, tmp_path):
    # Management Level 9 -> canonical level 9, outside the 1..6 bound.
    path = _write_csv(tmp_path / "wd.csv", [_good_row(**{"Management Level": "9"})])
    with pytest.raises(ValidationError):
        run_ingestion(conn, path, WORKDAY_ADAPTER)
    assert conn.execute("SELECT COUNT(*) FROM employee_core").fetchone()[0] == 0


def test_volume_drop_fails(conn, tmp_path):
    # A single resolvable row but a guard expecting at least 100 -> volume sanity trips.
    path = _write_csv(tmp_path / "wd.csv", [_good_row()])
    with pytest.raises(ValidationError):
        run_ingestion(conn, path, WORKDAY_ADAPTER, min_rows=100)


def test_broken_referential_link_fails():
    # A resolved token that is not a real employee must be rejected before write.
    with pytest.raises(ValidationError) as exc:
        validate([({"role": "SWE", "level": 2}, "emp_ghost")],
                 required=("role",), known_tokens={"emp_real"}, min_rows=1)
    assert any("broken ref" in e for e in exc.value.errors)


def test_validation_error_collects_all_problems():
    with pytest.raises(ValidationError) as exc:
        validate([({"level": 99}, "emp_ghost")],
                 required=("role",), known_tokens=set(), min_rows=1)
    # missing role + broken ref + level out of range, all in one raise.
    assert len(exc.value.errors) >= 3
