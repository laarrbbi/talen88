"""Single-origin deployment: the data API forwards /agents and /agents/* to the agents
service. `GET /agents` (the agent list) must be forwarded too, not fall through to the
SPA mount and 404."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from data import api


class _FakeResponse:
    status_code = 200
    content = b'{"ok": true}'
    headers = {"content-type": "application/json"}


@pytest.fixture()
def calls(monkeypatch):
    seen: list[tuple[str, str]] = []

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def request(self, method, url, **kw):
            seen.append((method, url))
            return _FakeResponse()

    monkeypatch.setattr(api.httpx, "AsyncClient", _FakeClient)
    return seen


@pytest.mark.parametrize("method,path,target", [
    ("GET", "/agents", "/agents"),
    ("POST", "/agents/retention/run", "/agents/retention/run"),
    ("POST", "/agents/chat", "/agents/chat"),
])
def test_agent_paths_are_forwarded(calls, method, path, target):
    with TestClient(api.app) as client:
        res = client.request(method, path, json={} if method == "POST" else None)
    assert res.status_code == 200 and res.json() == {"ok": True}
    assert calls == [(method, f"{api._AGENTS_INTERNAL}{target}")]
