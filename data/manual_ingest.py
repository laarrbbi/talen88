"""Manual / CV-assisted employee creation — the REAL document-import write path.

Unlike `data.ingestion` (which ENRICHES already-known employees from an HRIS export and
review-queues anyone it cannot match), this module MINTS a brand-new employee from fields a
person typed or confirmed off a CV. It is the backend the Document Import "Add employee"
surface calls, and it lands a complete, real record across every table the rest of the app
reads:

  * `identities`     — encrypted name + email + optional profile photo (the sole PII store);
  * `employees`      — the pseudonymous base row the Employees list / Watchlist read;
  * `employee_core`  — the canonical GOLD row Profile + Analytics read (title, status, plus the
                       bias-audit demographics gender / birth_year_band / marital_status);
  * `compensation`   — base salary + currency (+ optional bonus / equity / pay band / last raise).

Everything beyond the required core is optional and, when supplied, also lands in the right
canonical table: `skills_credentials` (education, university, skills, certifications, licenses,
languages, hobbies), `cv_trajectory` + `career_mobility` (experience / tenure), `performance`
(rating / goal attainment), `lifecycle` (onboarding / commute), and `employee_ops` (birthday +
expiring credential for the Team-Pulse panel). A field with no value writes no row/column.

A new employee therefore appears immediately in the Employees list, their Profile, and the
headcount/compensation analytics. They carry NO score until the scoring refresh runs (the
Watchlist honestly shows "no score" — nothing is fabricated).

DIVISIONS ARE DATA-DRIVEN: a company adds employees in its own divisions (Engineering,
Sales, …). New names are registered in the `divisions` table and the legacy DB's
`employee_core.division` CHECK is relaxed once, idempotently, by `ensure_dynamic_divisions`.

Writes are admin-only and audited. Controlled-vocab fields (employment_type, status,
currency, business_travel_frequency) are alias-folded + validated by `data.ingestion.normalize`
BEFORE the DB, so a bad value fails loudly with a clear message instead of a raw IntegrityError.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, datetime
from typing import Any

from . import canonical
from .audit import write_audit
from .auth import Actor
from .db import SCHEMA_CANONICAL_PATH
from .identity import new_token, store_identity
from .ingestion import normalize
from .security import crypto


class ManualIngestError(ValueError):
    """Bad manual-import input (missing field, bad vocab, duplicate). Maps to HTTP 400."""


# --- division registry + one-time CHECK relaxation ----------------------------------

def _employee_core_ddl() -> str:
    """Pull the (CHECK-free) `CREATE TABLE employee_core (...)` statement from the canonical
    schema file — the single source of truth — so the migration target never drifts."""
    text = SCHEMA_CANONICAL_PATH.read_text()
    m = re.search(r"CREATE TABLE employee_core\b.*?\n\);", text, re.DOTALL)
    if m is None:  # pragma: no cover - schema is committed alongside this module
        raise RuntimeError("could not locate CREATE TABLE employee_core in canonical schema")
    return m.group(0)


def ensure_dynamic_divisions(conn: sqlite3.Connection) -> bool:
    """Idempotently relax a legacy DB's `employee_core.division` CHECK so custom divisions
    can be written. Returns True if a migration actually ran.

    Safe by construction: `employee_core` has NO inbound foreign keys (every other table
    references `employees(token)`, not this table), so the standard SQLite recreate-and-copy
    preserves all data and relationships. A no-op once the live CHECK is gone or for fresh
    DBs built from the already-relaxed schema.
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='employee_core'"
    ).fetchone()
    if row is None or "CHECK (division IN" not in (row[0] or ""):
        return False  # fresh/already-dynamic schema — nothing to do

    cols = [r["name"] for r in conn.execute("PRAGMA table_info(employee_core)")]
    collist = ", ".join(cols)
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        conn.execute("ALTER TABLE employee_core RENAME TO _employee_core_legacy_div")
        conn.execute(_employee_core_ddl())  # recreate without the division CHECK
        conn.execute(
            f"INSERT INTO employee_core ({collist}) "
            f"SELECT {collist} FROM _employee_core_legacy_div"
        )
        conn.execute("DROP TABLE _employee_core_legacy_div")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_core_division ON employee_core(division)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_core_manager ON employee_core(manager_token)")
        conn.commit()
    finally:
        conn.execute("PRAGMA foreign_keys=ON")
    return True


