# `data/` — the data layer (query seam + pseudonymization + security)

The **only** module that touches the database. Owns the schema, the synthetic data
generator, authentication/permissions, tamper-evident audit logging, encryption +
the key seam, the pseudonymization/name-resolution seam, and the HTTP API the
frontend calls. Nothing else in the monorepo reads the DB.

> Security model is documented end-to-end in [`SECURITY.md`](../SECURITY.md).

## Run

```bash
# from repo root, with the venv active and data/requirements.txt installed
python -m data.security.keygen            # mint a local data-encryption key (gitignored)
python -m data.generate --seed 7          # (re)build the synthetic SQLite DB (PII encrypted)
python -m data.refresh                     # call the scoring seam, populate scores+reason codes
uvicorn data.api:app --reload --port 8000 # serve the data API on :8000
pytest data/tests -q                      # run the data-layer + security tests
```

> `generate.py` auto-mints a gitignored dev key if none is set, so the keygen step is
> optional locally. In production, inject `PULSESCORE_DATA_KEY` via your KMS (BYOK).
> `data.refresh` needs the model service running on :8001 (it crosses the scoring
> seam over HTTP); it computes `risk_trend` across snapshots and is idempotent.

## Pseudonymization & encryption (security-critical)
Employees are tokens everywhere (`emp_…`). PII (`full_name`, `email`) lives ONLY in
the encrypted `identities` table; scoring, the query seam and analytics never need a
name. Real names are produced by exactly one function — `identity.resolve_names()` —
exposed via one endpoint, `POST /identity/resolve`, which the UI calls at render time
for **authorized** records only. Every resolution is scoped + audited. See `SECURITY.md`.

> Scores are intentionally empty after generation — they are populated by the
> score-refresh step (see `model_service/`), which calls the scoring seam. The
> data layer never computes scores.

