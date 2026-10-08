-- Talent88 — CANONICAL DATA SCHEMA v2 FINAL (§4 tables 0-13, §5 packs, §6 placeholders).
--
-- ADDITIVE OVERLAY. This script is applied AFTER schema.sql (see db.py). It does NOT
-- touch any existing table or seam. The legacy operational tables (`employees`,
-- `identities`, `feature_snapshots`, `scores`, ...) keep backing the live query +
-- scoring seams unchanged; these canonical tables are the spec-complete model that
-- ingestion writes to and future scoring/queries read. Every table is keyed on the
-- pseudonymous `employee_token` and references the existing `employees(token)`.
--
-- PSEUDONYMIZATION (§3): no analytics table here holds a name or email. Canonical
-- Table 0 `identity` is the EXISTING encrypted `identities` table (token-keyed,
-- Fernet at rest, single resolve_names() boundary) — not duplicated here.
--
-- CONTROLLED VOCAB: CHECK constraints below mirror data/canonical.py ENUMS exactly
-- (kept in sync by test_schema_canonical). Bad enum value -> IntegrityError.
--
-- ============================================================================
-- §9 EXPLICIT RED EXCLUSIONS — fields we deliberately DO NOT collect, ever:
--   * message / email / chat CONTENT (metadata only — see collaboration_metadata)
--   * keystroke logging, screenshots, screen / webcam capture
--   * scraped external profiles (license market data instead)
--   * inferred protected traits: health, pregnancy, religion, sexual orientation,
--     union / concerted activity (NLRA)
--   * national ID / SSN / financial account numbers beyond payroll necessity
--   * biometric data (BIPA and similar)
--   * any field used to SCORE on school prestige, named employer, gender, age, or
--     marital status (proxy / explicit discrimination — bias-audit / skills-search only)
-- No column in this schema represents any of the above. Enforced by test scan.
-- ============================================================================

PRAGMA foreign_keys = ON;

-- Table 1 — employee_core. Superset of the legacy `employees` row. Bias columns
-- (gender, birth_year_band, marital_status) are 🟡 bias-audit-only: present here but
-- read ONLY via data/bias_audit.py and walled off from all scoring (canonical.py).
CREATE TABLE employee_core (
    employee_token            TEXT PRIMARY KEY REFERENCES employees(token),
    role                      TEXT NOT NULL,
    title                     TEXT,
    level                     INTEGER NOT NULL,
    -- division is DATA-DRIVEN (open vocab): registered in the `divisions` table so a
    -- real company can ingest its own org structure. The five names in canonical.DIVISIONS
    -- remain the seed set used by the synthetic generator and the Workday alias-normalizer.
    division                  TEXT NOT NULL,
    team                      TEXT NOT NULL,
    manager_token             TEXT REFERENCES employees(token),
    skiplevel_token           TEXT REFERENCES employees(token),
    location                  TEXT NOT NULL,
    employment_type           TEXT NOT NULL CHECK (employment_type IN
                                ('full_time','part_time','contractor')),
    business_travel_frequency TEXT CHECK (business_travel_frequency IN
                                ('none','occasional','frequent')),
    hire_date                 TEXT NOT NULL,
    status                    TEXT NOT NULL CHECK (status IN ('active','on_leave','terminated')),
    -- 🟡 bias-audit ONLY — walled off from scoring (data/bias_audit.py is the sole reader)
    birth_year_band           TEXT CHECK (birth_year_band IN
                                ('<1970','1970-1979','1980-1989','1990-1999','2000+')),
    gender                    TEXT CHECK (gender IN ('female','male','non_binary','undisclosed')),
    marital_status            TEXT CHECK (marital_status IN ('single','married','divorced','undisclosed'))
);

-- Table 2 — compensation. comp_gap_vs_market is 🟡 (licensed benchmark).
CREATE TABLE compensation (
    employee_token         TEXT PRIMARY KEY REFERENCES employees(token),
    base_salary            REAL NOT NULL,
    currency               TEXT NOT NULL CHECK (currency IN ('USD','GBP','EUR','SGD','HKD','JPY')),
    bonus_history          TEXT,    -- JSON [{date,amount}]
    equity_deferred        TEXT,    -- JSON
    vesting_dates          TEXT,    -- JSON [date] — vest cliffs are departure triggers
    last_raise_date        TEXT,
    percent_salary_hike    REAL,
    pay_band               TEXT,
    pay_percentile_in_role REAL,
    comp_gap_vs_market     REAL     -- 🟡 licensed benchmark (McLagan/Radford/Mercer)
);

-- Table 3 — career_mobility.
CREATE TABLE career_mobility (
    employee_token               TEXT PRIMARY KEY REFERENCES employees(token),
    promotions                   TEXT,    -- JSON [{date,from,to}]
    time_since_last_promotion    INTEGER, -- months
    promotion_velocity           REAL,
    years_in_current_role        REAL,
    total_working_years          REAL,
    internal_moves               TEXT,    -- JSON
    internal_application_history TEXT,    -- JSON — internal apps = strong flight + mobility signal
    tenure                       REAL,    -- years
    tenure_at_exit               REAL     -- leavers only
);

