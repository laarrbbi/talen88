# Talent88 — Architecture

Talent88 is a **four-layer people-analytics platform** built on **synthetic data only**,
as a modular monorepo with **strict seams** so a real ML model and the agent layer plug
in without rewriting the UI or the data access. This document is the map: the four
layers, the seams they ride, the autonomy ladder, and the hard constraints.

> Synthetic data only · no real personal data · runs entirely locally · no third-party
> cloud services. Security/pseudonymization model: [`SECURITY.md`](SECURITY.md).

## The four layers

| Layer | Lives in | Rides the seam | What it adds |
| ----- | -------- | -------------- | ------------ |
| **1 · Unified Data (Employee 360)** | `data/` | the query seam (`get_employees` / `get_employee_360`) is the single data-access path | token-keyed 360 record: core HR + `employee_attributes` (perf, engagement, learning, span, **consent**) + `skills` graph |
| **2 · Intelligence Engine** | `model_service/` | the scoring seam (`POST /score`) — same request/response *shape* | a full panel of **8 scores + capital metrics**, all transparent heuristics with reason codes and modeled-estimate caveats |
| **3 · Agent Layer** | `agents/` (`:8002`) | calls the query seam + scoring service **as tools** — no DB access | 5 agents (Conversational + Retention / Career / Learning / Workforce-Planning) at **L1–L3 only** |
| **4 · Engagement** | `data/` (notifications) | rides the audited data API | in-app, **opt-in**, channel-abstracted notifications with a transparency `why` on every item |
| **UI** | `app/` (`:5173`) | talks **only** to `data:8000` (+ `agents:8002` read) | Dashboard, Analytics, Watchlist, Profile, Company Graph, Surveys, Agents, Inbox, Data Management; finance-grade design language, light/dark. Fields the API does not yet expose render explicit *"not yet available"* states rather than being faked or hidden. |

## Data flow (one direction, through the seams)

```
app/ (React+TS, :5173)
  │  ── only talks to ─▶  data/ API (:8000)  ──parameterized SQL──▶  pulsescore.db (SQLite)
  │                         │  query seam (tokens) · auth · scope · tamper-evident audit
  │                         │  identities (encrypted PII) — resolved only via /identity/resolve
  │                         └──HTTP──▶  model_service/ (:8001)   scoring seam (tokens + features)
  │
  └── read + L3 approve ─▶  agents/ (:8002)
                             └── as the CALLING USER (forwarded bearer) ─▶ data:8000 query seam + model_service:8001
```

Agents have **no database access and no privileged path**. They call the data layer's
query API (and the scoring service) over HTTP **as the calling user**, forwarding that
user's bearer token. So every guarantee the UI has is inherited for free: permission
scope (a manager cannot widen past their division), parameterized queries, and the
tamper-evident audit trail. There is no other door.

## The two seams (contracts)

1. **Scoring seam** — `model_service` `POST /score`: feature rows (token-keyed) in,
   `[{ employee_token, flight_risk, risk_trend, value_score, reason_codes[], metrics[],
   capital[] }]` out. The trained-model swap point (`Scorer`) is untouched by the panel
   extension; a real model still drops in behind the same contract.
2. **Query seam** — `data/query.py`: `get_employees(...)` and `get_employee_360(...)`
   are the single source of employee/score data (token-only), with permission scoping +
   audit enforced **inside** the seam, not the UI.

## Layer 1 — the canonical data model & ingestion

The Talent88 v2 FINAL data model (§4 tables 0–13, §5 division signal packs, §6
placeholders) ships as an **additive overlay** (`data/schema_canonical.sql`): 16
token-keyed canonical tables added *alongside* the untouched legacy tables and seams.
The app and both seams are byte-for-byte unchanged; the synthetic generator populates
legacy and canonical tables from the **same seeded employees**, so they never drift.
Canonical Table 0 (`identity`) **is** the existing encrypted `identities` table, not a
second PII store. `data/canonical.py` is the single source of truth for the controlled
vocabularies (mirrored by the DDL `CHECK` constraints) and the **scoring wall**.

