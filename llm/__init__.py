"""Talent88 LLM seam.

A single, swappable interface — `generate(messages, tools?)` — that every agent calls.
No agent talks to a model directly. Defaults to a LOCAL Ollama endpoint (OpenAI-
compatible); switching models/providers is an env-var change only. See `config.py`
for the data-sovereignty posture (local-by-default, no silent egress).
"""
from __future__ import annotations

from .config import LLMConfig, load_config
from .provider import (
    LLMConfigError,
    LLMError,
    LLMResponse,
    LLMUnavailable,
    generate,
    preflight,
)

__all__ = [
    "generate",
    "preflight",
    "load_config",
    "LLMConfig",
    "LLMResponse",
    "LLMError",
    "LLMUnavailable",
    "LLMConfigError",
]
