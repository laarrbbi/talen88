"""Live end-to-end: all three services booted, driven over real HTTP.

This is the real thing — `model_service` (:scoring), the data API, and the agent
layer are each started as their own uvicorn subprocess against a freshly generated,
freshly scored throwaway database, and exercised through their public HTTP surface
exactly as the browser would. It proves the seams hold end to end:

  * permission scope — an admin sees every division; a division manager is fenced
    to their own and cannot read another division's records (the agent inherits
    this for free because it forwards the caller's bearer token).
  * grounded narration — the conversational + retention agents return an answer
    over scoped, token-only results (LLM narrative when available, deterministic
    template otherwise — never a blank).
  * audit — every agent run lands a row on the tamper-evident trail, bounded to
    L1-L3.
  * the L3 ceiling — an approve at L4 is rejected; an approve at L3 records intent
    and executes no side effect.

It is GATED behind `TALENT88_E2E=1` (boots real processes + binds ports, so it is
opt-in and skipped in the default unit run). The optional live-LLM assertion is
further gated behind `TALENT88_E2E_LLM=1` and a reachable Ollama.

    TALENT88_E2E=1 .venv/bin/python -m pytest agents/tests/test_e2e_live.py -q
"""
from __future__ import annotations

import contextlib
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("TALENT88_E2E"),
    reason="live e2e (boots services + binds ports); set TALENT88_E2E=1 to run",
)

REPO_ROOT = Path(__file__).resolve().parents[2]
BOOT_TIMEOUT = 40.0  # seconds to wait for each service /health


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_health(url: str, proc: subprocess.Popen, ping: str = "/health") -> None:
    """Wait until the service is listening. Any HTTP response (even 404/401) proves
    the process bound the port — not every service exposes /health."""
    deadline = time.time() + BOOT_TIMEOUT
    last = None
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"service at {url} exited early (code {proc.returncode})")
        try:
            r = httpx.get(f"{url}{ping}", timeout=2.0)
            if r.status_code < 500:
                return
        except httpx.HTTPError as exc:  # socket not open yet
            last = exc
        time.sleep(0.4)
    raise RuntimeError(f"service at {url} did not become healthy: {last}")


def _spawn(module_app: str, port: int, env: dict) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", module_app, "--port", str(port),
         "--log-level", "warning"],
        cwd=str(REPO_ROOT), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


@pytest.fixture(scope="module")
def stack(tmp_path_factory):
    """Generate + score a throwaway DB, boot all three services, hand back base URLs."""
    from cryptography.fernet import Fernet

    db_file = tmp_path_factory.mktemp("e2e") / "live.db"
    model_port, data_port, agents_port = _free_port(), _free_port(), _free_port()
    data_url = f"http://127.0.0.1:{data_port}"
    agents_url = f"http://127.0.0.1:{agents_port}"
    model_url = f"http://127.0.0.1:{model_port}"

    env = dict(os.environ)
    env["PULSESCORE_DB"] = str(db_file)
    env.setdefault("PULSESCORE_DATA_KEY", Fernet.generate_key().decode())
    env.setdefault("PULSESCORE_SECRET", "e2e-only-secret")
    env.setdefault("PULSESCORE_DEMO_PASSWORD", "e2e-demo-password")
    env["PULSESCORE_MODEL_URL"] = model_url
    env["PULSESCORE_DATA_URL"] = data_url   # the agent layer's tools client reads this

    # The agent layer narrates via the LLM seam. For the deterministic e2e we point it
    # at a dead local endpoint so every narrate fails fast and falls back to the
    # grounded template (fast + reproducible). The opt-in LLM smoke (TALENT88_E2E_LLM)
    # leaves the default local-Ollama config in place so the real seam is exercised.
    agents_env = dict(env)
    if os.environ.get("TALENT88_E2E_LLM"):
        # Real local model: give the (small, slow) model generous headroom to respond.
        agents_env.setdefault("TALENT88_LLM_TIMEOUT", "180")
    else:
        agents_env["TALENT88_LLM_BASE_URL"] = "http://127.0.0.1:9/v1"  # refused instantly
        agents_env["TALENT88_LLM_TIMEOUT"] = "1"

    # Build + score in-process (the same code paths the CLI uses), against the temp DB.
    for k in ("PULSESCORE_DB", "PULSESCORE_DATA_KEY", "PULSESCORE_SECRET",
              "PULSESCORE_DEMO_PASSWORD"):
        os.environ[k] = env[k]
    from data import generate, refresh
    from data.db import get_connection

    generate.build(seed=7)

    procs: list[subprocess.Popen] = []
    try:
        model = _spawn("model_service.service:app", model_port, env)
        procs.append(model)
        _wait_health(model_url, model)

        # Refresh scores across the seam (data layer -> model service over HTTP).
        conn = get_connection()
        try:
            n = refresh.refresh_scores(conn, score_batch=refresh.http_score_batch(url=model_url))
        finally:
            conn.close()
        assert n > 0, "score refresh wrote no rows"

        data = _spawn("data.api:app", data_port, env)
        procs.append(data)
        _wait_health(data_url, data)

        agents = _spawn("agents.service:app", agents_port, agents_env)
        procs.append(agents)
        _wait_health(agents_url, agents)

        yield {"data": data_url, "agents": agents_url, "db": db_file}
    finally:
        for p in reversed(procs):
            with contextlib.suppress(Exception):
                p.terminate()
        for p in reversed(procs):
            with contextlib.suppress(Exception):
                p.wait(timeout=5)


