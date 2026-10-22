"""Employee documents (encrypted, HR-admin only), the career timeline, and HR-only reasons."""
from __future__ import annotations

import json
from datetime import date

import pytest

from data import db, documents, query
from data.tests.conftest import T_ADA, T_CY

CONTRACT = b"EMPLOYMENT CONTRACT\nThis employment agreement sets the base salary and notice period."


# ---- documents -------------------------------------------------------------------------

def test_upload_list_download_delete(conn, admin):
    meta = documents.add_document(conn, T_ADA, actor=admin, filename="Ada_Lovelace_contract.txt",
                                  raw=CONTRACT)
    assert meta["doc_type"] == "contract" and meta["size_bytes"] == len(CONTRACT)
    listed = documents.list_documents(conn, T_ADA, actor=admin)
    assert [d["filename"] for d in listed] == ["Ada_Lovelace_contract.txt"]
    found_meta, raw = documents.get_document(conn, T_ADA, meta["id"], actor=admin)
    assert raw == CONTRACT and found_meta["mime"] == "text/plain"
    assert documents.get_document(conn, T_CY, meta["id"], actor=admin) is None   # wrong owner
    assert documents.delete_document(conn, T_ADA, meta["id"], actor=admin)
    assert documents.list_documents(conn, T_ADA, actor=admin) == []


def test_file_and_name_are_encrypted_at_rest(conn, admin):
    documents.add_document(conn, T_ADA, actor=admin, filename="Ada_Lovelace_cv.txt", raw=CONTRACT)
    row = conn.execute("SELECT filename_enc, content_enc FROM employee_documents").fetchone()
    assert b"Lovelace" not in bytes(row["filename_enc"])
    assert b"salary" not in bytes(row["content_enc"])


def test_audit_never_records_the_filename(conn, admin):
    documents.add_document(conn, T_ADA, actor=admin, filename="Ada_Lovelace_cv.txt", raw=CONTRACT)
    rows = conn.execute("SELECT filters_json FROM audit_log WHERE action LIKE 'document%'").fetchall()
    assert rows and all("Lovelace" not in r["filters_json"] for r in rows)


def test_managers_cannot_touch_documents(conn, admin, tech_manager):
    meta = documents.add_document(conn, T_ADA, actor=admin, filename="cv.txt", raw=CONTRACT)
    for call in (lambda: documents.list_documents(conn, T_ADA, actor=tech_manager),
                 lambda: documents.get_document(conn, T_ADA, meta["id"], actor=tech_manager),
                 lambda: documents.delete_document(conn, T_ADA, meta["id"], actor=tech_manager),
                 lambda: documents.add_document(conn, T_ADA, actor=tech_manager,
                                                filename="x.txt", raw=b"x")):
        with pytest.raises(PermissionError):
            call()
    denied = conn.execute("SELECT COUNT(*) FROM audit_log WHERE actor_email = ? "
                          "AND filters_json LIKE '%denied%'", (tech_manager.email,)).fetchone()[0]
    assert denied == 4


@pytest.mark.parametrize("filename,raw,doc_type,msg", [
    ("virus.exe", b"MZ", "auto", "unsupported"),
    ("big.pdf", b"x" * (documents.MAX_BYTES + 1), "auto", "too large"),
    ("empty.txt", b"", "auto", "empty"),
    ("cv.txt", b"text", "passport", "doc_type"),
])
def test_bad_uploads_rejected(conn, admin, filename, raw, doc_type, msg):
    with pytest.raises(documents.DocumentError, match=msg):
        documents.add_document(conn, T_ADA, actor=admin, filename=filename, raw=raw,
                               doc_type=doc_type)


def test_unknown_employee_rejected(conn, admin):
    with pytest.raises(documents.DocumentError, match="unknown employee"):
        documents.add_document(conn, "emp_nobody", actor=admin, filename="cv.txt", raw=CONTRACT)


def test_images_get_a_neutral_type(conn, admin):
    meta = documents.add_document(conn, T_ADA, actor=admin, filename="scan.png", raw=b"\x89PNG....")
    assert meta["doc_type"] == "other" and meta["mime"] == "image/png"


