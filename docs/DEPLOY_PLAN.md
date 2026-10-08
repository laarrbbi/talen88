# Talent88 — Deploy Readiness Plan

_Written 2026-10-08 against branch `claude/festive-goldberg-o5uzmm`. Tick the boxes as we go._

## Where we are

- **Code:** complete. Backend tests pass (387 passed, 10 skipped) and the 9 live end-to-end
  tests pass when enabled. The frontend type-checks and builds.
- **Deploy setup exists:** `Dockerfile` + `fly.toml` + `deploy/` build one container that runs
  all three services under supervisord, with the data API serving the website on port 8080.
- **Rehearsal result:** I started the services exactly as the container would (same ports,
  same env, same startup script) and used the site like a visitor:

  | Check | Result |
  | ----- | ------ |
  | Website loads at `/` | ✅ 200 |
  | Login | ✅ works, but with no password (see 2.2) |
  | Dashboard data | ❌ empty — startup never computes the scores (1.1) |
  | Running an agent | ❌ 502 "upstream data error" — agents look for the data API on the wrong port (1.2) |
  | Agents page list (`/agents`) | ❌ 404 — the proxy doesn't forward it (1.3) |
  | `/health` on the public service | ❌ 404 (1.4) |

  With fixes 1.1 and 1.2 applied by hand, the dashboard showed all 396 employees across
  5 divisions and the agent run returned 200. Startup (generate + score) takes ~6 s, and all
  three services together use ~170 MB of memory, so the 1 GB machine in `fly.toml` is plenty.
- **Not yet verified:** the Docker image build itself (no Docker daemon in this environment).
  Its first real run is step 3.4 (Fly builds remotely) or the CI job in 3.6.

## Decisions to make first

| # | Decision | Options | Recommendation |
| - | -------- | ------- | -------------- |
| D1 | What is the first deployment for? | **A** public demo on fake data (prospects, investors) · **B** pilot on a real company's employee data | **A first.** B needs Phase 4, which is a much bigger job. |
| D2 | Hosting | Fly.io (already configured) · others | **Fly.io.** Vercel isn't a fit: it runs short-lived functions, while this app is three always-on Python services sharing one database file. |
| D3 | Who can open the demo? | anyone with the link · shared access code · invite list | **Shared access code.** |
| D4 | AI-written narratives in the agents? | off (built-in templates) · hosted AI provider | **Off for the demo.** There's no local model on Fly; a hosted provider means data leaves the app, which needs a DPA and the explicit opt-in the code already enforces. |
| D5 | Repo visibility | public · private | **Private** — it's a commercial product. |
| D6 | Web address | `*.fly.dev` · own domain | `*.fly.dev` to start. |

## Phase 0 — Housekeeping · Claude · small

- [ ] **0.1** Open a PR from this branch into `main` and merge it (`main` currently holds only `Archive.zip`).
- [ ] **0.2** Add `.dockerignore` excluding `data/.localkey`, `*.db`, `node_modules`, `app/dist`,
      `.git`, `__pycache__`. Today `COPY data/ data/` would ship the local key and database
      from whatever machine runs the deploy.
- [ ] **0.3** Add a root `.env.example` listing every setting (see appendix).
- [ ] **0.4** Treat the encryption key that was inside `Archive.zip` as public — never reuse it.
      Production gets its own key via secrets (3.3).
- [ ] **0.5** _(You)_ Make the repo private (D5).

## Phase 1 — Make the container work · Claude · small–medium · **blockers, all reproduced**

- [ ] **1.1 Compute scores at startup.** `deploy/start.sh` runs `python -m data.generate` but
      never `python -m data.refresh` (generate even prints "Next: run the score refresh").
      Fix: start the model service, wait for its `/health`, run the refresh, then hand over to
      supervisord (or a supervisord one-shot program ordered after `model_svc`).
      _Verify:_ `/dashboard` returns a non-empty `quadrant` right after boot.
- [ ] **1.2 Point the agents at the data API.** `agents/tools.py` defaults to
      `http://localhost:8000`, but in the container the data API listens on 8080. Add
      `environment=PULSESCORE_DATA_URL="http://127.0.0.1:8080"` to `[program:agents_svc]`
      in `deploy/supervisord.conf`. _Verify:_ `POST /agents/retention/run` → 200.