def _login(data_url: str, email: str) -> str:
    r = httpx.post(f"{data_url}/auth/login",
                   json={"email": email, "password": os.environ["PULSESCORE_DEMO_PASSWORD"]},
                   timeout=10.0)
    r.raise_for_status()
    return r.json()["token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def admin_token(stack) -> str:
    return _login(stack["data"], "admin@pulsescore.local")


@pytest.fixture(scope="module")
def manager_token(stack) -> str:
    return _login(stack["data"], "technology.manager@pulsescore.local")


# ---- permission scope --------------------------------------------------------
def test_admin_sees_all_divisions_manager_is_fenced(stack, admin_token, manager_token):
    admin_emps = httpx.get(f"{stack['data']}/employees",
                           headers=_auth(admin_token), timeout=15.0).json()
    mgr_emps = httpx.get(f"{stack['data']}/employees",
                         headers=_auth(manager_token), timeout=15.0).json()

    admin_divs = {e["division"] for e in admin_emps}
    mgr_divs = {e["division"] for e in mgr_emps}
    assert len(admin_divs) == 5
    assert mgr_divs == {"technology"}, "manager must be fenced to their division"
    assert len(mgr_emps) < len(admin_emps)


def test_manager_cannot_widen_scope_via_query(stack, manager_token):
    # Asking for another division as a scoped manager is clamped to their own scope:
    # the seam never honors a filter that would widen past the caller's division.
    rows = httpx.get(f"{stack['data']}/employees", params={"division": "operations"},
                     headers=_auth(manager_token), timeout=15.0).json()
    divs = {r["division"] for r in rows}
    assert divs == {"technology"}, "scope must clamp a foreign-division filter, not honor it"


def test_people_search_is_scoped_and_token_only_over_http(stack, admin_token, manager_token):
    # The multi-field search endpoint inherits the seam's scope over real HTTP: an admin
    # spans divisions; a manager naming another division is clamped to their own; and a
    # field the schema lacks comes back as unsupported (never faked). Token-only throughout.
    admin = httpx.post(f"{stack['data']}/employees/search",
                       json={"criteria": {"license": "CFA"}, "limit": 200},
                       headers=_auth(admin_token), timeout=15.0).json()
    assert len({m["division"] for m in admin["matched"]}) > 1  # admin spans divisions

    mgr = httpx.post(f"{stack['data']}/employees/search",
                     json={"criteria": {"division": "operations", "language": "French"}},
                     headers=_auth(manager_token), timeout=15.0).json()
    assert {m["division"] for m in mgr["matched"]} <= {"technology"}  # clamped to own scope
    assert "language" in mgr["unsupported_fields"]                    # missing field named
    for m in mgr["matched"]:
        assert "token" in m and not ({"name", "full_name", "email"} & set(m))


def test_manager_cannot_read_foreign_record_360(stack, admin_token, manager_token):
    ops = httpx.get(f"{stack['data']}/employees", params={"division": "operations"},
                    headers=_auth(admin_token), timeout=15.0).json()
    assert ops, "admin should see operations employees"
    foreign = ops[0]["token"]
    r = httpx.get(f"{stack['data']}/employees/{foreign}/360",
                  headers=_auth(manager_token), timeout=15.0)
    assert r.status_code == 404  # out of scope -> not found (no cross-tenant leak)


# ---- grounded, scoped agent narration ----------------------------------------
def test_conversational_answer_is_grounded(stack, admin_token):
    r = httpx.post(f"{stack['agents']}/agents/conversational/ask",
                   json={"question": "who is high-risk and underpaid?"},
                   headers=_auth(admin_token), timeout=30.0)
    r.raise_for_status()
    body = r.json()
    assert body["intent"] == "risk_and_underpaid"
    assert isinstance(body["answer"], str) and body["answer"].strip()  # never blank
    # Matched rows are token-only (no name/email crosses the analytics boundary).
    for m in body["matched"]:
        assert "token" in m
        assert not ({"name", "full_name", "email"} & set(m))


def test_retention_run_is_scoped_and_grounded(stack, manager_token):
    r = httpx.post(f"{stack['agents']}/agents/retention/run",
                   json={"division": None}, headers=_auth(manager_token), timeout=30.0)
    r.raise_for_status()
    run = r.json()
    # The manager's run can only ever touch their own division (scope inherited).
    for rec in run["recommendations"]:
        emp = httpx.get(f"{stack['data']}/employees/{rec['employee_token']}/360",
                        headers=_auth(manager_token), timeout=15.0)
        assert emp.status_code == 200  # every rec is inside the manager's scope
    # Either grounded recommendations or an honest empty-scope note — never invented.
    assert run["recommendations"] or run["insight"].get("note")


# ---- audit -------------------------------------------------------------------
def test_agent_runs_are_audited_and_bounded(stack, admin_token):
    # Drive a run, then read the tamper-evident trail straight off the DB file.
    httpx.post(f"{stack['agents']}/agents/conversational/ask",
               json={"question": "who is at high flight risk?"},
               headers=_auth(admin_token), timeout=30.0).raise_for_status()

    from data.db import get_connection
    os.environ["PULSESCORE_DB"] = str(stack["db"])
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT action, filters_json FROM audit_log WHERE action LIKE 'agent:%'").fetchall()
    finally:
        conn.close()
    assert rows, "no agent audit rows were written"
    import json
    for r in rows:
        level = json.loads(r["filters_json"]).get("level")
        assert level is None or 1 <= level <= 3  # L4 never appears on the trail


# ---- the L3 ceiling ----------------------------------------------------------
def test_approve_above_l3_is_rejected(stack, manager_token):
    r = httpx.post(f"{stack['agents']}/agents/retention/approve",
                   json={"kind": "compensation_review", "employee_token": "emp_x",
                         "level": 4},
                   headers=_auth(manager_token), timeout=15.0)
    assert r.status_code in (403, 422)  # bounded out (pydantic le=3 -> 422)


def test_approve_at_l3_records_without_side_effect(stack, manager_token):
    tech = httpx.get(f"{stack['data']}/employees", params={"division": "technology"},
                     headers=_auth(manager_token), timeout=15.0).json()
    token = tech[0]["token"]
    r = httpx.post(f"{stack['agents']}/agents/retention/approve",
                   json={"kind": "compensation_review", "employee_token": token},
                   headers=_auth(manager_token), timeout=15.0)
    r.raise_for_status()
    body = r.json()
    assert body["executed"] is False
    assert body["approved"]["level"] == 3


# ---- optional: live LLM narration is exercised AND audited -------------------
@pytest.mark.skipif(
    not os.environ.get("TALENT88_E2E_LLM"),
    reason="live-LLM smoke; set TALENT88_E2E_LLM=1 with a reachable Ollama to run",
)
def test_live_llm_narrative_is_generated_and_audited(stack, admin_token):
    httpx.post(f"{stack['agents']}/agents/conversational/ask",
               json={"question": "where is my biggest workforce risk?"},
               headers=_auth(admin_token), timeout=200.0).raise_for_status()

    from data.db import get_connection
    os.environ["PULSESCORE_DB"] = str(stack["db"])
    conn = get_connection()
    try:
        llm_rows = conn.execute(
            "SELECT COUNT(*) FROM audit_log WHERE action = 'agent:conversational:llm:llm_generate'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert llm_rows > 0, "LLM seam fired but left no audit row (should be impossible)"
