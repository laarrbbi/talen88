# Talent88 — Security & Privacy Model

Security and pseudonymization are first-class, modular concerns in Talent88, not
afterthoughts. This document is the audit story: what the MVP enforces today, where
the seams are, and what a production financial-grade deployment still needs. It covers
all four layers — unified data, the intelligence engine, the agent layer, and the
engagement layer.

> Scope note: authentication/login hardening is intentionally out of scope for this
> MVP (dev login). Everything else below is in scope and implemented.

## 1. Pseudonymization (tokenized by construction)

Employees are identified **everywhere** by an opaque, non-sequential token
(`emp_…`). Personally-identifying fields (`full_name`, `email`) never appear inline
in operational/analytics tables.

| Layer | What it sees |
| ----- | ------------ |
| `feature_snapshots`, `scores`, `reason_codes`, `metric_scores`, `capital_metrics` | tokens only |
| `employee_attributes`, `skills`, `employee_skills` | tokens only — the 360 inputs carry no PII |
| `employees` | tokens + non-PII attributes (role, level, division, team, location, dates) |
| canonical overlay (`employee_core`, `compensation`, … 16 tables) | tokens only — no name/email column in any canonical analytics table |
| `identities` | the ONLY table with PII — `full_name`/`email`, **encrypted at rest** (= canonical Table 0 `identity`, not duplicated) |
| scoring service (`model_service`) | tokens + numeric features only — never needs a name |
| query seam (`data/query.py`) | tokens only — never reads or returns a name |
| agent layer (`agents/`) | tokens only — calls the query seam as the user; no DB access |
| `notifications` | recipient operator email + token-only payload — never an employee name |
| UI boundary (`/identity/resolve`) | resolves tokens → names, only for authorized records |

**Single isolated resolution boundary.** Real names are produced in exactly one
function — `data/identity.resolve_names()` — exposed via one endpoint,
`POST /identity/resolve`. The UI calls it at render time to label the tokens it is
about to display. Because every name shown flows through this one place, the feature
can be disabled, further restricted, or routed through a stricter authorization
check without touching any query or scoring logic. An unauthorized path sees only
tokens.

Resolution is **authorized** (same permission scope as the query seam: a manager can
only resolve names within their own division) and **audited** (every resolution is
recorded).

Quasi-identifiers (location + role + division) remain on the pseudonymous record by
design so analytics work; production may further restrict or generalize these — see §6.

## 2. Encryption

- **At rest:** PII in `identities` is encrypted with authenticated symmetric
  encryption (Fernet = AES-128-CBC + HMAC-SHA256) — see `data/security/crypto.py`.
  Tampered ciphertext fails closed on decrypt.
- **In transit:** assumed TLS everywhere (terminate TLS in front of both services).
  CORS is locked to the local dev origin in the MVP.

### Key-management seam (BYOK)
All encryption depends only on `crypto.encrypt/decrypt`, which depend only on
`crypto.load_key()`. Key resolution order:

1. `PULSESCORE_DATA_KEY` env var — how production injects the key.
2. gitignored local keyfile (`data/.localkey`) — developer convenience only.
3. **fail closed** — no key → the system refuses to encrypt/decrypt.

There is **no hardcoded key** in the code or repo. For a production deployment,
replace steps 1–2 with a provider backed by the customer's **KMS/HSM** (AWS KMS,
GCP KMS, Azure Key Vault, HashiCorp Vault) for customer-managed keys (BYOK). That
is the single integration point; nothing else changes.

### Secrets hygiene
No secrets, keys, or credentials live in the code or repo. They come from env vars /
a secrets mechanism. `.gitignore` excludes `.env*` (except `.env.example`),
`*.localkey`, `*.key`, `*.pem`, and `secrets/`. `.env.example` documents the variables.

## 3. Input validation & injection resistance

- **Parameterized queries only.** All SQL uses bound parameters; the only
  interpolated identifiers (sort column + direction) come from a fixed server-side
  whitelist (`data/query.py::_SORT_COLUMNS`). No user string is ever concatenated
  into SQL.
- Inputs are validated twice: at the HTTP edge (pydantic / FastAPI `Query`
  constraints + regex on `risk_band`) and inside the query seam
  (`_validate`, fail-closed). Bad input → generic `400`.

## 4. Least privilege & permission enforcement

The permission filter lives **inside** the query seam and the resolution seam, not
the UI — so it cannot be bypassed. Managers are hard-pinned to their own division (they
cannot widen scope by passing another division); admins see all.

**Agents inherit scope for free.** The agent layer (`agents/`, `:8002`) has **no
database access and no privileged path**. Each agent calls the data API over HTTP **as
the calling user**, forwarding that user's bearer token, so it inherits the exact same
permission scope and audit trail the UI has. A manager running an agent cannot see
beyond their division — verified by test and end-to-end. There is no other door into
the data.

## 5. Tamper-evident, comprehensive audit log