- [ ] **1.3 Forward `/agents` itself.** The proxy in `data/api.py` only matches
      `/agents/{path}`, so the bare `/agents` call the Agents page makes falls through to the
      static site (404). Also stop passing upstream redirects through — `/agents/` currently
      returns `Location: http://127.0.0.1:8002/agents`. _Verify:_ the Agents page lists the agents.
- [ ] **1.4 Health check.** Add `GET /health` to the data API (confirms the DB is readable) and
      a `[[http_service.checks]]` block to `fly.toml`.
- [ ] **1.5 Container boot test.** Turn the rehearsal into an automated test that boots with
      the exact supervisord ports and env, then asserts: website at `/`, dashboard populated,
      `/agents` 200, agent run 200, `/health` 200. The existing e2e tests set
      `PULSESCORE_DATA_URL` and run the refresh themselves — which is why they passed while
      the container setup was broken.

## Phase 2 — Make the public demo safe · Claude · medium · **before sharing the link**

- [ ] **2.1 Signing secret.** `data/auth.py` falls back to `"dev-only-insecure-secret"`, and
      `fly.toml` puts a placeholder in plain `[env]`. Anyone who reads the repo could forge
      an admin login token. Remove both and refuse to start without `PULSESCORE_SECRET`.
- [ ] **2.2 Access gate (D3).** Login is email-only, and the admin email is printed in the
      README, so anyone who finds the URL is admin. Add a demo access code
      (`TALENT88_ACCESS_CODE`) checked in `auth.authenticate()`, plus a field on the login screen.
