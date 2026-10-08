-- ============================================================================
-- Talent88 — Survey module (employee listening). ADDITIVE overlay, applied AFTER
-- schema_canonical.sql and schema_ops.sql. Every table references employees(token)
-- and/or another survey_* table; none modifies a base/canonical table or seam.
--
-- PRIVACY: responses are token-linked but read ONLY in aggregate above a minimum-
-- reporting threshold (default 5, reusing analytics.SUPPRESS_N). Raw open-text is
-- access-restricted; only extracted themes are surfaced. Same token-only / scoped /
-- audited framing as the rest of the data seam.
--
-- OUTPUT: a closed campaign writes results into the EXISTING `engagement` table
-- (engagement_score, enps_score, engagement_driver_scores, survey_open_text_themes,
-- survey_response_rate) — so the flight-risk model and the Analytics Engagement tab
-- consume survey output with no change.
-- ============================================================================

-- Driver taxonomy. `code` is the stable key stored in engagement.engagement_driver_scores.
CREATE TABLE survey_driver (
    code        TEXT PRIMARY KEY,            -- 'manager','growth','belonging',...
    name        TEXT NOT NULL,
    description TEXT,
    is_outcome  INTEGER NOT NULL DEFAULT 0 CHECK (is_outcome IN (0,1))  -- engagement index = outcome
);

-- Reusable validated items (the science-backed question bank).
CREATE TABLE survey_item (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    driver_code    TEXT REFERENCES survey_driver(code),
    text           TEXT NOT NULL,
    scale          TEXT NOT NULL CHECK (scale IN ('AGREE5','ENPS','FREQ5','OPEN')),
    reverse_scored INTEGER NOT NULL DEFAULT 0 CHECK (reverse_scored IN (0,1)),
    lifecycle_tag  TEXT,                      -- 'onboarding'|'exit'|'mgr_eff'|NULL
    active         INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1))
);

-- Reusable blueprints (the seeded templates).
CREATE TABLE survey_template (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    key         TEXT UNIQUE NOT NULL,         -- 'annual_census','quarterly_pulse',...
    title       TEXT NOT NULL,
    type        TEXT NOT NULL CHECK (type IN
                  ('engagement','pulse','lifecycle','enps','manager_effectiveness',
                   'dei','wellbeing','change','custom')),
    cadence     TEXT,                         -- 'annual','quarterly','monthly','triggered','event','semiannual','targeted','adhoc'
    description TEXT,
    builtin     INTEGER NOT NULL DEFAULT 1 CHECK (builtin IN (0,1))  -- seeded vs user-created
);

CREATE TABLE survey_template_item (
    template_id INTEGER NOT NULL REFERENCES survey_template(id),
    item_id     INTEGER NOT NULL REFERENCES survey_item(id),
    position    INTEGER NOT NULL,
    PRIMARY KEY (template_id, item_id)
);

-- A launchable instance (type, cadence, audience, status).
CREATE TABLE survey_campaign (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    template_id         INTEGER REFERENCES survey_template(id),   -- NULL if built ad hoc
    title               TEXT NOT NULL,
    type                TEXT NOT NULL CHECK (type IN
                          ('engagement','pulse','lifecycle','enps','manager_effectiveness',
                           'dei','wellbeing','change','custom')),
    status              TEXT NOT NULL DEFAULT 'draft' CHECK (status IN
                          ('draft','scheduled','active','closed','archived')),
    cadence             TEXT,                 -- recurring cadence, or NULL for one-off
    audience_json       TEXT,                 -- {division,team,manager_token,level,tenure_band,employment_type}
    trigger_event       TEXT CHECK (trigger_event IN ('hire','termination','role_change')),
    trigger_offset_days INTEGER,              -- +1 / +30 / +90 for lifecycle triggers
    anonymous           INTEGER NOT NULL DEFAULT 0 CHECK (anonymous IN (0,1)),  -- confidential default
    min_threshold       INTEGER NOT NULL DEFAULT 5,
    opens_at            TEXT,
    closes_at           TEXT,
    created_by          TEXT,                 -- actor email
    created_ts          TEXT NOT NULL
);

-- Frozen question list for a campaign (snapshot: editing the library later never
-- mutates an already-sent survey). Carries question type + per-question logic.
CREATE TABLE survey_question (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id  INTEGER NOT NULL REFERENCES survey_campaign(id),
    item_id      INTEGER REFERENCES survey_item(id),   -- provenance (NULL = fully custom)
    driver_code  TEXT REFERENCES survey_driver(code),
    position     INTEGER NOT NULL,
    text         TEXT NOT NULL,
    qtype        TEXT NOT NULL CHECK (qtype IN
                   ('likert','enps','multiple_choice','open_text','ranking','matrix')),
    scale        TEXT,                        -- 'AGREE5'|'ENPS'|'FREQ5'|NULL
    options_json TEXT,                        -- choices / ranking / matrix rows+cols
    required     INTEGER NOT NULL DEFAULT 1 CHECK (required IN (0,1)),
    logic_json   TEXT                         -- skip / branch / piping rules
);

-- Who was invited (response_rate denominator; token-linked).
CREATE TABLE survey_invitation (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id    INTEGER NOT NULL REFERENCES survey_campaign(id),
    employee_token TEXT NOT NULL REFERENCES employees(token),
    status         TEXT NOT NULL DEFAULT 'invited' CHECK (status IN
                     ('invited','started','completed','declined')),
    invited_ts     TEXT,
    completed_ts   TEXT,
    UNIQUE (campaign_id, employee_token)
);

-- Token-linked, confidential responses (one row per answer; read only in aggregate).
CREATE TABLE survey_response (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id    INTEGER NOT NULL REFERENCES survey_campaign(id),
    question_id    INTEGER NOT NULL REFERENCES survey_question(id),
    employee_token TEXT NOT NULL REFERENCES employees(token),
    numeric_value  REAL,                      -- Likert 1–5, eNPS 0–10, ranking position
    choice_value   TEXT,                      -- selected option(s) JSON for choice/matrix
    text_value     TEXT,                      -- open-text (raw access restricted; themes surfaced)
    submitted_ts   TEXT NOT NULL,
    UNIQUE (campaign_id, question_id, employee_token)
);

-- Manager follow-up (owner, items, status).
CREATE TABLE action_plan (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER REFERENCES survey_campaign(id),
    driver_code TEXT REFERENCES survey_driver(code),     -- the driver the plan targets
    scope_json  TEXT,                         -- team/manager/division the plan covers (token-only)
    title       TEXT NOT NULL,
    owner_email TEXT,
    status      TEXT NOT NULL DEFAULT 'open' CHECK (status IN
                  ('open','in_progress','done','dropped')),
    items_json  TEXT,                         -- [{text,done}]
    created_ts  TEXT NOT NULL,
    updated_ts  TEXT
);

CREATE INDEX idx_svy_q_campaign    ON survey_question(campaign_id);
CREATE INDEX idx_svy_inv_campaign  ON survey_invitation(campaign_id, employee_token);
CREATE INDEX idx_svy_resp_campaign ON survey_response(campaign_id, question_id);
CREATE INDEX idx_svy_resp_token    ON survey_response(employee_token);
CREATE INDEX idx_svy_camp_status   ON survey_campaign(status, type);
