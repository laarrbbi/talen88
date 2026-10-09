"""Agent tools — the ONLY way agents touch data.

Agents have no database access and no privileged path. They call the data layer's
query API (and, where needed, the scoring service) over HTTP **as the calling user**,
forwarding that user's bearer token. So every guarantee the UI has is inherited for
free: permission scope (a manager can't widen past their division), parameterized
queries, and the tamper-evident audit trail. Agents cannot bypass the seam because
there is no other door.

This client is deliberately thin: it exposes the data-layer reads as named "tools"
and an audit-write so an agent can record what it saw and proposed.
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from .autonomy import Level

DATA_API_URL = os.environ.get("PULSESCORE_DATA_URL", "http://localhost:8000")
_TIMEOUT = 15.0


class ToolError(Exception):
    """A tool call into the data layer failed (network, auth, or upstream error)."""


class NotFoundError(ToolError):
    """The requested record does not exist or is out of the caller's scope (404)."""


class DataTools:
    """Named, read-mostly tools over the data API, scoped to one user's token."""

    def __init__(self, token: str, base_url: str = DATA_API_URL) -> None:
        self._headers = {"Authorization": f"Bearer {token}"}
        self._base = base_url.rstrip("/")

    def _get(self, path: str, params: dict | None = None) -> Any:
        try:
            r = httpx.get(f"{self._base}{path}", headers=self._headers,
                          params=params or {}, timeout=_TIMEOUT)
            if r.status_code == 404:
                raise NotFoundError(f"{path} not found or out of scope")
            r.raise_for_status()
            return r.json()
        except httpx.HTTPError as exc:
            raise ToolError(f"data tool GET {path} failed") from exc

    def _post(self, path: str, body: dict) -> Any:
        try:
            r = httpx.post(f"{self._base}{path}", headers=self._headers,
                           json=body, timeout=_TIMEOUT)
            r.raise_for_status()
            return r.json()
        except httpx.HTTPError as exc:
            raise ToolError(f"data tool POST {path} failed") from exc

    # ---- read tools (scoped + audited inside the data layer) ----------------
    def list_employees(self, **filters: Any) -> list[dict]:
        """Tool: the query seam. Returns scoped token-only records + latest score."""
        clean = {k: v for k, v in filters.items() if v is not None}
        return self._get("/employees", clean)

    def employee_360(self, token: str) -> dict:
        """Tool: one employee's full 360 panel (scores + capital + trend)."""
        return self._get(f"/employees/{token}/360")

    def search_people(self, criteria: dict, limit: int | None = None) -> dict:
        """Tool: multi-field people search over the canonical record (token-only).
        Returns {matched, applied, unsupported_fields}. Scope + audit enforced server-side;
        walled/missing fields come back in `unsupported_fields`, never silently honored."""
        body: dict = {"criteria": criteria}
        if limit is not None:
            body["limit"] = limit
        return self._post("/employees/search", body)

    def dashboard(self) -> dict:
        """Tool: scoped dashboard aggregates (headline, quadrant, by-division)."""
        return self._get("/dashboard")

    def me(self) -> dict:
        return self._get("/me")

    # ---- audit (the only write an L1/L2 agent performs) ---------------------
    def log_run(self, *, agent: str, action: str, level: int,
                summary: dict, result_count: int = 0) -> None:
        """Record what the agent saw + proposed in the tamper-evident audit trail."""
        self._post("/audit/agent", {"agent": agent, "action": action, "level": level,
                                    "summary": summary, "result_count": result_count})

    def log_llm(self, *, agent: str, model: str, messages: list[dict], response: str,
                tool_calls: int = 0, endpoint: str | None = None,
                external: bool = False) -> None:
        """Audit one LLM call (insight-level) on the same tamper-evident trail: which
        agent, which model/endpoint, the TOKENIZED prompt that was sent, and the response.
        So any AI step is traceable for a security review. Prompts are token-only by
        construction (the agent only ever feeds the model query-seam output)."""
        self._post("/audit/agent", {
            "agent": f"{agent}:llm", "action": "llm_generate",
            "level": int(Level.INSIGHT),
            "summary": {"model": model, "endpoint": endpoint, "external": external,
                        "tool_calls": tool_calls,
                        "prompt": [{"role": m["role"], "content": m["content"]} for m in messages],
                        "response": response[:2000]},
            "result_count": 1,
        })

    # ---- engagement (L3 approval -> opt-in in-app notification) --------------
    def notify(self, *, recipient_email: str, kind: str, payload: dict,
               why_text: str | None = None) -> dict:
        """Create an in-app notification for an operator. The data layer drops it if
        the recipient has not opted into this kind (opt-in is enforced server-side)."""
        return self._post("/notifications", {"recipient_email": recipient_email,
                                             "kind": kind, "payload": payload,
                                             "why_text": why_text})
