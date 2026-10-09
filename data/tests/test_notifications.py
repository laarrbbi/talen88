"""Engagement layer: opt-in is a hard precondition, scope is per-recipient.

These tests prove the two guarantees the notifications module must hold:
  - nothing is written for a kind the recipient has not opted into;
  - a recipient can only read / mark their OWN notifications.
Plus the transparency contract: every stored notification carries a `why`.
"""
from __future__ import annotations

from data import notifications as nf

ALICE = "alice@x"
BOB = "bob@x"


def test_opt_out_writes_nothing(conn):
    res = nf.create(conn, recipient_email=ALICE, kind="promotion_ready",
                    payload={"employee_token": "emp_1"})
    assert res["created"] is False and res["reason"] == "opted_out"
    assert nf.list_for(conn, actor_email=ALICE) == []


def test_opt_in_then_create_carries_why(conn):
    nf.set_pref(conn, actor_email=ALICE, kind="promotion_ready", opted_in=True)
    res = nf.create(conn, recipient_email=ALICE, kind="promotion_ready",
                    payload={"employee_token": "emp_1"})
    assert res["created"] is True
    inbox = nf.list_for(conn, actor_email=ALICE)
    assert len(inbox) == 1 and inbox[0]["why"]  # transparency line present
    assert inbox[0]["payload"]["employee_token"] == "emp_1" and inbox[0]["read"] is False


def test_unknown_kind_rejected(conn):
    assert nf.create(conn, recipient_email=ALICE, kind="nope",
                     payload={})["reason"] == "unknown_kind"
    assert nf.set_pref(conn, actor_email=ALICE, kind="nope",
                       opted_in=True)["reason"] == "unknown_kind"


def test_recipient_scope_isolated(conn):
    nf.set_pref(conn, actor_email=ALICE, kind="promotion_ready", opted_in=True)
    nf.create(conn, recipient_email=ALICE, kind="promotion_ready", payload={})
    # Bob sees nothing of Alice's, and cannot mark her notification read.
    assert nf.list_for(conn, actor_email=BOB) == []
    nid = nf.list_for(conn, actor_email=ALICE)[0]["id"]
    assert nf.mark_read(conn, actor_email=BOB, notif_id=nid) is False
    assert nf.list_for(conn, actor_email=ALICE)[0]["read"] is False  # untouched


def test_mark_read_own(conn):
    nf.set_pref(conn, actor_email=ALICE, kind="retention_approved", opted_in=True)
    nf.create(conn, recipient_email=ALICE, kind="retention_approved", payload={})
    nid = nf.list_for(conn, actor_email=ALICE)[0]["id"]
    assert nf.mark_read(conn, actor_email=ALICE, notif_id=nid) is True
    assert nf.list_for(conn, actor_email=ALICE)[0]["read"] is True
    assert nf.list_for(conn, actor_email=ALICE, unread_only=True) == []


def test_prefs_default_off_and_toggle(conn):
    prefs = nf.get_prefs(conn, actor_email=ALICE)
    assert prefs and all(p["opted_in"] is False for p in prefs)  # default off
    nf.set_pref(conn, actor_email=ALICE, kind="succession_gap", opted_in=True)
    updated = {p["kind"]: p["opted_in"] for p in nf.get_prefs(conn, actor_email=ALICE)}
    assert updated["succession_gap"] is True
