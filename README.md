# Talent88

A **people-analytics platform** (MVP) on **synthetic data only**: it builds a unified
**Employee 360**, scores a full panel of **8 metrics + capital figures** with transparent,
explainable heuristics, layers **L1–L3 AI agents** that recommend (but never auto-execute)
grounded actions, and surfaces everything in a finance-grade UI with **opt-in**
notifications. Built as a clean modular monorepo with **strict seams** so a real ML model
and the agent layer plug in **without rewriting the UI or the data access**.

> Synthetic data only. No real personal data. Runs entirely locally — no third-party
> cloud services. **No Level-4 / auto-execution anywhere** — a human always approves.

## The four layers

1. **Unified Data (Employee 360)** — `data/`: a token-keyed record joining core HR,
   comp/perf, engagement (consented), learning, skills, mobility, and risk. The
   spec-complete **canonical schema** (Talent88 v2 FINAL, 16 tables) is an additive
   overlay that **ingestion** writes to, with the bias-audit / identity-proxy **scoring
   wall**, consent gating, and §9 RED exclusions enforced by tests.
2. **Intelligence Engine** — `model_service/`: 8 transparent scores + capital metrics,
   each with reason codes and modeled-estimate caveats, behind one `/score` contract.
3. **Agent Layer** — `agents/` (`:8002`): Conversational (read-only) + Retention /
   Career / Learning / Workforce-Planning, all calling the seams **as tools**, capped at
   **L3 (click-to-approve)**.
4. **Engagement** — in-app, **opt-in**, transparent (`why`) notifications.

Full design + seam map + autonomy ladder: [`ARCHITECTURE.md`](ARCHITECTURE.md).

## Architecture at a glance

```
app/ (React/TS, :5173)
  │  ── only talks to ─▶  data/ (:8000)  ──SQLite──▶  pulsescore.db
  │                         │ query seam (tokens) · auth · scope · audit · encrypted PII
  │                         └──HTTP──▶  model_service/ (:8001)   scoring seam
  └── read + L3 approve ─▶  agents/ (:8002)  ──as the calling user──▶  data:8000 + model_service:8001
```

| Module | Responsibility |
| ------ | -------------- |
| `data/` | **The only place that touches the DB.** Schema, synthetic generator, query seam, auth, tamper-evident audit, encryption + key/BYOK seam, name-resolution boundary, notifications, HTTP API (`:8000`). |
| `model_service/` | Scoring service (`:8001`). Transparent heuristic panel now; trained model swaps in behind `POST /score`. Tokens + features only. |
| `agents/` | Agent layer (`:8002`). 5 L1–L3 agents that call the seams as tools; **no DB access**; autonomy capped at L3. Natural-language narratives via the LLM seam over tokenized data; per-org RAG context. |
| `llm/` | Single LLM provider seam — `generate(messages)` over an OpenAI-compatible API; **local Ollama by default**; provider/model swap by env only; local-by-default with no silent egress. |
| `app/` | React + TS + Vite UI (`:5173`). Reads **only** through `data/`'s API (+ agents read). Resolves names at one boundary. |

## UI screens

Finance-grade design language (OKLCH tokens, hand-rolled inline-SVG charts, light/dark).
Type follows a Caslon + Swiss 721 pairing: Caslon for titles and figures, a Helvetica-style
sans for UI chrome, IBM Plex Mono for codes. The licensed faces are used when installed or
self-hosted; otherwise Libre Caslon Text (Google Fonts) and the system Helvetica/Arial stand in
(`--serif` / `--sans` in `design-system.css`).
Every screen is token-only and resolves names at the single `NameTag` boundary.