-- Table 4 — performance (core) + §5 division signal packs (long-format extension).
CREATE TABLE performance (
    employee_token    TEXT PRIMARY KEY REFERENCES employees(token),
    review_ratings    TEXT,    -- JSON [{period,score}]
    goal_attainment   REAL,
    performance_trend REAL     -- direction > level
);

-- §5 division-specific signal packs, long-format so divisions never widen a table.
CREATE TABLE performance_division_signals (
    employee_token TEXT NOT NULL REFERENCES employees(token),
    division       TEXT NOT NULL,
    signal         TEXT NOT NULL,   -- e.g. pnl_attribution, throughput, quota_attainment
    value          REAL,
    unit           TEXT,
    PRIMARY KEY (employee_token, signal)
);

-- Table 5 — skills_credentials.
CREATE TABLE skills_credentials (
    employee_token           TEXT PRIMARY KEY REFERENCES employees(token),
    skills                   TEXT,    -- JSON [string]
    skill_proficiency_level  TEXT,    -- JSON [{skill,level}]
    skill_acquisition_date   TEXT,    -- JSON [{skill,date}] — needed for learning velocity
    role_skill_requirements  TEXT,    -- JSON
    education_level          TEXT CHECK (education_level IN
                               ('high_school','associate','bachelor','master','doctorate')),
    education_field          TEXT CHECK (education_field IN
                               ('life_sciences','medical','marketing','technical_degree',
                                'human_resources','business','other')),
    education_institution    TEXT,    -- university / school name (free text)
    certifications           TEXT,    -- JSON [string]
    licenses                 TEXT,    -- JSON [{name,expiry}] — Series 7/63, CFA; expiry critical
    languages                TEXT,    -- JSON [string] — spoken languages
    hobbies                  TEXT,    -- JSON [string] — personal interests
    training_completed       TEXT,    -- JSON
    training_times_last_year INTEGER
);

-- Table 6 — engagement (survey-based, opt-in). Snapshotted. Ingest the platform's
-- precomputed scores/drivers/Q12/themes — never recompute (see ingestion adapter).
CREATE TABLE engagement (
    employee_token                     TEXT NOT NULL REFERENCES employees(token),
    as_of_date                         TEXT NOT NULL,
    engagement_score                   REAL,
    engagement_trend                   REAL,    -- trend beats absolute
    enps_score                         INTEGER, -- -100..100
    enps_trend                         REAL,
    pulse_survey_score                 REAL,
    job_satisfaction                   INTEGER,
    environment_satisfaction           INTEGER,
    relationship_satisfaction          INTEGER,
    job_involvement                    INTEGER,
    work_life_balance                  INTEGER, -- self-reported burnout signal
    engagement_driver_scores           TEXT,    -- JSON — per-driver "why"
    q12_scores                         TEXT,    -- JSON — Gallup Q12 vector
    survey_text_sentiment              REAL,    -- numeric sentiment (no raw comments stored)
    survey_open_text_themes            TEXT,    -- JSON — platform-computed themes
    survey_response_rate               REAL,
    survey_date                        TEXT,
    recognition_count                  INTEGER,
    manager_effectiveness_survey_score REAL,
    PRIMARY KEY (employee_token, as_of_date)
);

-- Table 7 — manager_team (keyed on the manager).
CREATE TABLE manager_team (
    manager_token              TEXT PRIMARY KEY REFERENCES employees(token),
    manager_tenure             REAL,
    manager_performance_rating REAL,
    span_of_control            INTEGER,
    manager_team_turnover_rate REAL,
    team_turnover_rate         REAL,
    time_since_manager_change  INTEGER, -- months (IBM YearsWithCurrManager) — reorgs are risk events
    peers_departed_recently    INTEGER  -- last 3mo — resignation contagion
);

-- Table 8 — collaboration_metadata (🟡 ONA, CONSENT-GATED, metadata only — never
-- content). Every signal column is NULL unless engagement_prefs.monitoring_consent=1.
CREATE TABLE collaboration_metadata (
    employee_token          TEXT NOT NULL REFERENCES employees(token),
    as_of_date              TEXT NOT NULL,
    interaction_frequency   TEXT,    -- JSON [{peer_token,count}] — who-with-whom, NOT content
    network_size            INTEGER,
    network_trend           REAL,    -- shrinking network = early flight signal
    betweenness_centrality  REAL,
    pagerank_centrality     REAL,
    bridge_score            REAL,
    cross_division_reach    REAL,
    meeting_load            REAL,
    focus_time_fragmentation REAL,
    after_hours_activity    REAL,    -- risk flag ONLY — never a productivity bonus
    response_latency        REAL,
    PRIMARY KEY (employee_token, as_of_date)
);