Five structural properties are enforced in code + tests:

- **Pseudonymization** — no canonical analytics table carries a name/email column.
- **Consent gating** — `collaboration_metadata` (ONA metadata, never content) exists
  only for tokens with `engagement_prefs.monitoring_consent = 1`.
- **Bias-audit + identity-proxy walling** — `gender`/`birth_year_band`/`marital_status`
  and `school_prestige`/`named_employer` live in the model but appear in
  `canonical.SCORING_EXCLUDED_COLUMNS` and in **no** `model_service` feature list. The
  *only* read path to the protected traits is `data/bias_audit.py` (aggregate cohorts,
  audited, admin-scoped), which the scorer never imports.
- **Snapshots** — engagement / collaboration / external-market / lifecycle /
  features-engineered are snapshot-keyed with ≥6 monthly rows per employee.
- **§9 RED exclusions** — message/keystroke/screenshot/biometric/SSN-style fields are
  documented atop the DDL and proven absent by a column-name scan.

**Ingestion** (`data/ingestion/`) is the write path into these tables: a deterministic,
**AI-free** pipeline — land (CSV / multi-sheet Excel / JSON / XML) → map (static
field maps) → normalize (dates, currency→base, grade→level, vocab) → resolve (exact-key;
ambiguous → human **review queue**, never auto-merged) → validate (schema / ranges /
referential / volume, **fail loudly**) → write. A Workday adapter and a six-platform
engagement-survey adapter (ingests **precomputed** scores/drivers/Q12/themes verbatim)
ship; unified-HRIS / Graph-Workspace / licensed-market integrations are documented
`NotImplementedError` seams. No ML or fuzzy matching anywhere in the transform path.

### Enterprise ingestion — reference architecture (DOCUMENTED, backend DEFERRED)

The target market (US financial firms) is a near-monoculture on **Workday** and **Oracle
HCM**, with **Radford McLagan (Aon)** as the comp-benchmark standard. The full data-
engineering design (per *Data Engineering & Ingestion Spec v1*, paired with the canonical
schema) is captured as reference architecture; the native enterprise backend is **not built
in the MVP** — the rule remains **UI-only, no new backend**, so SFTP-receive, native source
adapters, the medallion pipeline, and the validation gate are **deferred until a design
partner + explicit sign-off + a phased plan**. What ships today is the synthetic generator
plus the file-based `data/ingestion/` adapters above.

- **Three intake modes:** **A** scheduled **SFTP file drop** (CSV/tab/flat/XLSX/XML — the
  pilot default) · **B** **API pull** (production) · **C** **delta / change feed** (the
  scalable path — only what changed, with event flags).
- **Workday adapter (build-first):** RaaS (XML/JSON/CSV) · REST v1 (**partial coverage** —
  fall back to SOAP/RaaS) · SOAP WWS v46.1 · EIB (Mode A) · Core Connector: Worker (Mode C).
  Auth is **OAuth2 client-credentials with no refresh token** (explicit re-fetch) / SOAP
  ISU+ISSG. Constraints to engineer around: **no webhooks** (poll or delta), **~10 calls/sec
  per tenant** (backoff/cache), **RaaS ~2 GB / 30-min cap and JSON has no pagination — XML
  paging only**.
- **Oracle HCM adapter (second):** **HCM Extracts** + **BICC incremental** for bulk/delta;
  **REST is low-volume reads only, max 499 records/page** (`offset`/`limit`/`hasMore`) — do
  **not** use API for bulk.
- **Radford McLagan** licensed comp-benchmark adapter (🟡 paid feed, never scraped); the
  **engagement-survey adapter** (six platforms, ingests precomputed outputs verbatim); and a
  stubbed **unified-HRIS-API** seam (Merge/Knit/Apideck) for the long tail.
