"""Synthetic data generator (built FIRST).

Creates ~150 employees across 5 divisions with a real manager hierarchy and 6
monthly FEATURE snapshots per employee. Employees are tokenized: the pseudonymous
token is the identifier everywhere, and PII (name, email) is written ONLY to the
encrypted `identities` table via data.identity. Scores/reason_codes are left for
the score-refresh step (model_service). Easy to reseed: `--seed N`.

    python -m data.generate --seed 7

All data is synthetic. No real personal data anywhere.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import secrets
from datetime import date

from . import auth, canonical
from .db import db_path, get_connection, init_db
from .survey_demo import seed_demo_survey_activity
from .survey_library import seed_survey_library
from .identity import new_token, store_identity
from .security import crypto

DIVISIONS = ["front office", "technology", "risk & compliance", "operations", "corporate"]

ROLES = {
    "front office": ["Analyst", "Associate", "Trader", "Sales", "VP Markets", "Desk Head"],
    "technology": ["SWE", "Senior SWE", "Staff Engineer", "EM", "Platform Lead", "Head of Eng"],
    "risk & compliance": ["Risk Analyst", "Compliance Officer", "Quant Risk", "Risk Manager",
                          "Head of Risk", "CRO Office"],
    "operations": ["Ops Analyst", "Settlements", "Ops Specialist", "Ops Manager",
                   "Head of Ops", "COO Office"],
    "corporate": ["Coordinator", "HRBP", "Finance Analyst", "Manager", "Director",
                  "Head of Corporate"],
}
LOCATIONS = ["New York", "London", "Singapore", "Hong Kong", "Frankfurt", "Tokyo"]
EMPLOYMENT_TYPES = ["full_time", "full_time", "full_time", "contractor"]

# Target headcount per division (≈400 total + 1 CEO). Operations is the largest book
# of people; corporate/risk are leaner. Drives a believable org-size mix.
DIVISION_HEADCOUNT = {
    "operations": 120,
    "technology": 90,
    "front office": 80,
    "risk & compliance": 60,
    "corporate": 45,
}

# Location norms per division — weighted picks (front office clusters in NY/London,
# operations leans to APAC hubs, corporate is HQ-centric). Repeats encode weight.
DIVISION_LOCATION_WEIGHTS = {
    "front office": ["New York", "New York", "New York", "London", "London", "Hong Kong"],
    "technology": ["New York", "London", "London", "Singapore", "Frankfurt", "Tokyo"],
    "risk & compliance": ["New York", "London", "London", "Frankfurt", "Singapore"],
    "operations": ["Singapore", "Singapore", "Hong Kong", "London", "New York", "Tokyo"],
    "corporate": ["New York", "New York", "London"],
}

# IC level mix — mostly juniors, a thinner band of level-3 seniors (skews value tiers
# so only a minority of people are high-value stars).
IC_LEVEL_WEIGHTS = [1, 1, 1, 2, 2, 2, 3]

# Canonical `compensation` synthesis: local currency per location and a rough FX from
# a USD-equivalent per-level base band. Normalization to a base currency is an
# ingestion concern; here we just emit realistic local figures + the currency code.
CURRENCY_BY_LOCATION = {
    "New York": "USD", "London": "GBP", "Singapore": "SGD",
    "Hong Kong": "HKD", "Frankfurt": "EUR", "Tokyo": "JPY",
}
FX_FROM_USD = {"USD": 1.0, "GBP": 0.79, "EUR": 0.92, "SGD": 1.35, "HKD": 7.8, "JPY": 150.0}
LEVEL_BASE_USD = {1: 70_000, 2: 95_000, 3: 130_000, 4: 180_000, 5: 260_000, 6: 380_000}

# 🟡 external_market is LICENSE-gated: only locations the (synthetic) market-data
# license covers get a row, so the source-flag gate is demonstrable.
LICENSED_MARKET_REGIONS = {"New York", "London", "Singapore"}

# §5 division-specific signal packs (only the relevant division is populated). Each
# entry: (signal, unit, low, high). Generator divisions map to the spec's packs.
DIVISION_SIGNAL_PACKS = {
    "front office": [
        ("pnl_attribution", "USD_mm", 0.2, 25.0), ("production_credit", "USD_mm", 0.1, 12.0),
        ("book_of_business", "USD_mm", 5.0, 400.0), ("client_concentration", "ratio", 0.05, 0.7),
    ],
    "technology": [
        ("throughput", "story_points", 8.0, 60.0), ("cycle_time", "days", 0.5, 14.0),
        ("on_call_load", "shifts_per_qtr", 0.0, 12.0), ("skill_market_heat", "index", 0.2, 1.0),
    ],
    "operations": [
        ("shift_adherence", "ratio", 0.7, 1.0), ("attendance", "ratio", 0.8, 1.0),
        ("schedule_volatility", "index", 0.0, 1.0), ("workload_volume", "items_per_day", 20.0, 300.0),
        ("commute_distance", "km", 1.0, 60.0),
    ],
    "risk & compliance": [
        ("credential_currency", "ratio", 0.5, 1.0), ("workload", "cases", 5.0, 80.0),
        ("span_of_control", "reports", 0.0, 12.0),
    ],
    "corporate": [
        ("deliverable_completion", "ratio", 0.6, 1.0), ("project_load", "projects", 1.0, 8.0),
    ],
}

FIRST_NAMES = ["Dana", "Avery", "Jordan", "Riley", "Quinn", "Morgan", "Casey", "Reese",
               "Skyler", "Harper", "Rowan", "Emerson", "Sasha", "Devon", "Kendall", "Logan",
               "Marlow", "Noa", "Priya", "Mateo", "Sora", "Imani", "Diego", "Yuki", "Leah",
               "Omar", "Nina", "Tariq", "Elena", "Hassan", "Mei", "Ivan", "Zara", "Cole",
               "Anya", "Bilal", "Freya", "Kai"]
LAST_NAMES = ["Holloway", "Marsh", "Okafor", "Bauer", "Nakamura", "Costa", "Lindqvist",
              "Patel", "Rivera", "Schmidt", "Abara", "Vance", "Ito", "Mensah", "Romano",
              "Khan", "Sorensen", "Delgado", "Petrov", "Haddad", "Bianchi", "Novak", "Reyes",
              "Fischer", "Ahmadi", "Wallace", "Cho", "Mbeki", "Larsen", "Ferraro"]

SNAPSHOT_DATES = ["2026-01-01", "2026-02-01", "2026-03-01",
                  "2026-04-01", "2026-05-01", "2026-06-01"]

# Skills catalog (token-only graph). Each (name, family). Employees are assigned a
# division-relevant subset plus a couple of general skills; count + proficiency grow
# with level so the Skills-Depth and Mobility scores vary realistically.
SKILLS_CATALOG = [
    ("Python", "Engineering"), ("Distributed Systems", "Engineering"),
    ("Cloud Infrastructure", "Engineering"), ("Data Modeling", "Data"),
    ("Machine Learning", "Data"), ("SQL", "Data"),
    ("Market Risk", "Risk"), ("Credit Risk", "Risk"), ("Regulatory Reporting", "Risk"),
    ("Derivatives Pricing", "Finance"), ("Portfolio Management", "Finance"),
    ("Financial Modeling", "Finance"), ("Trade Settlement", "Operations"),
    ("Process Automation", "Operations"), ("Vendor Management", "Operations"),
    ("People Leadership", "Leadership"), ("Strategy", "Leadership"),
    ("Stakeholder Management", "Leadership"), ("Negotiation", "Communication"),
    ("Presentation", "Communication"), ("Technical Writing", "Communication"),
    ("Product Sense", "Product"), ("Project Management", "Product"),
    ("Compliance Advisory", "Risk"),
]

# Which skill families are core to each division (drives skill assignment).
DIVISION_SKILL_FAMILIES = {
    "front office": ["Finance", "Communication", "Product"],
    "technology": ["Engineering", "Data", "Product"],
    "risk & compliance": ["Risk", "Data", "Finance"],
    "operations": ["Operations", "Data", "Communication"],
    "corporate": ["Leadership", "Communication", "Product"],
}


def _name(rng: random.Random, used: set[str]) -> str:
    for _ in range(400):
        c = f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"
        if c not in used:
            used.add(c)
            return c
    base = f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"
    n = 1
    while f"{base} {n}" in used:
        n += 1
    used.add(f"{base} {n}")
    return f"{base} {n}"


def _email(full_name: str, token: str) -> str:
    handle = full_name.lower().replace(" ", ".")
    return f"{handle}.{token[-4:]}@example.com"


def build(seed: int = 7) -> None:
    crypto.ensure_local_key()  # dev convenience: mint a gitignored key if none set
    rng = random.Random(seed)
    conn = get_connection()
    init_db(conn)

    used_names: set[str] = set()
    # employee dict carries token + PII (PII is encrypted into `identities`, not employees)
    employees: list[dict] = []

    for i, name in enumerate(DIVISIONS, start=1):
        conn.execute("INSERT INTO divisions (id, name) VALUES (?, ?)", (i, name))

    for sid, (sname, family) in enumerate(SKILLS_CATALOG, start=1):
        conn.execute("INSERT INTO skills (id, name, family) VALUES (?,?,?)", (sid, sname, family))

    # Single org root: the CEO (corporate). Division heads report to the CEO, so the
    # whole company is one connected reporting tree and span_of_control is real
    # end-to-end (the Company Graph renders from this hierarchy).
    ceo = _make_emp(rng, "corporate", "Chief Executive Officer", level=6,
                    manager_token=None, used=used_names, is_ceo=True)
    employees.append(ceo)

    for division in DIVISIONS:
        roles = ROLES[division]
        headcount = DIVISION_HEADCOUNT[division]
        head = _make_emp(rng, division, roles[-1], level=5, manager_token=ceo["token"],
                         used=used_names, is_head=True)
        employees.append(head)

        n_managers = max(3, round(headcount / 14))
        manager_tokens = []
        for _ in range(n_managers):
            mgr = _make_emp(rng, division, rng.choice(roles[3:5]), level=4,
                            manager_token=head["token"], used=used_names)
            employees.append(mgr)
            manager_tokens.append(mgr["token"])

        teams = [f"{division.split()[0].title()} Team {chr(65 + k)}" for k in range(n_managers)]
        n_ic = max(0, headcount - 1 - n_managers)
        for _ in range(n_ic):
            idx = rng.randrange(n_managers)
            ic = _make_emp(rng, division, rng.choice(roles[:3]), level=rng.choice(IC_LEVEL_WEIGHTS),
                           manager_token=manager_tokens[idx], used=used_names, team=teams[idx])
            employees.append(ic)

    # Insert pseudonymous rows + encrypted identities.
    for e in employees:
        conn.execute(
            "INSERT INTO employees (token, role, level, division, team, manager_token, "
            "location, hire_date, employment_type) VALUES (?,?,?,?,?,?,?,?,?)",
            (e["token"], e["role"], e["level"], e["division"], e["team"], e["manager_token"],
             e["location"], e["hire_date"], e["employment_type"]),
        )
        store_identity(conn, e["token"], e["full_name"], _email(e["full_name"], e["token"]))

    # Count direct reports per manager token to drive span_of_control.
    reports: dict[str, int] = {}
    for e in employees:
        if e["manager_token"] is not None:
            reports[e["manager_token"]] = reports.get(e["manager_token"], 0) + 1
    # Token -> its manager's token, used to fill employee_core.skiplevel_token.
    mgr_of = {e["token"]: e["manager_token"] for e in employees}

    # Designate a synthetic leaver cohort (the FR training labels). Leavers are drawn
    # MOSTLY from the high-risk persona pool, so each leaver's pre-exit fields actually
    # explain the exit (a realistic, learnable label); a thinner slice of 'watch'
    # personas adds label noise. The CEO and division heads are retained.
    high_pool = [e for e in employees if e["persona"]["arch"] == "high"
                 and not e["is_head"] and not e["is_ceo"]]
    watch_pool = [e for e in employees if e["persona"]["arch"] == "watch"
                  and not e["is_head"] and not e["is_ceo"]]
    n_leavers = max(1, int(len(employees) * 0.12))
    leavers = rng.sample(high_pool, min(len(high_pool), int(n_leavers * 0.8)))
    remaining = n_leavers - len(leavers)
    if remaining > 0 and watch_pool:
        leavers += rng.sample(watch_pool, min(len(watch_pool), remaining))
    for e in leavers:
        e["is_leaver"] = True
        e["status"] = "terminated"

    # Guarantee a handful of HIGH-VALUE flight risks so the risk×value "act now"
    # quadrant — the moneyball case for retention spend — is genuinely populated.
    # Elevate the longest-tenure senior managers (+ one division head) to a STRONG
    # high-risk persona: the classic "underpaid, long-overdue-for-promotion star, just
    # had a second reorg, that we can't afford to lose". Strong anchors on all three
    # scored drivers so they clear the high band even far from the tenure cliff.
    retained_senior = [e for e in employees if not e["is_ceo"] and not e.get("is_leaver")
                       and e["persona"]["arch"] != "high"]
    l4_by_tenure = sorted([e for e in retained_senior if e["level"] == 4],
                          key=lambda e: e["hire_date"])[:5]
    one_head = [e for e in retained_senior if e["level"] >= 5][:1]
    for e in l4_by_tenure + one_head:
        p = _persona_for(rng, "high", allow_cliff=False)
        p.update(comp_gap=round(rng.uniform(0.30, 0.40), 3),
                 promo_months=rng.randint(42, 54), mgr_changes=2)
        e["persona"] = p

    for e in employees:
        _write_feature_history(conn, rng, e)
        _write_attribute_history(conn, rng, e, span=reports.get(e["token"], 0))
        _assign_skills(conn, rng, e)
        # Canonical core tables (Talent88 v2 §4 Tables 1-4 + §5 packs).
        skip = mgr_of.get(e["manager_token"]) if e["manager_token"] else None
        _write_employee_core(conn, e, skip)
        _write_compensation(conn, rng, e)
        _write_career_mobility(conn, rng, e)
        _write_performance(conn, rng, e)
        # Remaining 🟢 canonical tables (§4 Tables 5,6,9,11,13 + label 12).
        _write_skills_credentials(conn, rng, e)
        _write_engagement(conn, rng, e)
        _write_cv_trajectory(conn, rng, e)
        _write_lifecycle(conn, rng, e)
        _write_employee_ops(conn, rng, e)   # operational overlay (Team Pulse)
        _write_features_engineered(conn, rng, e)
        if e.get("is_leaver"):
            _write_label_attrition(conn, rng, e)
        # 🟡 consent/license-gated tables (§4 Tables 8,10) + the consent record (§6).
        _write_engagement_prefs(conn, rng, e)
        _write_collaboration_metadata(conn, rng, e)   # only if monitoring_consent
        _write_external_market(conn, rng, e)          # only if licensed region

    # manager_team (§4 Table 7) — one row per token that has direct reports. Team
    # churn signals are derived from who actually reports to each manager, so a
    # manager whose team contains leavers/high-risk reports shows real turnover and a
    # recent manager change (the "peers departed" retention signal).
    leavers_by_mgr: dict[str, int] = {}
    highrisk_by_mgr: dict[str, int] = {}
    for e in employees:
        mt = e["manager_token"]
        if mt is None:
            continue
        if e.get("is_leaver"):
            leavers_by_mgr[mt] = leavers_by_mgr.get(mt, 0) + 1
        if e["persona"]["arch"] == "high":
            highrisk_by_mgr[mt] = highrisk_by_mgr.get(mt, 0) + 1
    for token, span in reports.items():
        _write_manager_team(conn, rng, token, span,
                            peers_departed=leavers_by_mgr.get(token, 0),
                            team_high_risk=highrisk_by_mgr.get(token, 0))

    # Users: one admin + one manager (division head) per division.
    conn.execute(
        "INSERT INTO users (email, name, role, division, employee_token) VALUES (?,?,?,?,?)",
        ("admin@pulsescore.local", "Admin User", "admin", None, None),
    )
    for division in DIVISIONS:
        head = next(e for e in employees if e["division"] == division and e["is_head"])
        slug = division.split()[0].lower().replace("&", "and")
        conn.execute(
            "INSERT INTO users (email, name, role, division, employee_token) VALUES (?,?,?,?,?)",
            (f"{slug}.manager@pulsescore.local", head["full_name"], "manager",
             division, head["token"]),
        )
    _, password_note = _seed_operator_passwords(conn)

    # Survey module: seed the science-backed question library + templates (additive;
    # campaigns/responses are created at runtime). This is the reference content that
    # backs the survey builder; responses, when collected, write into `engagement`.
    lib = seed_survey_library(conn)
    # Showcase: a spread of demo campaigns + synthesized responses (the closed census
    # writes results back onto the latest engagement snapshot). Gated here so production
    # starts empty.
    demo = seed_demo_survey_activity(conn, rng, close_date=SNAPSHOT_DATES[-1])

    conn.commit()
    conn.close()
    print(f"Generated {len(employees)} employees across {len(DIVISIONS)} divisions "
          f"with {len(SNAPSHOT_DATES)} monthly snapshots (seed={seed}).")
    print(f"Seeded survey library: {lib['drivers']} drivers, {lib['items']} items, "
          f"{lib['templates']} templates; {demo['campaigns']} demo campaigns.")
    print(f"Database: {db_path()}  (PII encrypted at rest in `identities`)")
    print(f"Operator sign-in password: {password_note}")
    print("Next: run the score refresh (model_service) to populate scores + reason codes.")


def _seed_operator_passwords(conn) -> tuple[str, str]:
    """Give every seeded operator the same demo password.

    Uses PULSESCORE_DEMO_PASSWORD when set; otherwise mints a random one and writes it to
    a gitignored `.demo-password` file next to the database, so a local developer can
    find it and a deployment never ships a password that is public in the repo.
    Returns (password, a note saying where it came from).
    """
    password = os.environ.get("PULSESCORE_DEMO_PASSWORD")
    if password:
        note = "from PULSESCORE_DEMO_PASSWORD"
    else:
        password = secrets.token_urlsafe(12)
        path = db_path().with_name(".demo-password")
        path.write_text(password + "\n")
        os.chmod(path, 0o600)
        note = f"generated, saved to {path}"
    hashed = auth.hash_password(password)
    conn.execute("UPDATE users SET password_hash = ?", (hashed,))
    return password, note


def _birth_year_band(year: int) -> str:
    if year < 1970:
        return "<1970"
    if year < 1980:
        return "1970-1979"
    if year < 1990:
        return "1980-1989"
    if year < 2000:
        return "1990-1999"
    return "2000+"


def _assign_persona(rng: random.Random, level: int) -> dict:
    """Assign a coherent retention PERSONA before any fields are written.

    The persona is the single source of every risk-driver field downstream: the
    scoring-seam features (comp_gap / months_since_promotion / tenure / manager
    changes) AND the canonical narrative fields (comp_gap_vs_market, engagement
    trend, peers departed, …) are all derived from it. That is what makes a
    high-flight-risk employee actually *look* high-risk when you open their profile —
    the scorer is reading the same story the UI shows.

    Distribution skews realistic: most people healthy, a minority genuinely at risk.
    Leaders (level ≥ 4) are flagged high less often, but not never, so the
    risk×value "act now" quadrant has real occupants.
    """
    r = rng.random()
    if level >= 4:
        arch = "high" if r < 0.10 else ("watch" if r < 0.35 else "healthy")
    else:
        arch = "high" if r < 0.18 else ("watch" if r < 0.48 else "healthy")
    return _persona_for(rng, arch)


def _persona_for(rng: random.Random, arch: str, allow_cliff: bool = True) -> dict:
    """Build the persona field-anchors for one archetype. Split out so a few
    high-value 'stars' can be deliberately elevated to the high-risk persona (to
    populate the risk×value 'act now' quadrant) using the same coherent anchors."""
    if arch == "healthy":
        return {
            "arch": "healthy",
            "comp_gap": round(rng.uniform(0.0, 0.08), 3),
            "promo_months": rng.randint(2, 16),
            "mgr_changes": 0,
            "engage_base": rng.randint(72, 90),
            "engage_drift": rng.choice([0, 0, 1, 1, 2]),
            "perf_base": round(rng.uniform(3.8, 4.8), 2),
            "pct_hike": round(rng.uniform(9.0, 22.0), 1),
            "near_cliff": False,
        }
    if arch == "watch":
        return {
            "arch": "watch",
            "comp_gap": round(rng.uniform(0.10, 0.18), 3),
            "promo_months": rng.randint(18, 30),
            "mgr_changes": rng.choice([0, 0, 1]),
            "engage_base": rng.randint(54, 68),
            "engage_drift": rng.choice([-1, 0, 0]),
            "perf_base": round(rng.uniform(3.0, 3.9), 2),
            "pct_hike": round(rng.uniform(4.0, 10.0), 1),
            "near_cliff": False,
        }
    # high — strong, mutually-reinforcing drivers
    cliff = allow_cliff and rng.random() < 0.45
    return {
        "arch": "high",
        "comp_gap": round(rng.uniform(0.20, 0.35), 3),
        # cliff personas were never promoted since joining (~2yr tenure); the
        # stagnation flavor has a long overdue promotion instead.
        "promo_months": None if cliff else rng.randint(30, 48),
        "mgr_changes": rng.choice([1, 1, 2]),
        "engage_base": rng.randint(28, 48),
        "engage_drift": rng.choice([-3, -2, -1]),
        "perf_base": round(rng.uniform(2.6, 3.6), 2),
        "pct_hike": round(rng.uniform(2.0, 6.0), 1),
        "near_cliff": cliff,
    }


def _persona_hire_date(rng: random.Random, level: int, persona: dict) -> str:
    """Hire date coherent with the persona: tenure grows with seniority, but a
    'cliff' high-risk persona is deliberately placed ~2 years in (the classic
    underpaid-never-promoted-and-restless window)."""
    if persona.get("near_cliff"):
        return date(2024, rng.randint(1, 6), 1).isoformat()
    hy = 2025 - level * 2 - rng.randint(0, 4)
    hy = max(2009, min(2025, hy))
    return date(hy, rng.randint(1, 12), 1).isoformat()


def _make_emp(rng, division, role, level, manager_token, used, team=None,
              is_head=False, is_ceo=False) -> dict:
    # Synthetic age correlates loosely with level; only ever used to derive the
    # bias-audit-only birth_year_band (walled off from scoring).
    age = 26 + level * 3 + rng.randint(0, 10)
    persona = _assign_persona(rng, level)
    return {
        "token": new_token(rng),
        "full_name": _name(rng, used),
        "role": role,
        "title": role,
        "level": level,
        "division": division,
        "team": team or f"{division.split()[0].title()} Leadership",
        "manager_token": manager_token,
        "location": rng.choice(DIVISION_LOCATION_WEIGHTS.get(division, LOCATIONS)),
        "hire_date": _persona_hire_date(rng, level, persona),
        "employment_type": rng.choice(EMPLOYMENT_TYPES),
        "business_travel_frequency": rng.choice(canonical.BUSINESS_TRAVEL_FREQUENCIES),
        "status": "active",
        # 🟡 bias-audit ONLY — never a scoring input (read via data/bias_audit.py).
        "birth_year_band": _birth_year_band(2026 - age),
        "gender": rng.choice(canonical.GENDERS),
        "marital_status": rng.choice(canonical.MARITAL_STATUSES),
        "is_leaver": False,
        "is_head": is_head,
        "is_ceo": is_ceo,
        "persona": persona,
    }


def _write_feature_history(conn, rng, e) -> None:
    """Six monthly scoring-seam snapshots, derived from the employee's PERSONA so the
    flight-risk model reads exactly the story the profile shows. comp_gap, months-
    since-promotion and the manager-change count all come from the persona; tenure
    comes from the real hire date (so the ~2-year cliff is genuine for 'cliff'
    personas, who were never promoted since joining)."""
    p = e["persona"]
    hy, hm, _ = (int(x) for x in e["hire_date"].split("-"))
    base_tenure = max(1, (2026 - hy) * 12 + (1 - hm))
    promo0 = base_tenure if p["promo_months"] is None else min(p["promo_months"], base_tenure)
    manager_changes = p["mgr_changes"]
    comp_gap0 = p["comp_gap"]
    # High-risk comp gaps drift slightly WIDER across the six months (pressure
    # building); watch gaps inch up; healthy gaps stay flat and tiny.
    comp_drift = {"high": 0.004, "watch": 0.001}.get(p["arch"], 0.0)
    for i, d in enumerate(SNAPSHOT_DATES):
        cg = min(0.5, max(0.0, comp_gap0 + comp_drift * i + rng.uniform(-0.005, 0.005)))
        conn.execute(
            "INSERT INTO feature_snapshots (employee_token, as_of_date, comp_gap, "
            "months_since_promotion, tenure_months, manager_changes_12mo) VALUES (?,?,?,?,?,?)",
            (e["token"], d, round(cg, 3), promo0 + i, base_tenure + i, manager_changes),
        )


def _write_attribute_history(conn, rng, e, span: int) -> None:
    """Synthesize 360 attributes per snapshot, correlated with level/tenure/division.

    Higher levels rate higher and lead more; juniors learn more hours; engagement
    drifts month to month. ~12% of records withhold consent (consented_signals=0),
    which downstream gates the engagement/productivity score.
    """
    level = e["level"]
    p = e["persona"]
    hy, hm, _ = (int(x) for x in e["hire_date"].split("-"))
    tenure_years = max(0, 2026 - hy)
    # Performance + engagement come from the persona so they track flight risk: a
    # high-risk employee shows lower, declining engagement (a real driver), not a
    # random walk uncorrelated with their score.
    base_perf = p["perf_base"]
    base_engage = p["engage_base"]
    engage_drift = p["engage_drift"]
    learn_base = max(4, int(60 - 6 * level + rng.randint(-8, 12)))
    internal_moves = min(tenure_years, rng.randint(0, max(1, tenure_years // 2)))
    consented = 0 if rng.random() < 0.12 else 1
    for i, d in enumerate(SNAPSHOT_DATES):
        perf = round(min(5.0, max(1.0, base_perf + rng.uniform(-0.15, 0.15))), 2)
        engage = int(min(100, max(0, base_engage + engage_drift * i + rng.randint(-3, 3))))
        learn = max(0, learn_base + rng.randint(-3, 3))
        conn.execute(
            "INSERT INTO employee_attributes (employee_token, as_of_date, perf_rating, "
            "engagement_pulse, learning_hours_12mo, internal_moves, span_of_control, "
            "consented_signals) VALUES (?,?,?,?,?,?,?,?)",
            (e["token"], d, perf, engage, learn, internal_moves, span, consented),
        )


def _assign_skills(conn, rng, e) -> None:
    """Assign a division-relevant skill set; breadth + proficiency grow with level."""
    core_families = DIVISION_SKILL_FAMILIES[e["division"]]
    core = [s for s in SKILLS_CATALOG if s[1] in core_families]
    general = [s for s in SKILLS_CATALOG if s[1] in ("Communication", "Leadership")]
    n_skills = min(len(core), 2 + e["level"] + rng.randint(0, 2))
    chosen = rng.sample(core, min(n_skills, len(core)))
    if e["level"] >= 4:  # leaders also pick up leadership/comms breadth (adjacency)
        chosen += rng.sample(general, min(2, len(general)))
    name_to_id = {name: i for i, (name, _) in enumerate(SKILLS_CATALOG, start=1)}
    seen: set[int] = set()
    for name, _family in chosen:
        sid = name_to_id[name]
        if sid in seen:
            continue
        seen.add(sid)
        prof = min(5, max(1, round(e["level"] * 0.7 + rng.randint(-1, 2))))
        conn.execute(
            "INSERT INTO employee_skills (employee_token, skill_id, proficiency) VALUES (?,?,?)",
            (e["token"], sid, prof),
        )


def _hire_year(e) -> int:
    return int(e["hire_date"].split("-")[0])


def _write_employee_core(conn, e, skiplevel_token) -> None:
    """Canonical Table 1. Superset of the legacy `employees` row, including the
    bias-audit-only traits (walled off from scoring by data/canonical.py)."""
    conn.execute(
        "INSERT INTO employee_core (employee_token, role, title, level, division, team, "
        "manager_token, skiplevel_token, location, employment_type, "
        "business_travel_frequency, hire_date, status, birth_year_band, gender, "
        "marital_status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (e["token"], e["role"], e["title"], e["level"], e["division"], e["team"],
         e["manager_token"], skiplevel_token, e["location"], e["employment_type"],
         e["business_travel_frequency"], e["hire_date"], e["status"],
         e["birth_year_band"], e["gender"], e["marital_status"]),
    )


def _write_compensation(conn, rng, e) -> None:
    """Canonical Table 2. Local-currency figures from a USD-equivalent per-level band;
    comp_gap_vs_market is the 🟡 licensed-benchmark field (a modeled estimate here)."""
    level = e["level"]
    p = e["persona"]
    currency = CURRENCY_BY_LOCATION[e["location"]]
    # An underpaid (high comp_gap) persona earns BELOW the level band midpoint; a
    # healthy one sits at/above it — so base_salary itself reflects the gap that
    # drives flight risk.
    base_usd = LEVEL_BASE_USD[level] * (1 - p["comp_gap"]) * (1 + rng.uniform(-0.03, 0.05))
    base = round(base_usd * FX_FROM_USD[currency], -2)
    bonus_history = [
        {"date": "2024-12-15", "amount": round(base * rng.uniform(0.05, 0.35))},
        {"date": "2025-12-15", "amount": round(base * rng.uniform(0.05, 0.40))},
    ]
    equity_deferred = {"granted_usd": round(base_usd * rng.uniform(0.0, 1.5)),
                       "unvested_usd": round(base_usd * rng.uniform(0.0, 0.8))}
    vesting_dates = ["2026-09-01", "2027-03-01", "2027-09-01"]
    # comp_gap_vs_market mirrors the scoring feature; raise size + recency track the
    # persona (high-risk = small, stale raise; healthy = recent, healthy raise);
    # pay percentile falls as the gap widens.
    comp_gap_vs_market = round(p["comp_gap"] + rng.uniform(-0.01, 0.01), 3)
    last_raise = f"{2024 if p['arch'] == 'high' else 2025}-{rng.randint(1, 12):02d}-01"
    pay_pct = round(max(0.05, min(0.95, 0.62 - p["comp_gap"] * 1.2 + rng.uniform(-0.05, 0.05))), 2)
    conn.execute(
        "INSERT INTO compensation (employee_token, base_salary, currency, bonus_history, "
        "equity_deferred, vesting_dates, last_raise_date, percent_salary_hike, pay_band, "
        "pay_percentile_in_role, comp_gap_vs_market) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (e["token"], base, currency, json.dumps(bonus_history), json.dumps(equity_deferred),
         json.dumps(vesting_dates), last_raise, p["pct_hike"], f"L{level}",
         pay_pct, comp_gap_vs_market),
    )


def _write_career_mobility(conn, rng, e) -> None:
    """Canonical Table 3. time_since_last_promotion mirrors the scoring feature, and
    promotion_velocity is low for the (stagnating) high-risk persona — so the career
    panel agrees with the retention score."""
    p = e["persona"]
    hy, hm, _ = (int(x) for x in e["hire_date"].split("-"))
    base_tenure_m = max(1, (2026 - hy) * 12 + (1 - hm))
    tslp = base_tenure_m if p["promo_months"] is None else min(p["promo_months"], base_tenure_m)
    tenure_years = max(0.0, 2026 - hy)
    tenure = round(tenure_years + rng.uniform(0.0, 0.9), 1)
    years_in_role = round(min(tenure, rng.uniform(0.5, max(0.6, tenure))), 1)
    velocity = round(rng.uniform(0.2, 0.6), 2) if p["arch"] == "high" \
        else round(rng.uniform(0.8, 1.5), 2)
    # 'cliff' personas were never promoted since joining -> no promotion record.
    promoted = e["level"] > 1 and p["promo_months"] is not None
    promotions = [{"date": f"{2026 - rng.randint(1, 6)}-06-01",
                   "from": f"L{max(1, e['level'] - 1)}", "to": f"L{e['level']}"}] \
        if promoted else []
    internal_moves = [{"date": f"{2026 - rng.randint(1, 5)}-03-01", "team": e["team"]}] \
        if rng.random() < 0.4 else []
    internal_apps = [{"date": "2026-02-01", "role": "internal posting"}] \
        if rng.random() < 0.15 else []
    conn.execute(
        "INSERT INTO career_mobility (employee_token, promotions, time_since_last_promotion, "
        "promotion_velocity, years_in_current_role, total_working_years, internal_moves, "
        "internal_application_history, tenure, tenure_at_exit) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (e["token"], json.dumps(promotions), tslp, velocity, years_in_role,
         round(tenure + rng.uniform(0.0, 12.0), 1), json.dumps(internal_moves),
         json.dumps(internal_apps), tenure, tenure if e.get("is_leaver") else None),
    )


def _write_performance(conn, rng, e) -> None:
    """Canonical Table 4 (core) + §5 division signal pack rows."""
    base = min(5.0, 2.6 + 0.3 * e["level"])
    ratings = [{"period": "2024", "score": round(min(5.0, base + rng.uniform(-0.6, 0.4)), 1)},
               {"period": "2025", "score": round(min(5.0, base + rng.uniform(-0.4, 0.5)), 1)}]
    conn.execute(
        "INSERT INTO performance (employee_token, review_ratings, goal_attainment, "
        "performance_trend) VALUES (?,?,?,?)",
        (e["token"], json.dumps(ratings), round(rng.uniform(0.6, 1.2), 2),
         round(rng.uniform(-0.3, 0.4), 2)),
    )
    for signal, unit, lo, hi in DIVISION_SIGNAL_PACKS.get(e["division"], []):
        conn.execute(
            "INSERT INTO performance_division_signals (employee_token, division, signal, "
            "value, unit) VALUES (?,?,?,?,?)",
            (e["token"], e["division"], signal, round(rng.uniform(lo, hi), 2), unit),
        )


_LICENSES_BY_FAMILY = {
    "Finance": ["Series 7", "Series 63", "CFA"],
    "Risk": ["FRM", "Series 99"],
}


def _write_skills_credentials(conn, rng, e) -> None:
    """Canonical Table 5. Reads back the token's assigned skills (written by
    _assign_skills) so the credential record matches the skills graph."""
    rows = conn.execute(
        "SELECT s.name, s.family, es.proficiency FROM employee_skills es "
        "JOIN skills s ON s.id = es.skill_id WHERE es.employee_token = ?",
        (e["token"],)).fetchall()
    skills = [r["name"] for r in rows]
    proficiency = {r["name"]: r["proficiency"] for r in rows}
    acquisition = {r["name"]: f"{rng.randint(2016, 2025)}-{rng.randint(1, 12):02d}-01"
                   for r in rows}
    role_reqs = {"required": skills[:3], "recommended": skills[3:]}
    families = {r["family"] for r in rows}
    licenses = []
    for fam in families:
        for lic in _LICENSES_BY_FAMILY.get(fam, []):
            if rng.random() < 0.5:
                licenses.append({"name": lic, "expiry": f"{rng.randint(2026, 2029)}-12-31"})
    certs = [f"{fam} Certificate" for fam in families if rng.random() < 0.3]
    training = [{"course": f"{fam} Fundamentals", "year": 2025} for fam in list(families)[:2]]
    conn.execute(
        "INSERT INTO skills_credentials (employee_token, skills, skill_proficiency_level, "
        "skill_acquisition_date, role_skill_requirements, education_level, education_field, "
        "certifications, licenses, training_completed, training_times_last_year) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (e["token"], json.dumps(skills), json.dumps(proficiency), json.dumps(acquisition),
         json.dumps(role_reqs), rng.choice(canonical.EDUCATION_LEVELS),
         rng.choice(canonical.EDUCATION_FIELDS), json.dumps(certs), json.dumps(licenses),
         json.dumps(training), rng.randint(0, 6)),
    )


def _write_engagement(conn, rng, e) -> None:
    """Canonical Table 6 (snapshotted). Synthesizes the survey-platform outputs the
    ingestion adapter would normally carry; trend drifts across the 6 months."""
    p = e["persona"]
    base = p["engage_base"]
    drift = p["engage_drift"]
    # eNPS, satisfaction facets, sentiment and manager-effectiveness all track the
    # persona, so the survey panel agrees with the engagement-driven risk signal.
    enps_base = int((base - 55) * 1.4)
    sentiment_base = round((base - 55) / 45.0, 2)

    def _facet() -> int:
        if p["arch"] == "high":
            return rng.randint(1, 3)
        if p["arch"] == "watch":
            return rng.randint(2, 4)
        return rng.randint(3, 5)

    mgr_eff = round(rng.uniform(2.0, 3.2), 1) if p["arch"] == "high" \
        else round(rng.uniform(3.3, 5.0), 1)
    for i, d in enumerate(SNAPSHOT_DATES):
        score = float(min(100, max(0, base + drift * i + rng.randint(-3, 3))))
        enps = int(max(-100, min(100, enps_base + drift * i * 2 + rng.randint(-5, 5))))
        drivers = {"recognition": round(rng.uniform(2, 5), 1),
                   "growth": round(rng.uniform(2, 5), 1),
                   "manager": round(rng.uniform(2, 5), 1)}
        q12 = {f"q{n}": _facet() for n in range(1, 13)}
        themes = rng.sample(["workload", "career growth", "recognition", "leadership",
                             "tooling"], k=2)
        conn.execute(
            "INSERT INTO engagement (employee_token, as_of_date, engagement_score, "
            "engagement_trend, enps_score, enps_trend, pulse_survey_score, job_satisfaction, "
            "environment_satisfaction, relationship_satisfaction, job_involvement, "
            "work_life_balance, engagement_driver_scores, q12_scores, survey_text_sentiment, "
            "survey_open_text_themes, survey_response_rate, survey_date, recognition_count, "
            "manager_effectiveness_survey_score) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (e["token"], d, score, round(float(drift), 2), enps,
             round(float(drift), 1), round(score / 20.0, 2), _facet(),
             _facet(), _facet(), _facet(), _facet(),
             json.dumps(drivers), json.dumps(q12),
             round(max(-1.0, min(1.0, sentiment_base + rng.uniform(-0.15, 0.15))), 2),
             json.dumps(themes), round(rng.uniform(0.5, 1.0), 2), d, rng.randint(0, 12),
             mgr_eff),
        )


def _write_cv_trajectory(conn, rng, e) -> None:
    """Canonical Table 9. school_prestige / named_employer are 🟡 identity proxies —
    stored for skills/search, never read into a score (enforced by the scoring wall)."""
    exp = max(1, 2026 - _hire_year(e) + rng.randint(0, 12))
    conn.execute(
        "INSERT INTO cv_trajectory (employee_token, prior_employer_count, median_prior_tenure, "
        "job_hopping_rate, tenure_curve_shape, career_velocity, skill_adjacency_to_role, "
        "boomerang_flag, years_of_experience, school_prestige, named_employer) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (e["token"], rng.randint(0, 6), round(rng.uniform(1.0, 6.0), 1),
         round(rng.uniform(0.0, 1.0), 2), rng.choice(canonical.TENURE_CURVE_SHAPES),
         round(rng.uniform(0.3, 1.6), 2), round(rng.uniform(0.2, 1.0), 2),
         1 if rng.random() < 0.05 else 0, exp,
         rng.choice(["tier_1", "tier_2", "tier_3", "unknown"]),
         rng.choice(["BigBank", "FinCorp", "TechCo", "Boutique", "unknown"])),
    )


def _write_lifecycle(conn, rng, e) -> None:
    """Canonical Table 11 (snapshotted)."""
    onboarding = "complete" if (2026 - _hire_year(e)) >= 1 else \
        rng.choice(["in_progress", "complete"])
    for d in SNAPSHOT_DATES:
        absence = {"sick_days": rng.randint(0, 6), "unplanned": rng.randint(0, 3)}
        conn.execute(
            "INSERT INTO lifecycle (employee_token, as_of_date, pto_used, absence_pattern, "
            "overtime_flag, distance_from_home, onboarding_status) VALUES (?,?,?,?,?,?,?)",
            (e["token"], d, round(rng.uniform(0.0, 25.0), 1), json.dumps(absence),
             round(rng.uniform(0.0, 1.0), 2), round(rng.uniform(1.0, 60.0), 1), onboarding),
        )


def _write_employee_ops(conn, rng, e) -> None:
    """Operational status overlay (Team Pulse). NEUTRAL signals only — birthdays, work
    anniversaries (the anniversary comes from the real hire_date, not stored here),
    availability, who's away + when they return, onboarding, upcoming role changes, and
    expiring credentials.

    PRIVACY: leave is a NEUTRAL flag + an expected return date. The generator NEVER
    produces or stores a reason, diagnosis, or any health detail — the schema has no
    column for one. A manager sees that someone is away and when they're back, never why.

    Dates are anchored around the synthetic "now" (mid-2026, matching the monthly
    snapshots) so the panel is demonstrably populated; all of it is deterministic by seed.
    """
    # Birthday as a bare MM-DD (no year — age lives only in the bias-audit band).
    birthday_md = f"{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"

    availability = rng.choices(["in_office", "remote", "out"], weights=[6, 5, 1])[0]

    # ~8% currently on short PTO, returning within the next few weeks of the synthetic now.
    pto_return = None
    if rng.random() < 0.08:
        pto_return = date(2026, 6, rng.randint(12, 28)).isoformat()
        availability = "out"

    # ~3% on extended leave (neutral) with an expected return later in the year.
    on_leave = 0
    leave_return = None
    if rng.random() < 0.03:
        on_leave = 1
        availability = "out"
        leave_return = date(2026, rng.randint(8, 11), rng.randint(1, 28)).isoformat()

    # Onboarding: a small new-joiner cohort is still in progress; everyone else complete.
    onboarding = "in_progress" if rng.random() < 0.05 else "complete"

    # ~6% have a known upcoming role/manager change in the next month (neutral note).
    role_note = None
    role_date = None
    if rng.random() < 0.06:
        role_note = rng.choice(["Promotion pending", "New manager", "Internal transfer",
                                "Role change"])
        role_date = date(2026, rng.choice([6, 7]), rng.randint(12, 28)).isoformat()

    # ~25% carry a credential/permit; expiries spread across 2026 (some imminent).
    cred_name = None
    cred_expiry = None
    if rng.random() < 0.25:
        cred_name = rng.choice(["Series 7", "Series 63", "CFA renewal", "Work permit",
                                "AML certification", "Compliance attestation"])
        cred_expiry = date(2026, rng.randint(6, 12), rng.randint(1, 28)).isoformat()

    conn.execute(
        "INSERT INTO employee_ops (employee_token, birthday_md, availability, "
        "pto_return_date, on_extended_leave, leave_return_date, onboarding_status, "
        "role_change_note, role_change_date, credential_name, credential_expiry) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (e["token"], birthday_md, availability, pto_return, on_leave, leave_return,
         onboarding, role_note, role_date, cred_name, cred_expiry),
    )


def _write_features_engineered(conn, rng, e) -> None:
    """Canonical Table 13 (gold). One row per monthly snapshot — history mandatory."""
    uniqueness = round(rng.uniform(0.0, 1.0), 3)
    for d in SNAPSHOT_DATES:
        conn.execute(
            "INSERT INTO features_engineered (employee_token, score_snapshot_date, "
            "tenure_cliff_proximity, skill_uniqueness, comp_gap_normalized) VALUES (?,?,?,?,?)",
            (e["token"], d, round(rng.uniform(0.0, 1.0), 3), uniqueness,
             round(rng.uniform(-1.5, 1.5), 3)),
        )


def _write_manager_team(conn, rng, manager_token, span, peers_departed=0,
                        team_high_risk=0) -> None:
    """Canonical Table 7 — keyed on the manager. Turnover and recency-of-change track
    the manager's actual team: a churning team shows higher turnover and a more recent
    manager change (a genuine peers-departed retention signal for the remaining team)."""
    turnover = round(min(0.6, peers_departed / max(1, span) + rng.uniform(0.0, 0.1)), 2)
    churning = peers_departed > 0 or team_high_risk > 0
    time_since_change = rng.randint(1, 8) if churning else rng.randint(12, 60)
    conn.execute(
        "INSERT INTO manager_team (manager_token, manager_tenure, manager_performance_rating, "
        "span_of_control, manager_team_turnover_rate, team_turnover_rate, "
        "time_since_manager_change, peers_departed_recently) VALUES (?,?,?,?,?,?,?,?)",
        (manager_token, round(rng.uniform(1.0, 15.0), 1), round(rng.uniform(2.5, 5.0), 1),
         span, turnover, turnover, time_since_change, min(3, peers_departed)),
    )


def _write_engagement_prefs(conn, rng, e) -> None:
    """§6 placeholder that IS populated: it carries monitoring_consent, the gate for
    the 🟡 collaboration_metadata table. ~60% of employees consent to ONA metadata."""
    consent = 1 if rng.random() < 0.6 else 0
    e["monitoring_consent"] = consent
    conn.execute(
        "INSERT INTO engagement_prefs (employee_token, monitoring_consent, messaging_consent, "
        "channel_prefs, recommendation_history) VALUES (?,?,?,?,?)",
        (e["token"], consent, 1 if rng.random() < 0.5 else 0,
         json.dumps({"in_app": True, "email": False}), json.dumps([])),
    )


def _write_collaboration_metadata(conn, rng, e) -> None:
    """Canonical Table 8 (🟡 ONA, CONSENT-GATED). NOTHING is collected unless the
    employee consented — no row is written for non-consenting tokens (no covert
    monitoring). Metadata only, never content."""
    if e.get("monitoring_consent") != 1:
        return
    for d in SNAPSHOT_DATES:
        peers = {f"peer_{rng.randint(1, 40)}": rng.randint(1, 30) for _ in range(rng.randint(2, 6))}
        conn.execute(
            "INSERT INTO collaboration_metadata (employee_token, as_of_date, "
            "interaction_frequency, network_size, network_trend, betweenness_centrality, "
            "pagerank_centrality, bridge_score, cross_division_reach, meeting_load, "
            "focus_time_fragmentation, after_hours_activity, response_latency) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (e["token"], d, json.dumps(peers), len(peers), round(rng.uniform(-1, 1), 2),
             round(rng.uniform(0, 1), 3), round(rng.uniform(0, 1), 3), round(rng.uniform(0, 1), 3),
             round(rng.uniform(0, 1), 2), round(rng.uniform(0, 40), 1), round(rng.uniform(0, 1), 2),
             round(rng.uniform(0, 1), 2), round(rng.uniform(0.1, 8.0), 1)),
        )


def _write_external_market(conn, rng, e) -> None:
    """Canonical Table 10 (🟡 license-only). Populated only for tokens whose location
    the market-data license covers — so the source-flag gate is demonstrable."""
    if e["location"] not in LICENSED_MARKET_REGIONS:
        return
    for d in SNAPSHOT_DATES:
        conn.execute(
            "INSERT INTO external_market (employee_token, as_of_date, skill_market_demand, "
            "role_comp_benchmark, regional_comp_movement, industry_hiring_heat) "
            "VALUES (?,?,?,?,?,?)",
            (e["token"], d, round(rng.uniform(0, 1), 2), round(rng.uniform(0.7, 1.3), 2),
             round(rng.uniform(-0.05, 0.08), 3), round(rng.uniform(0, 1), 2)),
        )


def _write_label_attrition(conn, rng, e) -> None:
    """Canonical Table 12 — leavers only. The FR training label, made consistent with
    the leaver's pre-exit persona: the dominant risk driver becomes the exit reason,
    and a strong/senior performer who still left is flagged 'regretted'."""
    p = e["persona"]
    voluntary = 1 if rng.random() < 0.85 else 0
    regretted = 1 if (voluntary and (e["level"] >= 3 or p["perf_base"] >= 3.6)) else 0
    if p["comp_gap"] >= 0.22:
        reason = "compensation"
    elif p["promo_months"] is None or (p["promo_months"] or 0) >= 30:
        reason = "career_growth"
    elif p["mgr_changes"] >= 1:
        reason = "management"
    else:
        reason = rng.choice(canonical.LEAVER_REASONS)
    conn.execute(
        "INSERT INTO label_attrition (employee_token, attrition, term_date, voluntary, "
        "regretted, leaver_reason, leaver_destination) VALUES (?,?,?,?,?,?,?)",
        (e["token"], 1, f"{rng.randint(2024, 2026)}-{rng.randint(1, 12):02d}-15", voluntary,
         regretted, reason, rng.choice(canonical.LEAVER_DESTINATIONS)),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate synthetic PulseScore data.")
    parser.add_argument("--seed", type=int, default=7, help="random seed (reseed/reset)")
    build(seed=parser.parse_args().seed)


if __name__ == "__main__":
    main()
