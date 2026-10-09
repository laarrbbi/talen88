"""Phase 6 — ENTITY RESOLUTION. Exact-key matching with a human review queue.

The safety property: two distinct people who collide on the resolution key are NEVER
auto-merged. Such a record goes to the review queue carrying BOTH candidate tokens for a
human to adjudicate. Unique matches resolve; unknown people are unmatched; a record with
no key value is parked for review rather than guessed.
"""
from __future__ import annotations

from data.ingestion.resolve import resolve_records


def test_unique_match_resolves_to_its_token():
    candidates = [{"token": "emp_a", "email": "ada@co"}, {"token": "emp_b", "email": "bo@co"}]
    res = resolve_records([{"email": "ada@co", "role": "SWE"}], candidates, ("email",))
    assert res.resolved == [({"email": "ada@co", "role": "SWE"}, "emp_a")]
    assert not res.unmatched and not res.review_queue


def test_ambiguous_key_goes_to_review_and_is_never_merged():
    # Two REAL people share the same key value (a dirty source). Resolution must refuse.
    candidates = [{"token": "emp_a", "email": "dup@co"}, {"token": "emp_b", "email": "dup@co"}]
    res = resolve_records([{"email": "dup@co"}], candidates, ("email",))
    assert not res.resolved, "ambiguous record must NOT be written"
    assert len(res.review_queue) == 1
    item = res.review_queue[0]
    assert item.reason == "ambiguous"
    assert set(item.candidate_tokens) == {"emp_a", "emp_b"}  # both surfaced, neither chosen


def test_unknown_person_is_unmatched_not_invented():
    candidates = [{"token": "emp_a", "email": "ada@co"}]
    res = resolve_records([{"email": "ghost@co"}], candidates, ("email",))
    assert not res.resolved and not res.review_queue
    assert res.unmatched == [{"email": "ghost@co"}]


def test_missing_key_is_parked_for_review():
    res = resolve_records([{"email": None, "role": "SWE"}], [{"token": "emp_a", "email": "ada@co"}],
                          ("email",))
    assert not res.resolved
    assert res.review_queue[0].reason == "missing_key"


def test_match_is_case_and_whitespace_insensitive():
    candidates = [{"token": "emp_a", "email": "Ada@Co"}]
    res = resolve_records([{"email": "  ada@co  "}], candidates, ("email",))
    assert res.resolved[0][1] == "emp_a"


def test_multi_field_key_requires_all_fields_equal():
    candidates = [{"token": "emp_a", "first": "ada", "last": "lovelace"},
                  {"token": "emp_b", "first": "ada", "last": "byron"}]
    res = resolve_records([{"first": "ada", "last": "byron"}], candidates, ("first", "last"))
    assert res.resolved[0][1] == "emp_b"  # disambiguated by the second key field