- **Authoritative join IDs** anchor entity resolution: `Worker_ID`, `Employee_ID`,
  `Position_ID`, `Organization_Reference`, `Security_Group_Reference`.
- **Medallion flow:** Modes A/B/C → **BRONZE** (raw, per-client isolated, encrypted) →
  **SILVER** (adapter map + normalize + native XML parse + entity resolution + manual-edit
  collision rule) → **GOLD** (monthly snapshots + engineered features + attrition label →
  `/score` seam). No AI in the runtime transform path.

The Data Management **Re-ingestion / change-preview** screen is the front end for **Mode C**:
connector tiles → dry-run diff (**"12 updated, 3 new, 1 conflict"**) → conflict resolver →
Apply/Cancel, enforcing the **manual-edit collision rule** (hand-edited fields **pinned** or
**flagged**; comp + bias-walled fields lean pinned/locked). It is currently **UI-only and
mocked** — real synthetic data in, local state out, badged "demo — not persisted", with comp
and bias-walled fields excluded from diffs by design. The **real delta backend is deferred**.

## Layer 2 — the intelligence panel

Eight transparent, weighted-heuristic scores (0–100, each with structured reason
codes), persisted long-format in `metric_scores` so adding a score never widens a table:

- Skills Depth & Adjacency · Learning Velocity · Performance & Impact ·
  **Engagement & Productivity** *(consented signals only)* · Mobility Readiness ·
  **Retention Risk** (= the original flight_risk) · Trust & Reliability ·
  Leadership Influence.

Plus **capital metrics** (in `capital_metrics`, each flagged `is_estimate`): Value
Score · Cost-to-Lose / Moneyball · Suggested Retention Investment · Retention ROI ·
Compensation Efficiency. Every dollar/ratio figure is a **modeled estimate** and is
surfaced with that caveat in the UI — never as a precise individual figure.

`refresh.py` joins the 360 inputs, calls the scoring seam, and persists the panel.
Engagement is gated on consent at **both** ends: the refresh nulls the engagement
signal across the seam when `consented_signals = 0`, and `get_employee_360` nulls it on
read. No covert monitoring.

## Layer 3 — the autonomy ladder (hard cap at L3)

```
L1  INSIGHT         read-only — surface what the data says
L2  RECOMMENDATION  propose a grounded action (inert; nothing executes)
L3  APPROVE         a human clicks approve — recorded in the audit trail + an opt-in
                    notification. NO real HR/comp side effect.
L4  AUTO-EXECUTE    ✗ NOT IMPLEMENTED. Not even defined as an enum value.
```

The ladder is explicit in code (`agents/autonomy.py`): `Level` is an `IntEnum` with
`INSIGHT=1, RECOMMENDATION=2, APPROVE=3` — `AUTO_EXECUTE` is deliberately **absent**.
`MAX_LEVEL = APPROVE`, and `enforce(level)` raises `AutonomyError` for anything `< 1`
or `> 3`. A disabled `execute_autonomously()` placeholder exists only to raise if ever
invoked. The data API's agent-audit endpoint independently bounds the level to `1–3`
(defense in depth). **L4 is banned everywhere** — see the rationale in `agents/autonomy.py`
(regulated employment decisions, GDPR Art. 22 on solely-automated decisions).

Each agent: reads via the tools, **grounds every recommendation in panel scores /
reason codes** (never invents data — says so when a signal is missing), proposes an
inert `ProposedAction`, and logs every run to the audit trail. The `/agents/{name}/approve`
endpoint is the ceiling: it records the human approval and, if the approver opted in,
creates an in-app notification — and runs no system action.

## Layer 3a — LLM reasoning seam & per-org customization (retrieval, not fine-tuning)

