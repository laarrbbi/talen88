-- Talent88 — OPERATIONAL STATUS OVERLAY (Team Pulse / "What's happening").
--
-- ADDITIVE OVERLAY. Applied AFTER schema.sql and schema_canonical.sql (see db.py).
-- It does NOT touch any existing table or seam. The legacy operational tables and the
-- live query + scoring seams are unchanged; this single table backs ONLY the new
-- Dashboard "Team Pulse" panel via the new (scoped, audited) /team_pulse reader.
-- Every row is keyed on the pseudonymous `employee_token` and references
-- `employees(token)`. No name or email lives here.
--
-- PRIVACY (non-negotiable): leave carries a NEUTRAL availability + return date ONLY.
-- There is deliberately NO column for a leave reason, diagnosis, or any health detail.
-- A manager sees that someone is away and when they return — never why. The synthetic
-- generator must never produce or store a reason; the schema gives it nowhere to put one.
--
-- All operational columns are NULLABLE (additive/back-compatible): a token with no ops
-- row, or null fields, simply contributes nothing to the panel.

PRAGMA foreign_keys = ON;

CREATE TABLE employee_ops (
    employee_token     TEXT PRIMARY KEY REFERENCES employees(token),

    -- Upcoming birthday as a bare MM-DD (no birth year — year lives only in the
    -- bias-audit band, never here). Drives the "upcoming birthdays" list.
    birthday_md        TEXT,        -- "MM-DD" or NULL

    -- Current availability signal (in office / remote / out). Neutral, coarse.
    availability       TEXT CHECK (availability IN ('in_office', 'remote', 'out')),

    -- Short PTO: away now, back on this date. Neutral — no reason, ever.
    pto_return_date    TEXT,        -- ISO YYYY-MM-DD or NULL

    -- Extended leave: a NEUTRAL flag + an expected return date. No reason column exists.
    on_extended_leave  INTEGER NOT NULL DEFAULT 0 CHECK (on_extended_leave IN (0, 1)),
    leave_return_date  TEXT,        -- ISO YYYY-MM-DD or NULL

    -- New joiner / onboarding progress (the same neutral vocabulary as lifecycle).
    onboarding_status  TEXT CHECK (onboarding_status IN ('in_progress', 'complete')),

    -- Known upcoming role/manager change (a short neutral note + effective date).
    role_change_note   TEXT,        -- e.g. "Promotion to Senior", "New manager" or NULL
    role_change_date   TEXT,        -- ISO YYYY-MM-DD or NULL

    -- Expiring credential / work permit: name + expiry date so the panel can surface
    -- "expires in N days". Neutral compliance signal, no document content.
    credential_name    TEXT,        -- e.g. "Series 7", "Work permit" or NULL
    credential_expiry  TEXT         -- ISO YYYY-MM-DD or NULL
);
