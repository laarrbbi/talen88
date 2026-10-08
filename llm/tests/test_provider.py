"""LLM provider seam: config resolution, egress policy, graceful failure.

Every test mocks the HTTP layer (or asserts a refusal that never hits the network), so
the suite NEVER depends on a running Ollama/model. This is the contract that lets the
rest of the app fall back to deterministic behavior when no model is present.
"""
from __future__ import annotations

import httpx
import pytest

from llm import config as cfgmod
from llm import provider
from llm.config import LLMConfig, load_config
from llm.provider import (
    LLMConfigError,
    LLMResponse,
    LLMUnavailable,
    generate,
    preflight,
)


# ---- config: local-by-default + env-only swap --------------------------------
def test_defaults_are_local_ollama(monkeypatch):
    for var in ("TALENT88_LLM_BASE_URL", "TALENT88_LLM_MODEL", "TALENT88_LLM_API_KEY",
                "TALENT88_LLM_ALLOW_EXTERNAL", "TALENT88_LLM_TIMEOUT"):
        monkeypatch.delenv(var, raising=False)
    cfg = load_config()
    assert cfg.base_url == "http://localhost:11434/v1"
    assert cfg.model == "gemma3:1b"
    assert cfg.is_local and not cfg.is_external
    assert cfg.egress_allowed  # local is always allowed


def test_model_swaps_via_env_only(monkeypatch):
    monkeypatch.setenv("TALENT88_LLM_MODEL", "llama3.1:8b")
    assert load_config().model == "llama3.1:8b"


def test_external_host_detected(monkeypatch):
    monkeypatch.setenv("TALENT88_LLM_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.delenv("TALENT88_LLM_ALLOW_EXTERNAL", raising=False)
    cfg = load_config()
    assert cfg.is_external and not cfg.egress_allowed  # external + not opted in => blocked


# ---- egress policy: NO silent egress -----------------------------------------
def test_external_without_optin_is_refused_before_any_request(monkeypatch):
    monkeypatch.setenv("TALENT88_LLM_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.delenv("TALENT88_LLM_ALLOW_EXTERNAL", raising=False)

    # If a request were attempted, this would explode — proving none is made.
    def _boom(*a, **k):
        raise AssertionError("network call attempted to an unapproved external endpoint")
    monkeypatch.setattr(httpx, "post", _boom)

    with pytest.raises(LLMConfigError):
        generate([{"role": "user", "content": "hi"}])


def test_external_with_optin_warns_and_proceeds(monkeypatch, caplog):
    monkeypatch.setenv("TALENT88_LLM_BASE_URL", "https://api.openai.com/v1")
    monkeypatch.setenv("TALENT88_LLM_ALLOW_EXTERNAL", "true")
    cfgmod_reset_warn()
    with caplog.at_level("WARNING"):
        cfg = preflight()
    assert cfg.is_external and cfg.allow_external
    assert any("EGRESS" in r.message for r in caplog.records)


# ---- graceful failure: unreachable endpoint ----------------------------------
def test_unreachable_endpoint_raises_unavailable(monkeypatch):
    monkeypatch.setattr(httpx, "post",
                        lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError("refused")))
    with pytest.raises(LLMUnavailable):
        generate([{"role": "user", "content": "hi"}])


def test_malformed_response_raises_unavailable(monkeypatch):
    class _Resp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {"unexpected": "shape"}
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp())
    with pytest.raises(LLMUnavailable):
        generate([{"role": "user", "content": "hi"}])


# ---- happy path: parses OpenAI-style completion ------------------------------
def test_generate_parses_completion(monkeypatch):
    captured = {}

    class _Resp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self):
            return {"model": "gemma3:1b",
                    "choices": [{"message": {"content": "  3 people are high risk.  "}}]}

    def _post(url, json, headers, timeout):
        captured["url"] = url
        captured["model"] = json["model"]
        return _Resp()

    monkeypatch.setattr(httpx, "post", _post)
    out = generate([{"role": "system", "content": "be terse"},
                    {"role": "user", "content": "summarize"}])
    assert isinstance(out, LLMResponse)
    assert out.text == "3 people are high risk."   # trimmed
    assert out.model == "gemma3:1b"
    assert captured["url"].endswith("/chat/completions")


def test_generate_forwards_tools(monkeypatch):
    captured = {}

    class _Resp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self):
            return {"choices": [{"message": {"content": "ok", "tool_calls": [{"id": "1"}]}}]}

    monkeypatch.setattr(httpx, "post",
                        lambda url, json, headers, timeout: captured.update(json=json) or _Resp())
    tools = [{"type": "function", "function": {"name": "list_employees"}}]
    out = generate([{"role": "user", "content": "x"}], tools=tools)
    assert captured["json"]["tools"] == tools
    assert out.tool_calls == [{"id": "1"}]


# helper: reset the once-per-process egress warning so the warn test is deterministic
def cfgmod_reset_warn():
    provider._egress_warned.clear()