Agents reason in natural language through **one swappable seam** — `llm/generate(messages)`,
called only via `agents/reasoning.narrate()`. No agent talks to a model directly. The default
provider is a **local** Ollama (OpenAI-compatible) endpoint; swapping model or provider is
**env-only** (`TALENT88_LLM_*`), no code change. The model is a reasoning surface over
already-fetched, **tokenized** tool results — it is never a data source, has no DB/network/file
tool, is audited on every call, is PII-guarded, prompt-injection-fenced, local-by-default with
no silent egress, and degrades to deterministic templates when unavailable (full control list
in SECURITY.md §5e).

**Customization is retrieval + per-org context + the separately-trained risk model — NOT
fine-tuning the LLM.** At query time `agents/orgcontext.py` injects the org's static **context
profile** (its divisions, comp bands, terminology, survey definitions) plus **live aggregates
retrieved through the same permission-scoped tools client**, so the model speaks the
organization's language over its current numbers. We deliberately do **not** fine-tune the LLM
on client data, because retrieval is:

- **Current** — new data is available to the AI immediately; nothing waits on a retrain.
- **Private & deletable** — client data is never baked into shared weights; deleting rows
  deletes its influence (right-to-be-forgotten stays tractable; see SECURITY.md §1, §6).
- **Per-client isolated** — a profile loads by `org_id` only and live retrieval inherits the
  query seam's scope with no shared cache, so one org's data can never reach another's AI
  context (test-enforced). A single shared fine-tuned model could not give that guarantee.

The org-specific *prediction* (flight risk / value) still comes from the **separately-trained,
swappable model behind `POST /score`** (Layer 2) — that is where supervised learning belongs;
the LLM only narrates its tokenized outputs.

## Layer 4 — engagement (in-app, opt-in)

`notifications` + `notification_prefs` on the data API. Two guarantees, enforced in
`data/notifications.py`: **opt-in** is a hard precondition (nothing is written for a
kind the recipient has not opted into; default off) and **scope** is per-recipient (a
recipient reads/marks only their own, addressed by login email, token-only payloads, no
PII). `channel` is the abstraction seam — `in_app` for the MVP; slack/teams/email/mobile
are documented future channels, not built. Every notification carries a `why_text`.

## Modules

| Module | Responsibility |
| ------ | -------------- |
| [`data/`](data/) | The only place that touches the DB. Schema, synthetic generator, query seam, auth, tamper-evident audit, encryption + key/BYOK seam, identity-resolution boundary, notifications, HTTP API (`:8000`). |
| [`model_service/`](model_service/) | Scoring service (`:8001`). Transparent heuristics now; trained model swaps in behind `POST /score`. Tokens + features only. |
| [`agents/`](agents/) | Agent layer (`:8002`). 5 L1–L3 agents that call the seams as tools; no DB access; autonomy capped at L3. Includes `reasoning.py` (the privacy-hardened LLM bridge) and `orgcontext.py` (per-org RAG context). |
| [`llm/`](llm/) | The single LLM provider seam. `generate(messages)` over an OpenAI-compatible API; local Ollama by default; provider/model swap by env only; local-by-default with no silent egress. |
| [`app/`](app/) | React + TS + Vite UI (`:5173`). Talks only through the data API (+ agents read). Resolves names at one boundary. |

## Hard constraints honored

Synthetic data only · transparent heuristics + documented model seam (no training) ·
additive canonical overlay (legacy tables + both seams untouched, app needs no change) ·
pseudonymization + encryption + key/BYOK seams intact · single query seam + single
scoring seam (agents go through the seam, no new data path) · parameterized queries +
controlled-vocab `CHECK` constraints · bias-audit + identity-proxy **scoring wall**
(test-proven) · consent-gated collaboration metadata · ≥6 monthly snapshots · §9 RED
exclusions documented + test-enforced · AI-free ingestion with a human review queue
(never auto-merge) · least privilege + permission scope inherited by agents ·
tamper-evident audit incl. agent actions + bias-audit reads · consented-signals gate
(no covert monitoring) · opt-in transparent notifications · **NO Level 4 anywhere**.