Every data-access call (`get_employees`, `get_employee_history`, `get_employee_360`,
dashboard), every name resolution, **every agent run**, and **every L3 approval** writes
an `audit_log` row: **who** (actor email), **what** (action + filter values + counts),
**when** (UTC timestamp). The log is **hash-chained** — `entry_hash = sha256(prev_hash +
canonical(row))` — so any silent edit or deletion of a historical row breaks the chain
and is detectable via `audit.verify_chain()`.

Agents have no DB access, so they record what they saw + proposed through one scoped,
signed write path (`POST /audit/agent`), which the data layer bounds to autonomy levels
**1–3** (an L4 write is rejected at the edge). The DB still owns the hash chain.

Logged fields are deliberately non-sensitive: no PII, no decrypted names, no tokens'
underlying identities. **Safe errors/logs:** API error handlers return generic
messages and never leak stack traces, tokens, PII, or key material; server-side logs
record error *type* only.

## 5a. Consented signals (no covert monitoring)

Engagement/productivity scoring is **gated on consent**. `employee_attributes` carries a
`consented_signals` flag; when it is `0`, the engagement signal is withheld at **both**
ends — `refresh.py` nulls it across the scoring seam (so the model never receives it)
and `get_employee_360` nulls it on read (so the UI never shows it). The Engagement &
Productivity score is simply not produced for a non-consenting employee, and the Profile
screen states why. There is no covert behavioral monitoring.

## 5b. Autonomy ceiling — no Level 4 anywhere

The agent autonomy ladder is **L1 insight → L2 recommendation → L3 human approval**, and
that is the ceiling. `agents/autonomy.py` defines `Level` as an `IntEnum` with only
`INSIGHT/RECOMMENDATION/APPROVE` — **`AUTO_EXECUTE` is not even a defined value** —
`MAX_LEVEL = APPROVE`, and `enforce()` raises for anything above L3. A disabled
`execute_autonomously()` placeholder exists only to raise if ever called. L3 "approve"
endpoints only record the approval in the audit trail and create an opt-in notification;
they **never** touch comp/HR systems. The data API independently rejects any agent-audit
write above L3 (defense in depth). Rationale: solely-automated employment decisions are
legally fraught (e.g. GDPR Art. 22) and must keep a human in the loop.

## 5c. Engagement notifications — opt-in and transparent