-- Table 9 — cv_trajectory. school_prestige / named_employer are 🟡 identity proxies,
-- walled off from any pay/promotion/retention score (skills/search only).
CREATE TABLE cv_trajectory (
    employee_token          TEXT PRIMARY KEY REFERENCES employees(token),
    prior_employer_count    INTEGER,
    median_prior_tenure     REAL,    -- strongest CV-based flight signal
    job_hopping_rate        REAL,
    tenure_curve_shape      TEXT CHECK (tenure_curve_shape IN ('lengthening','shortening','stable')),
    career_velocity         REAL,
    skill_adjacency_to_role REAL,
    boomerang_flag          INTEGER CHECK (boomerang_flag IN (0,1)),
    years_of_experience     INTEGER, -- graduation year discarded (age proxy)
    school_prestige         TEXT,    -- 🟡 walled off from scoring — skills/search only
    named_employer          TEXT     -- 🟡 walled off from scoring — skills/search only
);

-- Table 10 — external_market (🟡 license-only — never scraped). Snapshotted.
CREATE TABLE external_market (
    employee_token          TEXT NOT NULL REFERENCES employees(token),
    as_of_date              TEXT NOT NULL,
    skill_market_demand     REAL,
    role_comp_benchmark     REAL,
    regional_comp_movement  REAL,
    industry_hiring_heat    REAL,
    PRIMARY KEY (employee_token, as_of_date)
);

-- Table 11 — lifecycle. Snapshotted.
CREATE TABLE lifecycle (
    employee_token     TEXT NOT NULL REFERENCES employees(token),
    as_of_date         TEXT NOT NULL,
    pto_used           REAL,    -- too little PTO = burnout risk
    absence_pattern    TEXT,    -- JSON
    overtime_flag      REAL,
    distance_from_home REAL,    -- commute
    onboarding_status  TEXT CHECK (onboarding_status IN ('not_started','in_progress','complete')),
    PRIMARY KEY (employee_token, as_of_date)
);

-- Table 12 — label_attrition (leavers only — the FR training label).
CREATE TABLE label_attrition (
    employee_token     TEXT PRIMARY KEY REFERENCES employees(token),
    attrition          INTEGER NOT NULL CHECK (attrition IN (0,1)),
    term_date          TEXT,
    voluntary          INTEGER CHECK (voluntary IN (0,1)),
    regretted          INTEGER CHECK (regretted IN (0,1)),  -- the target that matters
    leaver_reason      TEXT CHECK (leaver_reason IN
                         ('compensation','career_growth','management','relocation',
                          'performance','retirement','other')),
    leaver_destination TEXT CHECK (leaver_destination IN     -- 🟡 if known
                         ('competitor','other_industry','startup','unknown'))
);

-- Table 13 — features_engineered (gold layer). ONE ROW PER EMPLOYEE PER MONTHLY
-- SNAPSHOT — history mandatory (≥6 months in synthetic data).
CREATE TABLE features_engineered (
    employee_token          TEXT NOT NULL REFERENCES employees(token),
    score_snapshot_date     TEXT NOT NULL,
    tenure_cliff_proximity  REAL,    -- distance to thresholds (18mo, anniversaries)
    skill_uniqueness        REAL,    -- rarity of skill mix in org
    comp_gap_normalized     REAL,    -- cohort-relative
    PRIMARY KEY (employee_token, score_snapshot_date)
);

-- §6 — FUTURE placeholders. Schema only. `interventions` stays EMPTY for now;
-- `engagement_prefs` IS populated because monitoring_consent gates Table 8.
CREATE TABLE interventions (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_token     TEXT NOT NULL REFERENCES employees(token),
    action_type        TEXT,
    date               TEXT,
    scope              TEXT,
    cost               REAL,
    status             TEXT,
    outcome            TEXT,
    control_group_flag INTEGER CHECK (control_group_flag IN (0,1))
);

CREATE TABLE engagement_prefs (
    employee_token        TEXT PRIMARY KEY REFERENCES employees(token),
    monitoring_consent    INTEGER NOT NULL DEFAULT 0 CHECK (monitoring_consent IN (0,1)),
    messaging_consent     INTEGER NOT NULL DEFAULT 0 CHECK (messaging_consent IN (0,1)),
    channel_prefs         TEXT,    -- JSON
    recommendation_history TEXT    -- JSON
);

CREATE INDEX idx_core_division        ON employee_core(division);
CREATE INDEX idx_core_manager         ON employee_core(manager_token);
CREATE INDEX idx_engagement_token     ON engagement(employee_token, as_of_date);
CREATE INDEX idx_collab_token         ON collaboration_metadata(employee_token, as_of_date);
CREATE INDEX idx_extmarket_token      ON external_market(employee_token, as_of_date);
CREATE INDEX idx_lifecycle_token      ON lifecycle(employee_token, as_of_date);
CREATE INDEX idx_featureseng_snapshot ON features_engineered(employee_token, score_snapshot_date);
CREATE INDEX idx_divsignals_token     ON performance_division_signals(employee_token);