- [ ] **2.3 Token expiry.** Tokens carry no `exp` and are valid forever. Add one (e.g. 12 h).
- [ ] **2.4 Demo mode** (`TALENT88_DEMO_MODE=true`). Disable the Document Import write and
      upload endpoints (`/import/parse_document`, `/import/employee`) and hide the screen —
      it stores real people's details (name, email, salary, gender, birth date, photo), which
      don't belong in a public demo and are wiped on every restart anyway. Make
      `/import/divisions` admin-only (today any manager sees all divisions' headcounts).
- [ ] **2.5 Upgrade vulnerable packages** (found by `pip-audit`): fastapi (to get starlette
      ≥ 1.3.1; 7 advisories), python-multipart ≥ 0.0.31 (7), pypdf ≥ 6.19.0 (49),
      cryptography ≥ 49.0.0 (6), pytest ≥ 9.0.3 (1). Re-run the full suite; `pip-audit` and
      `npm audit` clean.
- [ ] **2.6 Hardening.** Run the container as a non-root user; add security headers (HSTS,
      `X-Content-Type-Options`, `frame-ancestors`/CSP); rate-limit `/auth/login`; reject
      uploads over 8 MB _before_ reading them (today `await file.read()` loads the whole file first).
- [ ] **2.7 Survey responses.** `/surveys/campaigns/{id}/respond` accepts answers for any
      employee token from any logged-in user. Require respondent = caller (or admin) for now;
      signed survey links come in 4.7.

## Phase 3 — Go live on Fly.io · You + Claude · small

- [ ] **3.1** _(You)_ Create a Fly.io account (it asks for a payment card). Then either give me
      a deploy token, or I set up GitHub Actions and you paste it as the repo secret `FLY_API_TOKEN`.
- [ ] **3.2** Check the app name `talent88` is free (otherwise pick another). Keep region
      `iad` (US East, close to the target customers).
- [ ] **3.3** Set secrets — random values generated at deploy time, never committed:
      `fly secrets set PULSESCORE_SECRET=… PULSESCORE_DATA_KEY=… TALENT88_ACCESS_CODE=…`
- [ ] **3.4** First deploy: `fly deploy`. Fly builds the image remotely, so nothing needs
      installing on your Mac. This is the first real test of the Docker build.
- [ ] **3.5** Live smoke test: wrong access code rejected; login; Dashboard numbers; Watchlist;
      Profile; Company Graph; Analytics; Surveys; Agents (run + approve); Inbox notification;
      `/health` 200; first load after the machine has gone idle.
- [ ] **3.6** CI/CD with GitHub Actions: on every PR run the backend tests, the container boot
      test (1.5), the frontend build and `pip-audit`; on merge to `main`, deploy to Fly.
- [ ] **3.7** Optional: custom domain (D6).

**Known demo behaviour (by design):** machines stop when idle and restart in a few seconds,
and every restart rebuilds the demo data — approvals, notifications and survey changes
reset. If that gets in the way of demos, add a Fly volume, but read 4.2 first.

## Phase 4 — Only for a pilot on real employee data (D1 = B) · large

- [ ] **4.1 Real login:** company SSO (OIDC/SAML) + MFA behind `auth.authenticate()`;
      short-lived tokens; roles mapped from the identity provider.
- [ ] **4.2 Real storage:** persistent volume or Postgres (`data/db.py` is the swap point).
      **Stop generating on boot** — `init_db()` drops every table, so it must never run against
      real data. Add schema migrations, automated backups and a tested restore.
- [ ] **4.3 Real data in:** load through `data/ingestion/` (Workday / survey adapters) instead
      of the generator; remove the demo users.
- [ ] **4.4 Keys:** KMS-backed `crypto.load_key()`, rotation plan, per-customer keys.
- [ ] **4.5 Audit log** shipped to append-only external storage.
- [ ] **4.6 AI provider** decision (D4) with a DPA, or a model running inside the customer's environment.
- [ ] **4.7 Signed survey links** for respondents.
- [ ] **4.8 Compliance:** privacy notice, DPIA, GDPR Art. 22 review, retention/erasure,
      penetration test, SOC 2 path (SECURITY.md §6).
- [ ] **4.9 Capacity:** one Python process per service on SQLite is fine for a demo or a small
      pilot; review it before adding many users.

## Phase 5 — Docs catch-up · Claude · small · any time

- [ ] README screens table: add Analytics, Connectors, Data Management, Document Import; note
      that Surveys now has a real backend.
- [ ] Document the optional ML scorer (`SCORING_BACKEND=ml`, LightGBM; its packages are not in
      the Docker image).
- [ ] Reconcile "runs entirely locally / synthetic data only" with the Fly deploy and Document Import.
- [ ] Add a "Deploying" section; update SECURITY.md §7's package list; settle on one product
      name (Talent88 vs PulseScore).
- [ ] _(You, optional)_ Upload `design_reference/`.

## Suggested order

Phases 0 → 1 → 2 in one working session (all code, no accounts needed). Then Phase 3
together — about 30 minutes of your time for the Fly account and token. Phase 4 only once
there's a pilot customer; Phase 5 alongside anything.

## Appendix — settings the app reads

| Setting | Purpose | Production (demo) |
| ------- | ------- | ----------------- |
| `PULSESCORE_SECRET` | signs login tokens | Fly secret, random — **required** (2.1) |
| `PULSESCORE_DATA_KEY` | encrypts names/emails at rest | Fly secret (Fernet key) |
| `PULSESCORE_DB` | database file path | default |
| `PULSESCORE_KEYFILE` | local dev key file path | unused |
| `PULSESCORE_MODEL_URL` | where the data layer finds the scorer | default `http://localhost:8001` |
| `PULSESCORE_DATA_URL` | where the agents find the data API | `http://127.0.0.1:8080` in the container (1.2) |
| `AGENTS_INTERNAL_URL` | where the proxy finds the agents | default |
| `SCORING_BACKEND` | `heuristic` or `ml` | `heuristic` |
| `PULSESCORE_MODEL_PATH`, `PULSESCORE_ML_DATASET` | ML model / training data | only with `ml` |
| `TALENT88_LLM_BASE_URL`, `_MODEL`, `_API_KEY`, `_TIMEOUT`, `_ALLOW_EXTERNAL` | AI narratives | unset / `false` (D4) |
| `TALENT88_ORG_ID`, `TALENT88_ORG_PROFILES_DIR` | per-org AI context | default |
| `TALENT88_E2E`, `TALENT88_E2E_LLM` | test switches | never set |
| `TALENT88_ACCESS_CODE`, `TALENT88_DEMO_MODE` | _new in Phase 2_ | set |
