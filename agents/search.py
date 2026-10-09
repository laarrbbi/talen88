"""People-search agent (read-only, Level 1) — the Company Graph's conversational discovery.

This is the search/discovery counterpart to the conversational agent: where that one
answers analytic questions ("who is high-risk and underpaid?"), this one finds *people*
across the full canonical record from a natural-language description ("a high-value
engineer in compliance with a CFA who isn't a flight risk").

How it stays grounded + safe:
  * PARSE — the LLM turns the free-text request into a structured `criteria` map over the
    KNOWN searchable fields (below). The parse call is audited and PII-guarded like every
    other model use. If the model is unavailable, a small deterministic keyword parser
    keeps the feature working (graceful degradation, never a crash).
  * RETRIEVE — criteria go to the scoped, audited `search_people` query tool. Permission
    scope is enforced server-side, so a manager's search can never surface someone outside
    their division — broad field search is NOT broad access.
  * HONESTY — a requested attribute the schema does not carry (spoken language, visa /
    work-authorization, availability) is surfaced in `unsupported_fields`, never invented.
  * NARRATE — the grounded, token-only result is phrased conversationally (or via a
    deterministic template when the model is down). Names never enter the model; the UI
    resolves tokens to names at its single identity boundary.
"""
from __future__ import annotations

import json
import re

from . import reasoning
from .autonomy import Level
from .tools import DataTools

AGENT = "search"

# The fields a request can be parsed into — the model's allowed vocabulary. This mirrors
# data.query.SEARCH_FIELDS (the seam is the source of truth; anything the model emits that
# is NOT honored comes back from the seam as unsupported). Kept here as plain descriptions
# so the prompt is self-contained and never leaks schema internals.
SEARCHABLE_FIELDS = {
    "division": "business division, e.g. technology / operations / front office / risk & compliance / corporate",
    "location": "office/city, e.g. London, New York, Singapore, Hong Kong, Frankfurt, Tokyo",
    "role": "job role keyword (substring), e.g. engineer, analyst, trader",
    "title": "job title keyword (substring)",
    "team": "team name keyword (substring)",
    "employment_type": "full_time / part_time / contract / intern",
    "status": "active / on_leave / terminated",
    "min_level": "minimum seniority level (integer 1-7; MD/senior ~ 5+)",
    "skill": "a single required skill (use the list form 'skills' for several)",
    "skills": "list of required skills — ALL must be present",
    "certification": "a single required certification",
    "certifications": "list of required certifications — ALL must be present",
    "license": "a single required license, e.g. CFA, FRM, CPA, Series 7",
    "licenses": "list of required licenses — ALL must be present",
    "education_level": "highest education level",
    "education_field": "field of study, e.g. finance, computer_science",
    "min_pay_percentile": "minimum pay percentile in role (0-100)",
    "max_pay_percentile": "maximum pay percentile in role (0-100; low = potentially underpaid)",
    "min_years_experience": "minimum total years of experience",
    "risk_band": "flight-risk band: high / medium / low",
    "min_value_score": "minimum value-to-org score (0-100)",
    "max_flight_risk": "maximum flight-risk score (0-100)",
}

# Attributes users plausibly ask for that the schema does NOT carry yet. Naming them lets
# the assistant be explicit ("we don't store visa status yet") instead of silently dropping
# the constraint — and flags them as fields to add later.
KNOWN_MISSING_FIELDS = {
    "language": ("language", "languages", "fluent", "speaks", "bilingual"),
    "visa": ("visa", "work authorization", "work_authorization", "right to work", "sponsorship"),
    "availability": ("available", "availability", "relocate", "relocation", "willing to move",
                     "notice period", "start date"),
}

_SYSTEM = (
    "You are Talent88's people-search assistant for the Company Graph. An HR manager "
    "describes who they are looking for in plain language; you translate that into a "
    "structured search over the workforce, then help them read the results. Be a natural, "
    "helpful colleague — but only ever discuss the people actually returned by the search "
    "(token-keyed); never invent a person, a name, or an attribute."
)

_PARSE_SYSTEM = (
    "You convert a natural-language people-search request into a STRICT JSON object and "
    "nothing else. Output exactly: {\"criteria\": {<field>: <value>, ...}, "
    "\"unsupported\": [<field>, ...]}. Use ONLY these fields:\n"
    + "\n".join(f"  - {k}: {v}" for k, v in SEARCHABLE_FIELDS.items())
    + "\nIf the request mentions an attribute that is NOT in that list (for example a spoken "
    "language, a visa / work-authorization status, or availability / willingness to "
    "relocate), DO NOT guess a field for it — instead add a short label for it to "
    "\"unsupported\". Never include names of people. Reply with the JSON object only, no prose."
)

# Allowed criteria keys (single + list forms) the seam will actually honor.
_ALLOWED_KEYS = set(SEARCHABLE_FIELDS)


