"""Phase 6 — INGESTION readers + adapters (the AI-free transform path).

Proves the format readers (CSV / multi-sheet Excel with an offset header / JSON / XML),
the normalize stage (Excel serial dates, currency-to-base, grade->level, controlled
vocab), and the two real adapters work end-to-end:
  * `workday` lands a worker export into `employee_core` with normalized values;
  * `engagement_survey:<platform>` lands PRECOMPUTED scores/drivers/Q12 into `engagement`
    verbatim — proven by asserting the stored values equal the source values (no recompute).
Also covers missing-field tolerance and that the live-integration seams stay stubbed.
"""
from __future__ import annotations

import csv
import json
from datetime import datetime

import pytest

from data.ingestion import normalize, stubs
from data.ingestion.adapters import build_survey_adapter, get_adapter
from data.ingestion.pipeline import run_ingestion
from data.ingestion.readers import (
    excel_sheet_names, read_csv, read_excel, read_json, read_xml,
)


# --- Stage 1: format readers --------------------------------------------------------

def test_read_csv_uses_header_keys(tmp_path):
    p = tmp_path / "a.csv"
    p.write_text("Email,Level\nada@co,3\nbo@co,4\n", encoding="utf-8")
    rows = read_csv(p)
    assert rows == [{"Email": "ada@co", "Level": "3"}, {"Email": "bo@co", "Level": "4"}]


def test_read_json_list_and_wrapped(tmp_path):
    lst = tmp_path / "l.json"
    lst.write_text('[{"a": 1}, {"a": 2}]', encoding="utf-8")
    assert read_json(lst) == [{"a": 1}, {"a": 2}]
    wrapped = tmp_path / "w.json"
    wrapped.write_text('{"records": [{"a": 9}]}', encoding="utf-8")
    assert read_json(wrapped) == [{"a": 9}]


def test_read_xml_flat_records(tmp_path):
    p = tmp_path / "x.xml"
    p.write_text(
        "<workers><worker id='1'><Email>ada@co</Email><Level>3</Level></worker>"
        "<worker id='2'><Email>bo@co</Email><Level>4</Level></worker></workers>",
        encoding="utf-8")
    rows = read_xml(p)
    assert rows[0] == {"Email": "ada@co", "Level": "3", "id": "1"}
    assert rows[1]["Email"] == "bo@co"


def test_read_excel_multi_sheet_with_offset_header(tmp_path):
    pd = pytest.importorskip("pandas")
    p = tmp_path / "wb.xlsx"
    with pd.ExcelWriter(p, engine="openpyxl") as w:
        # A banner occupies row 0; the real header is on row 1 (zero-based).
        pd.DataFrame({"Email": ["ada@co"], "Grade": ["VP"]}).to_excel(
            w, sheet_name="Workers", index=False, startrow=1)
        pd.DataFrame({"Token": ["emp_a"], "Base": [100]}).to_excel(
            w, sheet_name="Comp", index=False, startrow=1)
    assert set(excel_sheet_names(p)) == {"Workers", "Comp"}
    rows = read_excel(p, sheet="Workers", header_row=1)
    assert rows == [{"Email": "ada@co", "Grade": "VP"}]


# --- Stage 3: normalize -------------------------------------------------------------

def test_excel_serial_date_round_trips():
    serial = (datetime(2024, 1, 1) - datetime(1899, 12, 30)).days
    assert normalize.to_iso_date(serial) == "2024-01-01"
    assert normalize.excel_serial_to_date(serial) == "2024-01-01"


def test_date_formats_normalize_to_iso():
    assert normalize.to_iso_date("03/15/2021") == "2021-03-15"
    assert normalize.to_iso_date("2021-03-15") == "2021-03-15"
    assert normalize.to_iso_date(datetime(2021, 3, 15, 9, 0)) == "2021-03-15"


def test_currency_folds_to_usd_base():
    assert normalize.to_base_currency(100, "USD") == 100.0
    assert normalize.to_base_currency(100, "GBP") == 127.0
    with pytest.raises(ValueError):
        normalize.to_base_currency(100, "XYZ")


def test_grade_maps_to_canonical_level():
    assert normalize.normalize_level("VP") == 4
    assert normalize.normalize_level("md") == 6
    assert normalize.normalize_level("3") == 3
    with pytest.raises(ValueError):
        normalize.normalize_level("Grand Poobah")


def test_enum_alias_folds_then_validates():
    assert normalize.normalize_enum("employment_type", "Full Time") == "full_time"
    assert normalize.normalize_enum("status", "Separated") == "terminated"
    with pytest.raises(ValueError):
        normalize.normalize_enum("status", "vacationing")


# --- Stage 2/6: the Workday adapter, end to end -------------------------------------

WORKDAY_HEADER = ["Email", "Job Profile", "Business Title", "Management Level",
                  "Cost Center", "Supervisory Organization", "Location", "Worker Type",
                  "Travel", "Hire Date", "Position Status"]