In-app notifications (`data/notifications.py`) enforce two guarantees: **opt-in** is a
hard precondition — nothing is written for a `kind` the recipient has not opted into
(default off, not a read-time filter) — and **scope** is per-recipient — an operator
reads and marks only their own notifications (addressed by login email; a non-admin
cannot push into another operator's inbox; payloads are token-only, no PII). Every
notification carries a `why_text` for transparency. `channel` is an abstraction seam
(`in_app` only in the MVP; slack/teams/email/mobile are documented future channels).

## 5d. Canonical data model — walling, consent, and §9 RED exclusions

The spec-complete canonical schema (`data/schema_canonical.sql`) is an **additive
overlay** that adds the modeled traits without weakening any guarantee above. Four
privacy controls are enforced in code **and** tests:

- **Bias-audit walling.** `gender`, `birth_year_band` and `marital_status` exist in
  `employee_core` for fairness auditing only. They are listed in
  `canonical.SCORING_EXCLUDED_COLUMNS`, appear in **no** `model_service` feature list
  (`PROTECTED_COLS` are explicitly held out), and their **sole** read path is
  `data/bias_audit.py` — which returns aggregate cohort statistics, is admin-scoped and
  audited, and is **never imported by `model_service`** (test-enforced). The wall is
  therefore physical: the scorer has no code path that can reach a protected trait.
- **Identity-proxy walling.** `school_prestige` and `named_employer` (skills/search
  inputs) are likewise in the scoring-excluded set and proven absent from every scoring
  feature set, so pay/promotion/retention scores cannot be driven by them.
- **Consent gating.** `collaboration_metadata` holds ONA **metadata only — never message
  content**, and a row exists only for an employee whose `engagement_prefs.monitoring_consent
  = 1`. Non-consenting employees are excluded at the source, not merely nulled.
- **§9 RED exclusions.** Message/email/chat **content**, keystroke logging, screenshots,
  webcam/biometric capture, and SSN/national-ID/financial-account fields are deliberately
  **not modeled**. A comment block heads the DDL and a test scans every canonical column
  name for the forbidden substrings.

**Ingestion is AI-free and fails closed.** `data/ingestion/` transforms source exports
into canonical rows with **no machine learning, inference, or fuzzy matching** in the
path: mapping is a static dictionary, entity resolution is exact-key only, and any
ambiguous match is parked in a **human review queue — never auto-merged** (so two distinct
people are never fused). Validation (schema / ranges / referential integrity / volume
sanity) runs before any write and **raises, writing nothing**, on failure. Controlled
vocabularies are validated against `canonical.ENUMS` before the DB `CHECK` would catch
them. Engagement adapters ingest the platform's **precomputed** scores verbatim and never
recompute. Live HRIS / Graph-Workspace / licensed-market connectors are documented
`NotImplementedError` seams — no network call, credential, or scrape exists in the repo.

## 5e. Local LLM reasoning — local-by-default, no PII, audited, injection-fenced

Agents turn their already-fetched, tokenized data into a short natural-language narrative
through a single LLM seam (`llm/` → `agents/reasoning.py`). The model is a **reasoning
surface over tool results, never a data source**: it only ever sees JSON the agent already
pulled through the permission-scoped query seam. Six controls, each test-backed:

- **Local by default, no silent egress.** The provider (`llm/config.py`, `llm/provider.py`)
  defaults to a **local** Ollama-compatible endpoint (`http://localhost:11434`). An external
  host is refused **before any network call** unless `TALENT88_LLM_ALLOW_EXTERNAL=true` is
  explicitly set; there is **no automatic fallback to a cloud model**. When an external
  endpoint *is* opted into, a prominent one-time warning is logged that data will leave the
  environment. Switching providers/models is **env-only** — no code change (`TALENT88_LLM_*`).
- **No PII ever reaches the model.** Prompts are token-only by construction (the agent feeds
  the model only query-seam output). As defense-in-depth, `reasoning.assert_no_pii` scans the
  fully-assembled prompt and **drops the call** (never sends) if an email shape or known name
  is detected. Names are resolved only later, at the UI boundary (§1), after the AI step.
- **Never run un-auditable AI.** `narrate()` will not call the model unless the tools client
  can audit it (`log_llm`); every call writes the tokenized prompt + response to the same
  hash-chained trail (§5) as `agent:llm` / `llm_generate` at insight level (L1).
- **Bounded tool access (least privilege).** The model is handed JSON; it has **no** DB,
  filesystem, network, or endpoint tool. It cannot widen scope or reach another door — it
  reasons over what the scoped agent already retrieved.
- **Prompt-injection fencing.** Instructions live **only** in the system message; tokenized
  data is a separate user message; any data-originated free-text (e.g. survey comments) is
  placed in a clearly-labelled `<<<UNTRUSTED_DATA>>>` block marked *data, never instructions*.
- **Graceful degradation.** If the model is unreachable or egress is refused, `narrate()`
  returns `None` and the agent falls back to its deterministic template — clearly "AI
  unavailable", never a crash and never a silent cloud call.

**Per-org customization is retrieval, not fine-tuning.** The LLM is never trained on a
client's data. At query time `agents/orgcontext.py` injects that org's static **context
profile** (divisions, comp bands, terminology, survey definitions) plus **live aggregates
retrieved through the same scoped tools client**. Isolation is strict: a profile is loaded by
`org_id` only, can never surface another org's content, and live retrieval inherits the query
seam's permission scope with no shared cache — so one org's data can never enter another's AI
context (test-enforced). See ARCHITECTURE.md for why retrieval is preferred over fine-tuning.

**Before enabling an external endpoint in production:** confirm the endpoint is in-tenant or
under a signed DPA with no training-on-input; keep `TALENT88_LLM_ALLOW_EXTERNAL` config-managed
and reviewed; and note that the no-PII guard is defense-in-depth, not a license to send raw PII.

## 6. What still needs hardening for production (financial-grade)

This MVP establishes the architecture; a regulated deployment additionally needs:

- **Authentication/SSO:** replace dev login with enterprise SSO/OIDC + MFA behind
  the existing `auth.authenticate()` seam; short-lived, rotated tokens.
- **In-tenant deployment:** run inside the customer's VPC/tenant; data never leaves
  their boundary.
- **Customer-managed keys (BYOK):** wire `crypto.load_key()` to the customer KMS/HSM;
  envelope encryption + key rotation; per-tenant keys.
- **Tamper-PROOF audit:** ship the audit log to append-only/WORM storage or an
  external immutable sink; periodic external anchoring; the in-DB hash chain is
  tamper-*evident* only.
- **Transport & network:** enforced TLS 1.2+/mTLS between services; strict CORS/CSP;
  secrets via a managed vault.
- **Compliance & assurance:** SOC 2 Type II, independent penetration test, data
  retention/erasure (GDPR/CCPA right-to-be-forgotten via the isolated identity
  table), DPIA, role review, and dependency/vulnerability scanning in CI.
- **Quasi-identifier review:** assess re-identification risk of location/role/division
  combinations; generalize or restrict as required.

## 7. Dependencies

Kept minimal and current: `fastapi`, `uvicorn`, `pandas`, `openpyxl` (Excel ingestion),
`httpx`, `cryptography` (pinned `43.0.1`), `pytest`. No dependencies with known advisories at the pinned
versions at time of writing. Production CI should run `pip-audit`/Dependabot and fail
on new advisories.
