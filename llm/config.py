"""LLM provider configuration — env-only, swappable, local-by-default.

The entire provider is configured through environment variables so the model can be
swapped (a bigger local model, or a hosted OpenAI/Anthropic-compatible endpoint)
WITHOUT a code change. The defaults point at a **local** Ollama instance speaking the
OpenAI-compatible API, so out of the box no data ever leaves the machine.

Data-sovereignty posture (regulated financial clients):
  * Local-only by default. `base_url` defaults to localhost Ollama.
  * NO silent egress. If `base_url` resolves to a NON-local host, the provider refuses
    to send unless `TALENT88_LLM_ALLOW_EXTERNAL=true` is set explicitly. Switching to an
    external endpoint is therefore a deliberate, documented config change — never an
    automatic fallback.
  * Loud when external. When an external endpoint IS explicitly enabled, the provider
    logs a prominent warning that data will leave the environment.

Environment variables (see `.env.example`):
  TALENT88_LLM_BASE_URL        default http://localhost:11434/v1   (Ollama OpenAI API)
  TALENT88_LLM_MODEL           default gemma3:1b
  TALENT88_LLM_API_KEY         default "ollama"  (Ollama ignores it; hosted needs a real key)
  TALENT88_LLM_ALLOW_EXTERNAL  default false     (must be "true" to use a non-local host)
  TALENT88_LLM_TIMEOUT         default 30        (seconds)
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse

DEFAULT_BASE_URL = "http://localhost:11434/v1"
DEFAULT_MODEL = "gemma3:1b"
DEFAULT_API_KEY = "ollama"
DEFAULT_TIMEOUT = 30.0

# Hosts that count as "on this machine" — traffic to these never leaves the environment.
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]", ""}


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class LLMConfig:
    """Resolved provider configuration. Immutable; built from the environment."""

    base_url: str
    model: str
    api_key: str
    allow_external: bool
    timeout: float

    @property
    def host(self) -> str:
        return (urlparse(self.base_url).hostname or "").lower()

    @property
    def is_local(self) -> bool:
        """True iff the endpoint is on this machine (no network egress)."""
        return self.host in _LOCAL_HOSTS

    @property
    def is_external(self) -> bool:
        return not self.is_local

    @property
    def egress_allowed(self) -> bool:
        """Whether a request to this endpoint is permitted to proceed.

        Local endpoints are always allowed. A non-local (external) endpoint is allowed
        ONLY when the operator has explicitly opted in via TALENT88_LLM_ALLOW_EXTERNAL.
        This is the no-silent-egress guarantee.
        """
        return self.is_local or self.allow_external


def load_config() -> LLMConfig:
    """Build the provider config from the current environment (defaults = local Ollama)."""
    return LLMConfig(
        base_url=os.environ.get("TALENT88_LLM_BASE_URL", DEFAULT_BASE_URL).rstrip("/"),
        model=os.environ.get("TALENT88_LLM_MODEL", DEFAULT_MODEL),
        api_key=os.environ.get("TALENT88_LLM_API_KEY", DEFAULT_API_KEY),
        allow_external=_truthy(os.environ.get("TALENT88_LLM_ALLOW_EXTERNAL")),
        timeout=float(os.environ.get("TALENT88_LLM_TIMEOUT", DEFAULT_TIMEOUT)),
    )