## The query seam — `data/query.py`
`get_employees(conn, *, actor, division?, manager_id?, risk_band?, comp_gap?, sort?, limit?)`
is the single source of employee/score data. It returns each employee joined with
their **latest score + structured reason codes**, and is general enough that a
future natural-language agent can call it as a tool (e.g. *"who on my team is
high-risk and underpaid"* → `risk_band="high", comp_gap=0.2`).

Two guarantees are enforced **inside** the seam (not the UI), so they cannot be
bypassed and the future agent inherits them automatically:
- **Permission scope** — a `manager` Actor is hard-pinned to their own division; an
  `admin` sees everything. Managers cannot widen scope by passing another division.
- **Audit** — every call writes an `audit_log` row (actor, filters, result count, ts).

Companion functions on the same seam: `get_employee_history()` (6-snapshot trend +
reason codes), `get_employee_360()` (the **unified profile** — core record + 360
attributes + skills graph + the full 8-score/capital panel with per-metric reason
codes + retention trend; same scope + audit guarantees), and `get_dashboard_metrics()`
(headline counts, the capital-quadrant points incl. cost-to-lose, by-division rollup).

**Consent gating:** `get_employee_360` nulls the engagement signal when the employee
has not consented (`consented_signals = 0`), matching the gate the refresh applies
across the scoring seam. No covert monitoring.

## Seams in this module
| Seam | File | Swap-in later |
| ---- | ---- | ------------- |
| DB portability | `db.py` | SQLite → Postgres: change only this module |
| Auth | `auth.py` | dev login → password/SSO behind `authenticate()`, still returns `Actor` |
| Encryption / key (BYOK) | `security/crypto.py` | `load_key()` → customer KMS/HSM |
| Pseudonymization | `identity.py` | single `resolve_names()` boundary; lock down/disable here |

## Schema (`schema.sql`)
`divisions`, `employees` (token PK, self-referential `manager_token`, **no PII**),
`identities` (encrypted `full_name`/`email`, the only PII table), `feature_snapshots`
(6 monthly model-input rows/token), `scores`, `reason_codes` (structured rows with a
`metric` column tying each code to its score), `users`, `audit_log` (hash-chained,
tamper-evident). **Employee 360 + intelligence:** `employee_attributes` (360 inputs incl.
`consented_signals`), `skills` + `employee_skills` (skills graph), `metric_scores`
(long-format 8-score panel), `capital_metrics` (dollar/ratio figures, `is_estimate`
flagged). **Engagement:** `notifications` (opt-in, token-only payload, `why_text`) +
`notification_prefs` (per-kind opt-in, default off; `channel` is the abstraction seam).

## Canonical schema overlay (`schema_canonical.sql` + `canonical.py`)
The Talent88 v2 FINAL data model (§4 tables 0–13, §5 division signal packs, §6
placeholders) is an **additive overlay**: `schema_canonical.sql` is applied *after*
`schema.sql` and adds 16 token-keyed tables (`employee_core`, `compensation`,
`career_mobility`, `performance` + `performance_division_signals`, `skills_credentials`,
`engagement`, `manager_team`, `collaboration_metadata`, `cv_trajectory`,
`external_market`, `lifecycle`, `label_attrition`, `features_engineered`,
`interventions`, `engagement_prefs`). It **never** touches the legacy tables or the two
seams, so the app needs zero changes; the generator populates both from the same seeded
employees. Canonical Table 0 (`identity`) **is** the existing encrypted `identities`
table — not duplicated. `canonical.py` is the single source of truth for the controlled
vocabularies (mirrored by the SQL `CHECK` constraints) and the **scoring wall** list.

Four structural rules are enforced in code + tests: **pseudonymization** (no PII column
in any canonical table), **consent gating** (`collaboration_metadata` rows exist only
where `engagement_prefs.monitoring_consent = 1`), **bias-audit walling** + **identity-proxy
walling** (`gender`/`birth_year_band`/`marital_status` and `school_prestige`/`named_employer`
never enter a scoring feature set), and ≥6 **monthly snapshots** for the snapshot tables.
The §9 RED exclusions are a comment block atop the DDL and a column-name scan test.

## Bias-audit gateway (`bias_audit.py`)
The **sole** read path to the walled protected traits. `get_bias_audit_cohort(conn,
trait, *, actor)` returns *aggregate* cohort statistics (counts + mean outcomes per
group) for fairness analysis only — never per-individual trait values. It is admin-scoped
and audited, only accepts a trait in `canonical.BIAS_AUDIT_COLUMNS`, and `model_service`
contains **no import of it** (test-enforced) — so the scorer physically cannot reach a
protected trait.

## Ingestion pipeline (`data/ingestion/`)
Raw source exports → canonical tables through six explicit, **AI-free** stages:
`readers` (CSV / multi-sheet Excel with offset header + Excel serial dates / JSON / XML)
→ `adapters` (static source→canonical field maps) → `normalize` (dates, currency→USD
base, grade→level, controlled vocab) → `resolve` (exact-key entity resolution; ambiguous
matches go to a **review queue, never auto-merged**) → `validate` (schema / ranges /
referential / volume — **fails loudly, writes nothing**) → `write` (parameterized
`INSERT`). Ships a **Workday** adapter and an **engagement-survey** adapter for Peakon /
Glint / Qualtrics / Culture Amp / Perceptyx / Medallia that ingests the platform's
**precomputed** scores/drivers/Q12/themes verbatim (never recomputes). Live HRIS / graph
/ market integrations are documented `NotImplementedError` seams (`stubs.py`).

```bash
python -m data.ingestion.run <file> --adapter workday
python -m data.ingestion.run <file> --adapter engagement_survey:peakon
```

## Environment
- `PULSESCORE_DATA_KEY` — PII encryption key (required; KMS-injected in prod). See `.env.example`.
- `PULSESCORE_KEYFILE` — local dev keyfile path (default `data/.localkey`, gitignored)
- `PULSESCORE_DB` — DB path override (default `data/pulsescore.db`)
- `PULSESCORE_SECRET` — token signing secret (dev default provided)

## HTTP API (consumed by `app/` and the agent layer)
`POST /auth/login` · `GET /me` · `GET /employees` · `GET /employees/{token}` ·
`GET /employees/{token}/360` · `POST /identity/resolve` · `GET /dashboard` ·
`GET /divisions` · `POST /audit/agent` (the scoped, signed agent-run write, bounded to
L1–L3) · engagement: `GET/POST /notifications`, `POST /notifications/{id}/read`,
`GET/PUT /notification_prefs`. All except login require a `Bearer` token. Inputs
validated; errors are generic (no internal leakage).