# Columns added after the initial schema shipped; ALTER-added idempotently on legacy DBs.
_EXTRA_COLUMNS = {
    "identities": [("photo_enc", "BLOB")],
    "skills_credentials": [("education_institution", "TEXT"), ("languages", "TEXT"),
                           ("hobbies", "TEXT")],
}


def ensure_extra_columns(conn: sqlite3.Connection) -> bool:
    """Idempotently ADD COLUMN the post-launch columns (profile photo, university, languages,
    hobbies) on a legacy DB. SQLite ADD COLUMN is cheap + non-rewriting; a no-op once present."""
    changed = False
    for table, cols in _EXTRA_COLUMNS.items():
        have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, decl in cols:
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
                changed = True
    if changed:
        conn.commit()
    return changed


def _birth_year_band(year: int) -> str:
    """Map a birth year to the bias-audit band (employee_core.birth_year_band vocab)."""
    if year < 1970:
        return "<1970"
    if year < 1980:
        return "1970-1979"
    if year < 1990:
        return "1980-1989"
    if year < 2000:
        return "1990-1999"
    return "2000+"


def register_division(conn: sqlite3.Connection, name: str) -> None:
    """Add a division to the registry if new (idempotent). The `divisions` table backs the
    division filter dropdowns across the app."""
    conn.execute("INSERT OR IGNORE INTO divisions (name) VALUES (?)", (name,))