def _workday_csv(path, emails):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=WORKDAY_HEADER)
        w.writeheader()
        for email in emails:
            w.writerow({
                "Email": email, "Job Profile": "Software Engineer",
                "Business Title": "Senior Engineer", "Management Level": "VP",
                "Cost Center": "technology", "Supervisory Organization": "Tech Team A",
                "Location": "New York", "Worker Type": "Full Time",
                "Travel": "Travel_Rarely", "Hire Date": "03/15/2021",
                "Position Status": "Active",
            })
    return path


def test_workday_adapter_lands_normalized_employee_core(conn, tmp_path):
    path = _workday_csv(tmp_path / "wd.csv",
                        ["ada.lovelace@example.com", "bo.bauer@example.com"])
    report = run_ingestion(conn, path, get_adapter("workday"))
    assert report.written == 2 and not report.review_queue
    core = conn.execute("SELECT * FROM employee_core LIMIT 1").fetchone()
    assert core["level"] == 4                       # VP -> 4 (grade mapped)
    assert core["division"] == "technology"
    assert core["employment_type"] == "full_time"  # alias-folded enum
    assert core["business_travel_frequency"] == "occasional"
    assert core["hire_date"] == "2021-03-15"        # US date normalized
    assert core["status"] == "active"
    # email was only a resolution key — it must NOT have become a column.
    assert "email" not in core.keys()


# --- Engagement survey adapters: ingest precomputed scores, NEVER recompute ---------

def test_survey_adapter_ingests_precomputed_drivers_verbatim(conn, tmp_path):
    header = ["Employee Email", "Survey Date", "Engagement", "Engagement Trend", "eNPS",
              "Response Rate", "Text Sentiment", "Driver: Accomplishment",
              "Driver: Autonomy", "Driver: Growth", "Driver: Recognition",
              "Open Text Themes"]
    path = tmp_path / "peakon.csv"
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=header)
        w.writeheader()
        w.writerow({
            "Employee Email": "ada.lovelace@example.com", "Survey Date": "2026-06-01",
            "Engagement": "78.5", "Engagement Trend": "2.1", "eNPS": "30",
            "Response Rate": "0.88", "Text Sentiment": "0.4",
            "Driver: Accomplishment": "82", "Driver: Autonomy": "70",
            "Driver: Growth": "65", "Driver: Recognition": "60",
            "Open Text Themes": "workload;recognition",
        })
    report = run_ingestion(conn, path, get_adapter("engagement_survey:peakon"))
    assert report.written == 1
    row = conn.execute("SELECT * FROM engagement LIMIT 1").fetchone()
    assert row["engagement_score"] == 78.5          # precomputed score, ingested as-is
    assert row["enps_score"] == 30
    assert row["as_of_date"] == "2026-06-01"
    drivers = json.loads(row["engagement_driver_scores"])
    # The driver values are the SOURCE values verbatim — proof of no recompute.
    assert drivers == {"Accomplishment": "82", "Autonomy": "70",
                       "Growth": "65", "Recognition": "60"}
    themes = json.loads(row["survey_open_text_themes"])
    assert themes == {"themes": "workload;recognition"}


def test_survey_adapter_maps_q12_vector(conn, tmp_path):
    header = ["Email Address", "EndDate", "EngagementIndex", "eNPS"] + \
             [f"Q12_{i}" for i in range(1, 13)]
    path = tmp_path / "qualtrics.csv"
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=header)
        w.writeheader()
        row = {"Email Address": "bo.bauer@example.com", "EndDate": "2026-06-01",
               "EngagementIndex": "71", "eNPS": "12"}
        row.update({f"Q12_{i}": str(i) for i in range(1, 13)})
        w.writerow(row)
    run_ingestion(conn, path, get_adapter("engagement_survey:qualtrics"))
    stored = conn.execute("SELECT q12_scores FROM engagement LIMIT 1").fetchone()
    q12 = json.loads(stored["q12_scores"])
    assert len(q12) == 12 and q12["Q01"] == "1" and q12["Q12"] == "12"


def test_survey_adapter_tolerates_platform_with_fewer_fields(conn, tmp_path):
    # Perceptyx export has no driver/Q12 columns — it must still ingest the scores.
    header = ["Email", "Administration Date", "Engagement Score", "eNPS"]
    path = tmp_path / "perceptyx.csv"
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=header)
        w.writeheader()
        w.writerow({"Email": "cy.costa@example.com", "Administration Date": "2026-06-01",
                    "Engagement Score": "64", "eNPS": "5"})
    report = run_ingestion(conn, path, build_survey_adapter("perceptyx"))
    assert report.written == 1
    assert conn.execute("SELECT engagement_score FROM engagement").fetchone()[0] == 64


def test_unknown_platform_rejected():
    with pytest.raises(KeyError):
        build_survey_adapter("myspace_survey")


# --- Live-integration seams stay stubbed (documented contracts, no calls) -----------

def test_integration_seams_are_documented_stubs():
    for call in (lambda: stubs.merge_hris("tok"),
                 lambda: stubs.graph_metadata("tenant", consented_tokens=set()),
                 lambda: stubs.external_market("lic", {"New York"})):
        with pytest.raises(NotImplementedError):
            call()
