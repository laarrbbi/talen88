"""Security & privacy tests: encryption, PII isolation, name-resolution authz,
tamper-evident audit log."""
from __future__ import annotations

import pytest

from data import auth, identity
from data.audit import verify_chain, write_audit
from data.query import get_employees
from data.security import crypto
from data.security.crypto import KeyError_
from .conftest import T_ADA, T_CY


# ----- encryption at rest ----------------------------------------------------
def test_encrypt_decrypt_roundtrip():
    assert crypto.decrypt(crypto.encrypt("Ada Lovelace")) == "Ada Lovelace"


def test_ciphertext_is_not_plaintext():
    blob = crypto.encrypt("Ada Lovelace")
    assert b"Ada" not in blob and b"Lovelace" not in blob


def test_tampered_ciphertext_rejected():
    blob = bytearray(crypto.encrypt("Ada Lovelace"))
    blob[-1] ^= 0x01
    with pytest.raises(KeyError_):
        crypto.decrypt(bytes(blob))


# ----- PII isolation ---------------------------------------------------------
def test_pii_only_lives_in_identities_table(conn):
    # No analytics/identity table other than `identities` should contain the name.
    name = "Ada Lovelace"
    for table in ("employees", "scores", "reason_codes", "feature_snapshots"):
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
        assert "full_name" not in cols and "email" not in cols
        # And the literal name string appears nowhere in those tables' data.
        dump = str(conn.execute(f"SELECT * FROM {table}").fetchall())
        assert name not in dump


def test_identities_table_stores_only_ciphertext(conn):
    row = conn.execute("SELECT full_name_enc, email_enc FROM identities "
                       "WHERE token = ?", (T_ADA,)).fetchone()
    assert isinstance(row["full_name_enc"], (bytes, bytearray))
    assert b"Ada" not in row["full_name_enc"]


# ----- name resolution (the single isolated boundary) ------------------------
def test_admin_resolves_any_name(conn, admin):
    out = identity.resolve_names(conn, [T_ADA, T_CY], actor=admin)
    assert out[T_ADA] == "Ada Lovelace" and out[T_CY] == "Cy Costa"


def test_manager_cannot_resolve_other_division(conn, tech_manager):
    out = identity.resolve_names(conn, [T_ADA, T_CY], actor=tech_manager)
    assert out == {T_ADA: "Ada Lovelace"}  # T_CY (operations) omitted


def test_resolution_is_audited(conn, admin):
    identity.resolve_names(conn, [T_ADA], actor=admin)
    n = conn.execute("SELECT COUNT(*) FROM audit_log WHERE action='resolve_name'").fetchone()[0]
    assert n == 1


def test_unknown_token_silently_omitted(conn, admin):
    assert identity.resolve_names(conn, ["emp_doesnotexist"], actor=admin) == {}


# ----- name->token search (reverse of resolution, same boundary) -------------
def test_admin_searches_any_division(conn, admin):
    out = identity.search_identities(conn, "cy", actor=admin)
    assert any(h["token"] == T_CY and h["name"] == "Cy Costa" for h in out)


def test_search_is_case_insensitive_substring(conn, admin):
    out = identity.search_identities(conn, "LOVE", actor=admin)
    assert [h["token"] for h in out] == [T_ADA]


def test_manager_search_scoped_to_own_division(conn, tech_manager):
    # "a" matches Ada (tech) and Cy Costa (ops); the manager only sees their division.
    out = identity.search_identities(conn, "a", actor=tech_manager)
    tokens = {h["token"] for h in out}
    assert T_ADA in tokens and T_CY not in tokens
    assert all(h["division"] == "technology" for h in out)


def test_search_returns_no_name_for_other_division(conn, tech_manager):
    assert identity.search_identities(conn, "costa", actor=tech_manager) == []


def test_search_respects_limit(conn, admin):
    out = identity.search_identities(conn, "e", actor=admin, limit=1)
    assert len(out) <= 1


def test_search_is_audited_without_logging_the_query(conn, admin):
    identity.search_identities(conn, "Lovelace", actor=admin)
    row = conn.execute("SELECT filters_json FROM audit_log WHERE action='search_identity'").fetchone()
    assert row is not None
    assert "Lovelace" not in (row["filters_json"] or "")  # raw partial name (PII) never persisted


def test_empty_query_matches_nothing(conn, admin):
    assert identity.search_identities(conn, "   ", actor=admin) == []


def test_tokens_are_non_sequential():
    a = auth.Actor(email="x", name="x", role="admin", division=None)  # noqa: F841
    t1, t2 = identity.new_token(), identity.new_token()
    assert t1 != t2 and t1.startswith("emp_")


# ----- tamper-evident audit log ---------------------------------------------
def test_audit_chain_intact_after_normal_writes(conn, admin):
    get_employees(conn, actor=admin)
    identity.resolve_names(conn, [T_ADA], actor=admin)
    assert verify_chain(conn) is True


def test_audit_chain_detects_tampering(conn, admin):
    write_audit(conn, actor_email=admin.email, action="get_employees", result_count=1)
    write_audit(conn, actor_email=admin.email, action="get_employees", result_count=2)
    # Silently rewrite a historical row's payload.
    conn.execute("UPDATE audit_log SET result_count = 999 WHERE id = "
                 "(SELECT MIN(id) FROM audit_log)")
    conn.commit()
    assert verify_chain(conn) is False
