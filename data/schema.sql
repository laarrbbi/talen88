-- PulseScore data layer schema (SQLite for the MVP).
--
-- PSEUDONYMIZATION BY CONSTRUCTION
-- Employees are identified everywhere by an opaque, non-sequential TOKEN. Real
-- PII (full_name, email) lives ONLY in `identities`, encrypted at rest, keyed by
-- token. No analytics table (scores/reason_codes/feature_snapshots) ever holds a
-- name. Scoring and the query seam operate on tokens alone.
--
-- DB-portability seam: plain SQL kept close to the SQLite/Postgres common subset.
-- SQLite-isms: INTEGER PRIMARY KEY AUTOINCREMENT (audit_log) and BLOB columns ->
-- map to SERIAL/IDENTITY and BYTEA on Postgres. See db.py.

PRAGMA foreign_keys = ON;

CREATE TABLE divisions (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

-- Pseudonymous, non-PII employee record. NO full_name / email here.
CREATE TABLE employees (
    token           TEXT PRIMARY KEY,                      -- e.g. emp_3f9a1c...
    role            TEXT NOT NULL,
    level           INTEGER NOT NULL,
    division        TEXT NOT NULL,
    team            TEXT NOT NULL,
    manager_token   TEXT REFERENCES employees(token),      -- self-referential hierarchy
    location        TEXT NOT NULL,
    hire_date       TEXT NOT NULL,                          -- ISO YYYY-MM-DD
    employment_type TEXT NOT NULL
);

-- THE ONLY place PII lives. Encrypted at rest (see data/security/crypto.py).
-- Access-controlled: read only via data/identity.resolve_names() with authz+audit.
-- `photo_enc` is the (optional) profile picture — a small encrypted data-URL thumbnail,
-- PII like the name, resolved only via data/identity.resolve_photos() with authz+audit.
CREATE TABLE identities (
    token         TEXT PRIMARY KEY REFERENCES employees(token),
    full_name_enc BLOB NOT NULL,    -- Fernet ciphertext
    email_enc     BLOB NOT NULL,    -- Fernet ciphertext
    photo_enc     BLOB              -- Fernet ciphertext of a data-URL thumbnail, or NULL
);

-- Raw MODEL INPUTS, one row per token per monthly snapshot (token-only).
CREATE TABLE feature_snapshots (
    employee_token         TEXT NOT NULL REFERENCES employees(token),
    as_of_date             TEXT NOT NULL,
    comp_gap               REAL NOT NULL,    -- fraction below band midpoint (0..1)
    months_since_promotion INTEGER NOT NULL,
    tenure_months          INTEGER NOT NULL,
    manager_changes_12mo   INTEGER NOT NULL,
    PRIMARY KEY (employee_token, as_of_date)
);

-- Scores written ONLY by the refresh step (it calls the scoring seam). Token-only.
CREATE TABLE scores (
    employee_token TEXT NOT NULL REFERENCES employees(token),
    as_of_date     TEXT NOT NULL,
    flight_risk    INTEGER NOT NULL,   -- 0..100
    value_score    INTEGER NOT NULL,   -- 0..100
    risk_trend     INTEGER NOT NULL,   -- delta vs prior period
    PRIMARY KEY (employee_token, as_of_date)
);

-- Structured reason codes, never pre-formatted strings. Token-only. `metric` ties
-- each code to which score it explains ('retention_risk' for the legacy flight-risk
-- codes; one of the panel metric keys otherwise).
CREATE TABLE reason_codes (
    employee_token TEXT NOT NULL REFERENCES employees(token),
    as_of_date     TEXT NOT NULL,
    metric         TEXT NOT NULL DEFAULT 'retention_risk',
    label          TEXT NOT NULL,
    direction      TEXT NOT NULL CHECK (direction IN ('increases', 'decreases')),
    weight         REAL NOT NULL      -- 0..1
);

-- The full INTELLIGENCE PANEL (Layer 2), long-format so adding scores never widens a
-- table. One row per (token, date, metric). Written ONLY by the refresh step. Token-only.
CREATE TABLE metric_scores (
    employee_token TEXT NOT NULL REFERENCES employees(token),
    as_of_date     TEXT NOT NULL,
    metric         TEXT NOT NULL,     -- e.g. skills_depth, learning_velocity, ...
    score          INTEGER NOT NULL,  -- 0..100
    PRIMARY KEY (employee_token, as_of_date, metric)
);

-- Capital / "moneyball" metrics. Dollar + ratio figures are MODELED ESTIMATES
-- (is_estimate=1) and must be surfaced with a caveat in the UI. Token-only.
CREATE TABLE capital_metrics (
    employee_token TEXT NOT NULL REFERENCES employees(token),
    as_of_date     TEXT NOT NULL,
    metric         TEXT NOT NULL,     -- e.g. cost_to_lose, retention_roi, ...
    amount         REAL NOT NULL,
    unit           TEXT NOT NULL,     -- 'USD', 'ratio', 'score_0_100'
    is_estimate    INTEGER NOT NULL CHECK (is_estimate IN (0, 1)),
    PRIMARY KEY (employee_token, as_of_date, metric)
);

-- EMPLOYEE 360 — broadened model inputs beyond retention, one row per token per
-- monthly snapshot (token-only, NO PII). These feed the full score panel.
-- `consented_signals` GATES engagement/productivity scoring: when 0, no engagement
-- signal is used (no covert monitoring — see SECURITY.md).
CREATE TABLE employee_attributes (
    employee_token     TEXT NOT NULL REFERENCES employees(token),
    as_of_date         TEXT NOT NULL,
    perf_rating        REAL NOT NULL,    -- 1..5 performance rating
    engagement_pulse   INTEGER NOT NULL, -- 0..100 self-reported pulse (consented)
    learning_hours_12mo INTEGER NOT NULL,
    internal_moves     INTEGER NOT NULL, -- lateral/internal moves to date
    span_of_control    INTEGER NOT NULL, -- direct reports (0 for ICs)
    consented_signals  INTEGER NOT NULL CHECK (consented_signals IN (0, 1)),
    PRIMARY KEY (employee_token, as_of_date)
);

-- Skills graph (token-only). `skills` is the catalog; `employee_skills` is the
-- many-to-many with proficiency. Feeds Skills Depth & Adjacency + Mobility.
CREATE TABLE skills (
    id     INTEGER PRIMARY KEY,
    name   TEXT NOT NULL UNIQUE,
    family TEXT NOT NULL
);

CREATE TABLE employee_skills (
    employee_token TEXT NOT NULL REFERENCES employees(token),
    skill_id       INTEGER NOT NULL REFERENCES skills(id),
    proficiency    INTEGER NOT NULL CHECK (proficiency BETWEEN 1 AND 5),
    PRIMARY KEY (employee_token, skill_id)
);

-- ENGAGEMENT LAYER (Layer 4) — in-app, opt-in notifications.
-- Recipients are operators (admin/manager), keyed by their login email; the body
-- references employees by TOKEN only (no PII). Every row carries a `why_text`
-- ("you're seeing this because...") for transparency. `channel` on the prefs table
-- is the abstraction seam: default 'in_app'; slack/teams/email/mobile are documented
-- future channels, not built. Notifications are OPT-IN per kind (see notification_prefs).
CREATE TABLE notifications (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    recipient_email TEXT NOT NULL,
    kind           TEXT NOT NULL,        -- e.g. 'retention_approved', 'promotion_ready'
    payload_json   TEXT NOT NULL,        -- token-only structured body (NO PII)
    why_text       TEXT NOT NULL,        -- transparency: why this was sent
    created_ts     TEXT NOT NULL,        -- ISO 8601
    read_ts        TEXT                  -- NULL until the recipient reads it
);

-- Per-recipient, per-kind opt-in. A notification of `kind` is only created when the
-- recipient has opted in (default OFF — nothing is pushed without consent).
CREATE TABLE notification_prefs (
    email     TEXT NOT NULL,
    kind      TEXT NOT NULL,
    channel   TEXT NOT NULL DEFAULT 'in_app',   -- abstraction seam (in_app only for MVP)
    opted_in  INTEGER NOT NULL DEFAULT 0 CHECK (opted_in IN (0, 1)),
    PRIMARY KEY (email, kind)
);

-- Login operators. An operator may be linked to their own employee token for division
-- scoping. Passwords are stored only as salted scrypt hashes (see data/auth.py); an
-- operator with no hash cannot sign in.
CREATE TABLE users (
    email          TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    role           TEXT NOT NULL CHECK (role IN ('admin', 'manager')),
    division       TEXT,                                  -- NULL for admin
    employee_token TEXT REFERENCES employees(token),
    password_hash  TEXT
);

-- Tamper-evident, append-only audit trail. Each row is hash-chained to the prior
-- row so silent edits/deletions are detectable (see data/audit.py).
CREATE TABLE audit_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_email  TEXT NOT NULL,
    action       TEXT NOT NULL,
    filters_json TEXT,
    result_count INTEGER,
    ts           TEXT NOT NULL,        -- ISO 8601 timestamp
    prev_hash    TEXT NOT NULL,        -- entry_hash of the previous row ("" for genesis)
    entry_hash   TEXT NOT NULL         -- sha256(prev_hash + canonical(this row))
);

CREATE INDEX idx_scores_date        ON scores(as_of_date);
CREATE INDEX idx_metricscores_tok   ON metric_scores(employee_token, as_of_date);
CREATE INDEX idx_capital_tok        ON capital_metrics(employee_token, as_of_date);
CREATE INDEX idx_features_date      ON feature_snapshots(as_of_date);
CREATE INDEX idx_attrs_date         ON employee_attributes(as_of_date);
CREATE INDEX idx_empskills_token    ON employee_skills(employee_token);
CREATE INDEX idx_reason_tok_date    ON reason_codes(employee_token, as_of_date);
CREATE INDEX idx_employees_division ON employees(division);
CREATE INDEX idx_notif_recipient    ON notifications(recipient_email, created_ts);
