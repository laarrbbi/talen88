# `agents/` — agent layer (:8002)

Agents that answer questions and (later phases) recommend interventions over the
workforce data. They call the **data query API + scoring service as tools** — there
is **no separate data path** and agents have **no DB access**. The caller's bearer
token is forwarded to the data layer, so every agent inherits the same **permission
scope** (a manager only ever sees their division), **parameterized queries**, and the
**tamper-evident audit trail** the UI has. There is no door around the seam.

> Autonomy is capped at **Level 3** everywhere. **Level 4 / auto-execution is OUT OF
> SCOPE** — not built; left as a documented, disabled placeholder in `autonomy.py`.

## Run

```bash
# data API (:8000) and model service (:8001) must be running first
uvicorn agents.service:app --reload --port 8002
pytest agents/tests -q
```

Quick check (read-only conversational agent):
```bash
TOKEN=...   # from POST :8000/auth/login
curl -s localhost:8002/agents/conversational/ask \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"question":"who is high-risk and underpaid?"}' | python -m json.tool
```

## Endpoints
| Endpoint | Level | Purpose |
| -------- | ----- | ------- |
| `POST /agents/conversational/ask` | L1 | read-only NL Q&A |
| `POST /agents/{name}/run` | L1–L2 | insight + inert recommendations (`{division}` for retention/workforce, `{token}` for career/learning) |
| `POST /agents/{name}/approve` | L3 | a human approves one proposed action — records it in the audit trail + (opt-in) notifies the approver. **Runs no side effect.** |
| `GET /agents` · `GET /health` | — | catalog + autonomy ceiling |

`/approve` is the ceiling: `enforce()` and the request schema both refuse anything above
L3, and the only writes are an audit row and an opt-in notification — never comp/HR.

## The autonomy ladder (`autonomy.py`)
`L1 insight → L2 recommendation → L3 click-to-approve`. `enforce(level)` refuses
anything above `MAX_LEVEL` (= L3). `execute_autonomously()` is a **disabled** L4
placeholder that fails closed — it must never be wired to anything. See the module
docstring for *why* L4 is deferred (regulated-employment / automated-decision rules).

## Agents
| Agent | Max level | Scope | What it does |
| ----- | --------- | ----- | ------------ |
| conversational | L1 | ask | Read-only NL Q&A over the query seam |
| retention | L3 | division | Picks an intervention per high flight-risk person from their **top reason code** (comp/promotion/manager/tenure); attaches cost-to-lose + suggested-investment estimates |
| career | L3 | token | Promotion readiness (mobility + perf) + skill growth areas → nomination / mentor |
| learning | L3 | token | Skill gaps below the proficiency bar → learning paths, weighted by learning velocity |
| workforce_planning | L3 | division | Succession gaps: high flight-risk leaders vs. ready successors → hire / develop |

All five agents are **active**. Every run is logged to the audit trail
(`POST :8000/audit/agent`) with what the agent saw and proposed. Agents read the model's
reason codes / panel scores; they **never invent scores or data** — when a signal is
missing they say so (see `note` in the response).

## Conversational intelligence seam (rule-based now, LLM later)
`conversational.plan()` is a transparent, deterministic intent/slot parser — fully
local, no third-party cloud, auditable and testable. A future LLM-backed planner drops
in behind the same `plan()` boundary, emitting the same structured `Plan` (intent +
tool + filters), so the executor, scoping, and audit are unchanged.

## Tools (`tools.py`)
`DataTools(token)` exposes the data layer as named tools: `list_employees(**filters)`
(the query seam), `employee_360(token)` (full score/capital panel), `dashboard()`,
`me()`, `log_run(...)` (an audited record of the run), and `notify(...)` (creates an
**opt-in** in-app notification on L3 approval — the data layer drops it if the recipient
has not opted in). All scoped to the caller's token.