def parse_criteria(tools: DataTools, question: str,
                   history: list[dict] | None = None) -> tuple[dict, list[str]]:
    """Return (criteria, unsupported). LLM-first; deterministic keyword fallback if the
    model is unavailable so search still works. Always honest about unsupported asks."""
    messages = [{"role": "system", "content": _PARSE_SYSTEM}]
    for turn in (history or [])[-6:]:
        if turn.get("role") in ("user", "assistant") and (turn.get("content") or "").strip():
            messages.append({"role": turn["role"], "content": turn["content"].strip()})
    messages.append({"role": "user", "content": question.strip()})

    resp = reasoning.audited_generate(tools, agent=AGENT, messages=messages)
    if resp is not None:
        parsed = _extract_json(resp.text)
        if parsed is not None:
            criteria = {k: v for k, v in (parsed.get("criteria") or {}).items()
                        if k in _ALLOWED_KEYS}
            unsupported = [str(u) for u in (parsed.get("unsupported") or [])]
            unsupported += _scan_missing(question)
            return criteria, sorted(set(unsupported))
    # Model down or unparseable → deterministic keyword parse (still honest about gaps).
    return _keyword_parse(question), _scan_missing(question)


def run(tools: DataTools, question: str, history: list[dict] | None = None,
        limit: int = 60) -> dict:
    """Parse → scoped search → grounded narrative. Token-only throughout; audited at every
    step (the parse LLM call, the search query, and the narration LLM call)."""
    criteria, unsupported = parse_criteria(tools, question, history)
    result = tools.search_people(criteria, limit=limit)
    matched = result.get("matched", [])
    # Union the model's honest gaps with anything the seam itself couldn't honor.
    unsupported = sorted(set(unsupported) | set(result.get("unsupported_fields", [])))
    applied = result.get("applied", {})

    tools.log_run(agent=AGENT, action="search", level=int(Level.INSIGHT),
                  summary={"question": question, "criteria": applied,
                           "unsupported": unsupported},
                  result_count=len(matched))

    note = None
    if unsupported:
        note = ("Not stored yet (so not used in this search): "
                + ", ".join(unsupported) + ". Flagged as fields to add.")
    answer = _narrate(tools, question, applied, matched, unsupported, history) \
        or _template(matched, applied, unsupported)
    return {"agent": AGENT, "question": question, "criteria": applied,
            "unsupported_fields": unsupported, "matched": matched,
            "note": note, "answer": answer}


def _narrate(tools, question, applied, matched, unsupported, history) -> str | None:
    payload = {"question": question, "criteria_applied": applied,
               "unsupported_fields": unsupported,
               "match_count": len(matched), "matched": matched[:30]}
    return reasoning.narrate(tools, agent=AGENT, system=_SYSTEM,
                             data_json=json.dumps(payload, default=str),
                             history=history, caller=_caller(tools), conversational=True)


def _template(matched: list[dict], applied: dict, unsupported: list[str]) -> str:
    n = len(matched)
    crit = ", ".join(f"{k}={v}" for k, v in applied.items()) or "no filters"
    base = (f"Found {n} {'person' if n == 1 else 'people'} matching {crit}."
            if n else f"No one in your scope matches {crit}.")
    if unsupported:
        base += (" I couldn't use " + ", ".join(unsupported)
                 + " — we don't store that yet.")
    return base


def _caller(tools: DataTools) -> dict | None:
    try:
        me = tools.me()
        return {"role": me.get("role"), "division": me.get("division")}
    except Exception:
        return None


# ---- helpers: JSON extraction + honest gap detection + keyword fallback -------------
def _extract_json(text: str) -> dict | None:
    """Pull the first JSON object out of a model reply (tolerates code fences / stray prose)."""
    if not text:
        return None
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
        return obj if isinstance(obj, dict) else None
    except ValueError:
        return None


def _scan_missing(question: str) -> list[str]:
    """Detect requested-but-unstored attributes by keyword, so we flag them even when the
    model forgot to. Returns canonical missing-field labels (language / visa / availability)."""
    q = question.lower()
    found = []
    for label, cues in KNOWN_MISSING_FIELDS.items():
        if any(cue in q for cue in cues):
            found.append(label)
    return found


# Licenses/certs we can spot literally when the model is unavailable.
_KNOWN_LICENSES = ("cfa", "frm", "cpa", "caia", "pmp", "series 7", "series 63", "series 99")
_LOCATIONS = ("london", "new york", "singapore", "hong kong", "frankfurt", "tokyo")
_DIVISIONS = ("technology", "operations", "front office", "risk & compliance", "corporate")


def _keyword_parse(question: str) -> dict:
    """A modest deterministic parser for when the LLM is down: pulls licenses, location,
    division, and a coarse risk band out of the text. Conservative — better to under-match
    and let the manager refine than to invent a constraint."""
    q = question.lower()
    criteria: dict = {}
    for lic in _KNOWN_LICENSES:
        if lic in q:
            criteria["license"] = lic.upper() if lic.isalpha() else lic.title()
            break
    for loc in _LOCATIONS:
        if loc in q:
            criteria["location"] = loc.title()
            break
    for div in _DIVISIONS:
        if div in q:
            criteria["division"] = div
            break
    if "high risk" in q or "high-risk" in q or "flight risk" in q:
        criteria["risk_band"] = "high"
    elif "low risk" in q or "low-risk" in q or "not a flight risk" in q or "isn't a flight risk" in q:
        criteria["risk_band"] = "low"
    return criteria