def test_migrate_adds_documents_table(tmp_path):
    c = db.get_connection(tmp_path / "old.db")
    c.execute("CREATE TABLE employees (token TEXT PRIMARY KEY)")
    db.migrate(c)
    assert c.execute("SELECT name FROM sqlite_master WHERE name = 'employee_documents'").fetchone()
    c.close()


# ---- timeline ------------------------------------------------------------------------------

@pytest.fixture()
def career(conn):
    conn.execute("INSERT INTO career_mobility (employee_token, promotions, internal_moves, "
                 "internal_application_history) VALUES (?,?,?,?)",
                 (T_ADA, json.dumps([{"date": "2024-06-01", "from": "L1", "to": "L2"}]),
                  json.dumps([{"date": "2023-03-01", "team": "Tech Team A"}]),
                  json.dumps([{"date": "2026-02-01", "role": "internal posting"}])))
    conn.execute("INSERT INTO compensation (employee_token, base_salary, currency, last_raise_date, "
                 "percent_salary_hike, bonus_history, vesting_dates) VALUES (?,?,?,?,?,?,?)",
                 (T_ADA, 100000, "USD", "2025-09-01", 4.5,
                  json.dumps([{"date": "2025-12-15", "amount": 9000}]),
                  json.dumps(["2026-03-01", "2026-12-01", "2029-01-01"])))
    conn.execute("INSERT INTO interventions (employee_token, action_type, date, status) "
                 "VALUES (?,?,?,?)", (T_ADA, "compensation_review", "2026-04-01", "approved"))
    conn.commit()
    return conn


def _kinds(events):
    return [e["kind"] for e in events]


def test_timeline_lists_events_newest_first(career, admin):
    tl = query.get_employee_timeline(career, T_ADA, actor=admin, today=date(2026, 6, 1))
    dates = [e["date"] for e in tl["events"]]
    assert dates == sorted(dates, reverse=True)
    assert {"hire", "promotion", "internal_move", "internal_application", "raise", "bonus",
            "vest", "intervention"} <= set(_kinds(tl["events"]))
    raise_ev = next(e for e in tl["events"] if e["kind"] == "raise")
    assert raise_ev["detail"] == "4.5%"
    vests = [e for e in tl["events"] if e["kind"] == "vest"]
    assert [v["upcoming"] for v in vests] == [True, False]      # 2029 vest is beyond a year


def test_managers_see_no_pay_amounts_or_internal_applications(career, tech_manager):
    tl = query.get_employee_timeline(career, T_ADA, actor=tech_manager, today=date(2026, 6, 1))
    assert "internal_application" not in _kinds(tl["events"])
    assert next(e for e in tl["events"] if e["kind"] == "raise")["detail"] == ""
    assert all("9000" not in e["detail"] for e in tl["events"])


def test_timeline_is_scoped(career, tech_manager):
    assert query.get_employee_timeline(career, T_CY, actor=tech_manager) == {}


# ---- HR-only reason codes -------------------------------------------------------------------

def test_internal_application_reason_is_hr_only(conn, admin, tech_manager):
    conn.execute("INSERT INTO reason_codes (employee_token, as_of_date, metric, label, direction, "
                 "weight, detail) VALUES (?,?,?,?,?,?,?)",
                 (T_ADA, "2026-06-01", "retention_risk", "applied_internally", "increases", 0.3,
                  "Applied for 1 internal role(s) in the last 12 months"))
    conn.commit()

    def labels(actor):
        row = next(r for r in query.get_employees(conn, actor=actor) if r["token"] == T_ADA)
        return {c["label"] for c in row["reason_codes"]}, query.get_employee_360(conn, T_ADA, actor=actor)

    admin_labels, admin_360 = labels(admin)
    mgr_labels, mgr_360 = labels(tech_manager)
    assert "applied_internally" in admin_labels
    assert "applied_internally" not in mgr_labels and "career_movement_signal" in mgr_labels
    assert all("internal role" not in (c.get("detail") or "") for c in mgr_360["reason_codes"])