def list_divisions(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Registered divisions with current headcount, for the add-employee picker."""
    counts = {
        r["division"]: r["n"]
        for r in conn.execute(
            "SELECT division, COUNT(*) AS n FROM employees GROUP BY division"
        )
    }
    rows = conn.execute("SELECT name FROM divisions ORDER BY name").fetchall()
    return [{"name": r["name"], "headcount": counts.get(r["name"], 0)} for r in rows]


# --- duplicate guard ----------------------------------------------------------------

def find_token_by_email(conn: sqlite3.Connection, email: str) -> str | None:
    """Return the token whose stored identity email matches (case-insensitive), or None.

    Emails are Fernet-encrypted, so the compare is done in Python over decrypted values
    (the same boundary as resolve_names) — never as SQL on ciphertext."""
    needle = email.strip().lower()
    if not needle:
        return None
    for r in conn.execute("SELECT token, email_enc FROM identities"):
        if crypto.decrypt(r["email_enc"]).strip().lower() == needle:
            return r["token"]
    return None


# --- field validation / normalization -----------------------------------------------

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _require(value: Any, field: str) -> str:
    text = "" if value is None else str(value).strip()
    if not text:
        raise ManualIngestError(f"{field} is required")
    return text


def _clean(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate + normalize the raw form payload into exactly the columns each table needs.
    Raises ManualIngestError (HTTP 400) with a human message on the first problem."""
    full_name = _require(payload.get("full_name"), "full_name")
    email = _require(payload.get("email"), "email").lower()
    if not _EMAIL_RE.match(email):
        raise ManualIngestError(f"{email!r} is not a valid email address")

    role = _require(payload.get("role"), "role")
    title = (payload.get("title") or "").strip() or role
    division = _require(payload.get("division"), "division")
    team = (payload.get("team") or "").strip() or division
    location = _require(payload.get("location"), "location")

    try:
        level = normalize.normalize_level(_require(payload.get("level"), "level"))
    except ValueError as exc:
        raise ManualIngestError(str(exc)) from exc

    try:
        hire_date = normalize.to_iso_date(_require(payload.get("hire_date"), "hire_date"))
    except ValueError as exc:
        raise ManualIngestError(f"hire_date: {exc}") from exc

    try:
        employment_type = normalize.normalize_enum(
            "employment_type", _require(payload.get("employment_type"), "employment_type")
        )
        status = normalize.normalize_enum("status", payload.get("status") or "active")
        travel = payload.get("business_travel_frequency")
        business_travel = normalize.normalize_enum("business_travel_frequency", travel) if travel else None
    except ValueError as exc:
        raise ManualIngestError(str(exc)) from exc

    currency = _require(payload.get("currency"), "currency").upper()
    if currency not in canonical.CURRENCIES:
        raise ManualIngestError(
            f"currency {currency!r} not supported; one of {', '.join(canonical.CURRENCIES)}"
        )
    raw_salary = payload.get("base_salary")
    try:
        base_salary = float(raw_salary)
    except (TypeError, ValueError) as exc:
        raise ManualIngestError("base_salary must be a number") from exc
    if base_salary <= 0:
        raise ManualIngestError("base_salary must be greater than 0")

    # ---- optional groups (all skippable — a profile saves with whatever is supplied) ----
    def opt_num(key: str, label: str, lo: float | None = None, hi: float | None = None) -> float | None:
        raw = payload.get(key)
        if raw in (None, ""):
            return None
        try:
            v = float(raw)
        except (TypeError, ValueError):
            raise ManualIngestError(f"{label} must be a number")
        if lo is not None and v < lo:
            raise ManualIngestError(f"{label} must be at least {lo:g}")
        if hi is not None and v > hi:
            raise ManualIngestError(f"{label} must be at most {hi:g}")
        return v

    def opt_str(key: str) -> str | None:
        v = str(payload.get(key) or "").strip()
        return v or None

    def opt_date(key: str, label: str) -> str | None:
        raw = payload.get(key)
        if raw in (None, ""):
            return None
        try:
            return normalize.to_iso_date(raw)
        except ValueError as exc:
            raise ManualIngestError(f"{label}: {exc}") from exc

    def opt_vocab(key: str, field: str, label: str) -> str | None:
        raw = payload.get(key)
        if raw in (None, ""):
            return None
        val = str(raw).strip().lower()
        try:
            canonical.validate_enum(field, val)
        except ValueError as exc:
            raise ManualIngestError(
                f"{label} must be one of: {', '.join(canonical.ENUMS[field])}") from exc
        return val

    def opt_list(key: str) -> list[str] | None:
        raw = payload.get(key)
        if raw in (None, "", []):
            return None
        if isinstance(raw, str):
            items = [x.strip() for x in raw.split(",")]
        elif isinstance(raw, (list, tuple)):
            items = [str(x).strip() for x in raw]
        else:
            return None
        items = [x for x in items if x]
        return items or None

    bonus = opt_num("bonus", "bonus", 0)
    perf_rating = opt_num("performance_rating", "performance rating", 0, 5)
    goal = opt_num("goal_attainment", "goal attainment", 0, 3)
    yoe = opt_num("years_of_experience", "years of experience", 0, 70)
    prior = opt_num("prior_employer_count", "prior employers", 0, 60)
    total_years = opt_num("total_working_years", "total working years", 0, 70)
    months_since_promo = opt_num("time_since_last_promotion", "months since last promotion", 0, 600)
    commute = opt_num("distance_from_home", "commute distance", 0, 30000)

    # Demographics — bias-audit only, never scored (employee_core walled columns + the
    # Team-Pulse birthday). A full birth date sets both the (privacy-preserving) year band
    # and the MM-DD birthday; the year itself is never stored.
    gender = opt_vocab("gender", "gender", "gender")
    marital_status = opt_vocab("marital_status", "marital_status", "marital status")
    birth_year_band = opt_vocab("birth_year_band", "birth_year_band", "birth-year band")
    birthday_md = None
    birth_date = opt_date("birth_date", "birth date")
    if birth_date:
        y, mth, d = (int(p) for p in birth_date.split("-"))
        birthday_md = f"{mth:02d}-{d:02d}"
        if birth_year_band is None:
            birth_year_band = _birth_year_band(y)

    # Education & skills
    education_level = opt_vocab("education_level", "education_level", "education level")
    education_field = opt_vocab("education_field", "education_field", "field of study")
    education_institution = opt_str("education_institution")
    skills = opt_list("skills")
    certifications = opt_list("certifications")
    licenses = opt_list("licenses")
    languages = opt_list("languages")
    hobbies = opt_list("hobbies")

    # Lifecycle / compliance
    onboarding_status = opt_vocab("onboarding_status", "onboarding_status", "onboarding status")
    credential_name = opt_str("credential_name")
    credential_expiry = opt_date("credential_expiry", "credential expiry")

    # Compensation extras
    equity = opt_str("equity")
    pay_band = opt_str("pay_band")
    last_raise_date = opt_date("last_raise_date", "last raise date")

    # Profile photo — a small encrypted data-URL thumbnail (PII).
    photo = payload.get("photo")
    if photo:
        photo = str(photo)
        if not photo.startswith("data:image/"):
            raise ManualIngestError("photo must be an image")
        if len(photo) > 1_200_000:
            raise ManualIngestError("photo is too large — please use a smaller image")
    else:
        photo = None

    return {
        "full_name": full_name, "email": email, "role": role, "title": title,
        "level": level, "division": division, "team": team, "location": location,
        "hire_date": hire_date, "employment_type": employment_type, "status": status,
        "business_travel_frequency": business_travel,
        "currency": currency, "base_salary": round(base_salary, 2),
        "manager_token": (payload.get("manager_token") or "").strip() or None,
        "photo": photo,
        # compensation extras
        "bonus": bonus, "equity": equity, "pay_band": pay_band, "last_raise_date": last_raise_date,
        # work history / experience
        "years_of_experience": int(yoe) if yoe is not None else None,
        "prior_employer_count": int(prior) if prior is not None else None,
        "total_working_years": total_years,
        "time_since_last_promotion": int(months_since_promo) if months_since_promo is not None else None,
        # performance
        "performance_rating": perf_rating, "goal_attainment": goal,
        # demographics (bias-audit only)
        "gender": gender, "marital_status": marital_status,
        "birth_year_band": birth_year_band, "birthday_md": birthday_md,
        # education & skills
        "education_level": education_level, "education_field": education_field,
        "education_institution": education_institution,
        "skills": skills, "certifications": certifications, "licenses": licenses,
        "languages": languages, "hobbies": hobbies,
        # lifecycle / compliance
        "onboarding_status": onboarding_status, "credential_name": credential_name,
        "credential_expiry": credential_expiry, "distance_from_home": commute,
    }


# --- the create path ----------------------------------------------------------------

def _insert(conn: sqlite3.Connection, table: str, cols: dict[str, Any]) -> None:
    """Parameterized INSERT of the given columns (used for the optional canonical rows)."""
    names = list(cols)
    conn.execute(
        f"INSERT INTO {table} ({', '.join(names)}) VALUES ({', '.join('?' * len(names))})",
        tuple(cols[n] for n in names),
    )


def _years_since(iso_date: str) -> float | None:
    """Whole-and-fraction years between an ISO hire date and today (for tenure)."""
    try:
        d = datetime.strptime(iso_date, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None
    return round((date.today() - d).days / 365.25, 1)


def _current_period() -> str:
    return str(date.today().year)


def create_employee(conn: sqlite3.Connection, *, actor: Actor,
                    payload: dict[str, Any]) -> dict[str, Any]:
    """Create one real employee from confirmed fields. Admin-only; audited.

    Lands identity + base employee + canonical core + compensation atomically, registers a
    new division if needed, and returns the new record (token + the fields, for the UI to show
    and link to the Profile). Raises ManualIngestError on bad input or a duplicate email.
    """
    if not actor.is_admin:
        raise PermissionError("only an admin may add employees")

    f = _clean(payload)

    dup = find_token_by_email(conn, f["email"])
    if dup is not None:
        raise ManualIngestError(
            f"an employee with email {f['email']} already exists ({dup})"
        )

    if f["manager_token"] is not None:
        exists = conn.execute(
            "SELECT 1 FROM employees WHERE token = ?", (f["manager_token"],)
        ).fetchone()
        if exists is None:
            raise ManualIngestError(f"manager token {f['manager_token']!r} not found")

    ensure_dynamic_divisions(conn)
    ensure_extra_columns(conn)
    register_division(conn, f["division"])

    token = new_token()
    # Order matters: identities/employee_core/compensation all FK to employees(token),
    # so the base row is written first.
    conn.execute(
        "INSERT INTO employees (token, role, level, division, team, manager_token, "
        "location, hire_date, employment_type) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (token, f["role"], f["level"], f["division"], f["team"], f["manager_token"],
         f["location"], f["hire_date"], f["employment_type"]),
    )
    store_identity(conn, token, f["full_name"], f["email"], photo=f["photo"])

    core: dict[str, Any] = {
        "employee_token": token, "role": f["role"], "title": f["title"], "level": f["level"],
        "division": f["division"], "team": f["team"], "manager_token": f["manager_token"],
        "location": f["location"], "employment_type": f["employment_type"],
        "business_travel_frequency": f["business_travel_frequency"],
        "hire_date": f["hire_date"], "status": f["status"],
    }
    for k in ("gender", "birth_year_band", "marital_status"):
        if f[k] is not None:
            core[k] = f[k]
    _insert(conn, "employee_core", core)

    comp: dict[str, Any] = {"employee_token": token, "base_salary": f["base_salary"],
                            "currency": f["currency"]}
    if f["bonus"] is not None:
        comp["bonus_history"] = json.dumps([{"date": f["hire_date"], "amount": f["bonus"]}])
    if f["equity"] is not None:
        comp["equity_deferred"] = json.dumps({"note": f["equity"]})
    if f["pay_band"] is not None:
        comp["pay_band"] = f["pay_band"]
    if f["last_raise_date"] is not None:
        comp["last_raise_date"] = f["last_raise_date"]
    _insert(conn, "compensation", comp)

    # ---- optional canonical groups: written ONLY when the operator supplied them ----
    sc: dict[str, Any] = {}
    if f["education_level"]:
        sc["education_level"] = f["education_level"]
    if f["education_field"]:
        sc["education_field"] = f["education_field"]
    if f["education_institution"]:
        sc["education_institution"] = f["education_institution"]
    if f["skills"]:
        sc["skills"] = json.dumps(f["skills"])
    if f["certifications"]:
        sc["certifications"] = json.dumps(f["certifications"])
    if f["licenses"]:
        sc["licenses"] = json.dumps([{"name": n} for n in f["licenses"]])
    if f["languages"]:
        sc["languages"] = json.dumps(f["languages"])
    if f["hobbies"]:
        sc["hobbies"] = json.dumps(f["hobbies"])
    if sc:
        _insert(conn, "skills_credentials", {"employee_token": token, **sc})

    cvt: dict[str, Any] = {}
    if f["years_of_experience"] is not None:
        cvt["years_of_experience"] = f["years_of_experience"]
    if f["prior_employer_count"] is not None:
        cvt["prior_employer_count"] = f["prior_employer_count"]
    if cvt:
        _insert(conn, "cv_trajectory", {"employee_token": token, **cvt})

    cm: dict[str, Any] = {}
    if f["total_working_years"] is not None:
        tenure = _years_since(f["hire_date"])
        cm.update({"total_working_years": f["total_working_years"],
                   "tenure": tenure, "years_in_current_role": tenure})
    if f["time_since_last_promotion"] is not None:
        cm["time_since_last_promotion"] = f["time_since_last_promotion"]
    if cm:
        _insert(conn, "career_mobility", {"employee_token": token, **cm})

    if f["performance_rating"] is not None or f["goal_attainment"] is not None:
        perf: dict[str, Any] = {"employee_token": token}
        if f["performance_rating"] is not None:
            perf["review_ratings"] = json.dumps([{"period": _current_period(),
                                                  "score": f["performance_rating"]}])
        if f["goal_attainment"] is not None:
            perf["goal_attainment"] = f["goal_attainment"]
        _insert(conn, "performance", perf)

    life: dict[str, Any] = {}
    if f["onboarding_status"]:
        life["onboarding_status"] = f["onboarding_status"]
    if f["distance_from_home"] is not None:
        life["distance_from_home"] = f["distance_from_home"]
    if life:
        _insert(conn, "lifecycle",
                {"employee_token": token, "as_of_date": date.today().isoformat(), **life})

    # Team-Pulse operational row: birthday + expiring credential + (in-progress) onboarding.
    ops: dict[str, Any] = {}
    if f["birthday_md"]:
        ops["birthday_md"] = f["birthday_md"]
    if f["onboarding_status"] in ("in_progress", "complete"):
        ops["onboarding_status"] = f["onboarding_status"]
    if f["credential_name"]:
        ops["credential_name"] = f["credential_name"]
    if f["credential_expiry"]:
        ops["credential_expiry"] = f["credential_expiry"]
    if ops:
        _insert(conn, "employee_ops", {"employee_token": token, **ops})

    # write_audit commits the whole transaction (it ends with conn.commit()).
    write_audit(
        conn,
        actor_email=actor.email,
        action="create_employee",
        filters={"division": f["division"], "role": f["role"], "source": "manual_import"},
        result_count=1,
    )

    return {
        "token": token,
        "full_name": f["full_name"],
        "email": f["email"],
        "role": f["role"],
        "title": f["title"],
        "level": f["level"],
        "division": f["division"],
        "team": f["team"],
        "location": f["location"],
        "employment_type": f["employment_type"],
        "status": f["status"],
        "hire_date": f["hire_date"],
        "base_salary": f["base_salary"],
        "currency": f["currency"],
        "manager_token": f["manager_token"],
    }