| Screen | Source | Notes |
| ------ | ------ | ----- |
| **Dashboard** | `pages/Dashboard.tsx` | KPI row, Risk × Value action matrix, risk-by-division bars, retention priorities. |
| **Analytics** | `pages/Analytics.tsx` | Turnover, risk drivers, compensation, engagement, managers, fairness/bias audit, cost scenarios, forecast — all from the scoped `/analytics/*` endpoints with server-side small-segment suppression. |
| **Watchlist** | `pages/Watchlist.tsx` | Segmented band filter w/ live counts, mini-bar risk, Δ QoQ from real `risk_trend`, value tier, reason-code chips. Client-side filter/sort over one in-scope fetch. |
| **Profile** | `pages/Profile.tsx` | Radial gauge, reason-code-weighted factor bars, `/360` trend chart, real mini-stats, capital metrics, skills. |
| **Company Graph** | `pages/Graph.tsx` | Reporting hierarchy from `manager_token` + division/location/risk filters (client-side), plus an *Ask the graph* panel that runs natural-language people search through the agents service (`/agents/search/people`). |
| **Surveys** | `pages/Surveys.tsx` | Live over the `/surveys/*` endpoints: build from the question library/templates, launch, collect confidential responses, read aggregate results (suppressed below each campaign's minimum group size). |
| **Data Management** | `pages/DataManagement.tsx` | Operator console. **Document Import** is a real, admin-only write path (CV/contract/offer/payslip/review → parsed → new employee). The other actions (field edits, connectors, re-ingestion, uploads) are UI-only mocks badged *"demo — not persisted"*. |
| **Agents / Inbox / Login** | `pages/*.tsx` | App-native screens (agent runs + L3 approve, opt-in notifications, email + password sign-in). |

**Data-gap empty states (backend punch-list).** Where the design shows a field the API
does not yet expose — percentile, model confidence, trend event annotations, full role
history, per-employee documents, language/cert graph filters — the UI
renders an explicit *"not yet available"* state rather than faking or hiding it.

## Quick start

Requires Python 3.11–3.13 and Node 20+.

```bash
# 0. install Python deps + an ephemeral encryption key for local dev
pip install -r requirements.txt          # fastapi, uvicorn, httpx, cryptography, pandas, openpyxl, pytest
export PULSESCORE_DATA_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")

# 1. generate the synthetic database (legacy + canonical tables, from the same seed)
python -m data.generate --seed 7
# (optional) ingest a source export into the canonical tables — AI-free pipeline
# python -m data.ingestion.run <file> --adapter workday   # or engagement_survey:peakon

# 2. start the scoring service (:8001), then populate the score panel
python -m uvicorn model_service.service:app --port 8001 &
python -m data.refresh                    # calls /score, writes scores + the 8-metric panel + capital

# 3. start the data API (:8000) and the agent layer (:8002)
python -m uvicorn data.api:app     --port 8000 &
python -m uvicorn agents.service:app --port 8002 &

# 4. run the UI (:5173)
cd app && npm install && npm run dev
```

### Optional: local LLM for agent narratives

Agents work fully without an LLM (they fall back to deterministic templates). To enable
natural-language narratives, run a **local** [Ollama](https://ollama.com) model — nothing
leaves your machine, and the agents only ever feed it tokenized, permission-scoped data:

```bash
# install Ollama (macOS: `brew install ollama`), then pull a small model and serve it
ollama pull gemma3:1b
ollama serve                                  # exposes http://localhost:11434
```

That is all — `llm/` defaults to that endpoint and model. To point at a different model or a
hosted OpenAI-compatible endpoint, set env vars only (no code change); see `.env.example`
(`TALENT88_LLM_*`). External endpoints are **opt-in** (`TALENT88_LLM_ALLOW_EXTERNAL=true`)
and never used as a silent fallback. Details in [SECURITY.md](SECURITY.md) §5e.

Open <http://localhost:5173>. Sign in as `admin@pulsescore.local` (all divisions) or a
division manager such as `technology.manager@pulsescore.local` (scoped to their division).
The demo password is printed by `python -m data.generate` and saved to `data/.demo-password`
(set `PULSESCORE_DEMO_PASSWORD` before generating to choose your own). Manage operators with
`python -m data.users list | add | set-password`.

## Tests

```bash
pytest data/tests model_service/tests agents/tests llm/tests -q   # backend (Python)
cd app && npm run build                                  # frontend strict typecheck + build
```

## Deploy (Fly.io)

The `Dockerfile` builds one container: the data API serves the built UI and forwards
`/agents/*` to the agents service, so everything is one public address on port 8080. On
start, `deploy/start.sh` creates the demo database only if none exists, applies schema
migrations to an existing one, and supervisord re-scores everyone once the scoring
service is up.

One-time setup:

```bash
fly volumes create talent88_data --size 1 --region iad     # persistent /data for the database
fly secrets set \
  PULSESCORE_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')" \
  PULSESCORE_DATA_KEY="$(python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')" \
  PULSESCORE_DEMO_PASSWORD="choose-a-long-password"
fly deploy
```

`fly.toml` sets `PULSESCORE_ENV=production`, so the app refuses to start without a strong
`PULSESCORE_SECRET` and a `PULSESCORE_DATA_KEY`. Keep the data key safe: the employee
names in the database cannot be read without it. If you skip `PULSESCORE_DEMO_PASSWORD`,
a random demo password is generated on first boot and saved to `/data/.demo-password`
(read it with `fly ssh console -C "cat /data/.demo-password"`). To add real operators, open
`fly ssh console` and run `python -m data.users add you@company.com --name "Your Name" --role admin`
(it prompts for the password).

## The two seams (contracts)

1. **Scoring seam** — `model_service` `POST /score`: feature rows (token-keyed) in;
   `[{ employee_token, flight_risk, risk_trend, value_score, reason_codes[], metrics[],
   capital[] }]` out. The trained-model swap point is untouched by the panel extension.
2. **Query seam** — `data/` `get_employees(...)` / `get_employee_360(...)`: the single
   source of employee/score data (token-only), with permission scoping + audit enforced
   **inside** the seam. Agents call it as a tool and inherit those guarantees.

## Security & privacy (first-class)

Employees are **pseudonymous tokens** everywhere; PII lives only in a separate,
**encrypted** identity table and is resolved to a name at one isolated, authorized,
audited boundary (`POST /identity/resolve`). Parameterized SQL throughout, least
privilege enforced inside the seams (and inherited by agents), tamper-evident audit log
covering agent actions, externalized encryption keys with a BYOK seam, consented-signals
gate (no covert monitoring), opt-in notifications, and safe (non-leaking) errors.
**No Level-4 auto-execution exists.** Full model: [`SECURITY.md`](SECURITY.md).
