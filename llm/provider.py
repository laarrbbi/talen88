"""The LLM provider seam — one interface every agent calls: `generate(messages, tools?)`.

No agent talks to a model directly; they all go through this function. The default
provider is **Ollama** via its OpenAI-compatible Chat Completions API at
`http://localhost:11434/v1`. Because the wire format is the OpenAI standard, the very
same code path reaches a hosted OpenAI/Anthropic-compatible endpoint — swapping models
or providers is purely an environment-variable change (see `llm/config.py`).

Guarantees:
  * Local-by-default, no silent egress. `generate()` refuses to contact a non-local
    endpoint unless it has been explicitly enabled (raises `LLMConfigError`). When an
    external endpoint is enabled, it logs a prominent egress warning exactly once.
  * Graceful failure. If the endpoint is unreachable or errors, `generate()` raises
    `LLMUnavailable` — callers turn that into a clear "AI unavailable" state and fall
    back to deterministic templates. It never crashes the agent.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import httpx

from .config import LLMConfig, load_config

logger = logging.getLogger("talent88.llm")

# So the "data will leave the environment" warning is loud but not spammed per call.
_egress_warned: set[str] = set()


class LLMError(Exception):
    """Base class for provider errors."""


class LLMUnavailable(LLMError):
    """The model endpoint could not be reached or returned an error. Callers fall back."""


class LLMConfigError(LLMError):
    """The provider is configured in a way that is refused (e.g. external egress without
    explicit opt-in). Raised BEFORE any request is sent — no data leaves the machine."""


@dataclass
class LLMResponse:
    """A single model completion. `text` is the assistant message; `tool_calls` carries
    any OpenAI-style function calls the model requested (empty when none)."""

    text: str
    model: str
    tool_calls: list[dict] = field(default_factory=list)
    raw: dict = field(default_factory=dict)


def _warn_external_once(cfg: LLMConfig) -> None:
    if cfg.base_url in _egress_warned:
        return
    _egress_warned.add(cfg.base_url)
    logger.warning(
        "LLM EGRESS ENABLED: TALENT88_LLM_BASE_URL points at an EXTERNAL host (%s). "
        "Tokenized prompt data WILL LEAVE THIS ENVIRONMENT and be sent to that endpoint. "
        "This is not the local-only default; review your data-sovereignty posture.",
        cfg.host,
    )


def preflight(cfg: LLMConfig | None = None) -> LLMConfig:
    """Validate egress policy and emit the external-endpoint warning. Returns the config.

    Raises LLMConfigError if an external endpoint is configured without explicit opt-in.
    No network call is made here.
    """
    cfg = cfg or load_config()
    if cfg.is_external:
        if not cfg.allow_external:
            raise LLMConfigError(
                f"refusing to send to external LLM host '{cfg.host}': set "
                "TALENT88_LLM_ALLOW_EXTERNAL=true to permit egress (data will leave this "
                "environment). Local-only is the default."
            )
        _warn_external_once(cfg)
    return cfg


def generate(messages: list[dict], tools: list[dict] | None = None,
             *, config: LLMConfig | None = None) -> LLMResponse:
    """Send `messages` to the configured model and return its completion.

    `messages` is an OpenAI-style chat array: [{"role": "system"|"user"|"assistant"|
    "tool", "content": "..."}]. `tools` is an optional OpenAI tool/function spec list.

    Raises:
      LLMConfigError  — external endpoint configured without explicit opt-in (no egress).
      LLMUnavailable  — endpoint unreachable / timed out / returned an error.
    """
    cfg = preflight(config)

    payload: dict = {"model": cfg.model, "messages": messages, "stream": False}
    if tools:
        payload["tools"] = tools

    headers = {"Authorization": f"Bearer {cfg.api_key}"}
    try:
        resp = httpx.post(f"{cfg.base_url}/chat/completions", json=payload,
                          headers=headers, timeout=cfg.timeout)
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPError as exc:
        # Connection refused, timeout, DNS, non-2xx — all become a graceful unavailable.
        raise LLMUnavailable(f"LLM endpoint unavailable ({type(exc).__name__})") from exc
    except ValueError as exc:  # malformed JSON
        raise LLMUnavailable("LLM returned a malformed response") from exc

    try:
        message = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMUnavailable("LLM response missing choices/message") from exc

    return LLMResponse(
        text=(message.get("content") or "").strip(),
        model=data.get("model", cfg.model),
        tool_calls=message.get("tool_calls") or [],
        raw=data,
    )
