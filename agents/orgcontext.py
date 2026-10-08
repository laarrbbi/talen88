"""Per-organization context profiles + retrieval (RAG) — customization WITHOUT fine-tuning.

The LLM is never trained or fine-tuned on an organization's data. Instead, at query time
we RETRIEVE the org's current data through the same permission-scoped query seam the agents
already use, and combine it with a small per-org "context profile" (its divisions, comp
bands, survey definitions, and its own terminology). That combined context is injected into
the system prompt so the model speaks the organization's language. It is config + retrieved
data, never weights — so new data is available immediately (no retraining), a client's data
can be deleted by deleting rows, and one client's data is never baked into a shared model.

STRICT PER-ORG ISOLATION
  * Context profiles are keyed by org_id; loading org A never returns org B's profile. The
    registry only ever reads the single file named for the requested org.
  * Live retrieval uses ONLY the caller's scoped `tools` client, so one org's data can never
    reach another org's AI context — the isolation is inherited from the query seam's
    permission scope, not re-implemented here. There is no shared/cross-org cache.

This layer is fail-safe: if a profile is missing or live retrieval fails, it degrades to the
minimal context (or None) and the agent still works — it just speaks a little less fluently.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

# Where per-org profiles live. Overridable (tests point this at a temp dir) so the registry
# is never a shared global with cross-org bleed.
_DEFAULT_DIR = Path(__file__).resolve().parent / "org_profiles"


def active_org_id() -> str:
    """The org this process serves. Single-tenant deployments leave this at 'default';
    a multi-tenant gateway sets TALENT88_ORG_ID per request-scope before invoking agents."""
    return os.environ.get("TALENT88_ORG_ID", "default").strip() or "default"


def _profiles_dir() -> Path:
    return Path(os.environ.get("TALENT88_ORG_PROFILES_DIR", str(_DEFAULT_DIR)))


@dataclass(frozen=True)
class OrgProfile:
    """A small, static description of how one organization talks about its workforce.

    Pure config/data — never model weights. Keyed by org_id; carries no other org's content.
    """
    org_id: str
    display_name: str = ""
    terminology: dict = field(default_factory=dict)      # org's term -> what it means
    comp_bands: dict = field(default_factory=dict)        # band label -> description
    divisions: list = field(default_factory=list)         # the org's own division names
    survey_definitions: dict = field(default_factory=dict)  # survey metric -> definition
    notes: str = ""

    def render(self) -> str:
        """A compact text block for injection into the system prompt. Only this org's data."""
        lines: list[str] = []
        if self.display_name:
            lines.append(f"Organization: {self.display_name} (org_id={self.org_id}).")
        if self.divisions:
            lines.append("Divisions: " + ", ".join(self.divisions) + ".")
        if self.terminology:
            lines.append("Terminology (use these terms):")
            lines += [f"  - {term}: {meaning}" for term, meaning in self.terminology.items()]
        if self.comp_bands:
            lines.append("Compensation bands:")
            lines += [f"  - {band}: {desc}" for band, desc in self.comp_bands.items()]
        if self.survey_definitions:
            lines.append("Survey metrics:")
            lines += [f"  - {m}: {d}" for m, d in self.survey_definitions.items()]
        if self.notes:
            lines.append(self.notes)
        return "\n".join(lines)


def load_org_profile(org_id: str | None = None) -> OrgProfile:
    """Load exactly one org's profile by id. Never falls back to another org's content:
    a missing profile yields a minimal profile carrying only the requested org_id."""
    oid = (org_id or active_org_id()).strip() or "default"
    path = _profiles_dir() / f"{oid}.json"
    if not path.is_file():
        return OrgProfile(org_id=oid)
    try:
        raw = json.loads(path.read_text())
    except (ValueError, OSError):
        return OrgProfile(org_id=oid)
    # Force the org_id to the requested one — a mislabelled file cannot impersonate another org.
    return OrgProfile(
        org_id=oid,
        display_name=raw.get("display_name", ""),
        terminology=raw.get("terminology", {}) or {},
        comp_bands=raw.get("comp_bands", {}) or {},
        divisions=raw.get("divisions", []) or [],
        survey_definitions=raw.get("survey_definitions", {}) or {},
        notes=raw.get("notes", ""),
    )


def _live_aggregates(tools) -> str | None:
    """RETRIEVAL: pull the org's *current* aggregates through the scoped query seam.

    Uses only the caller's tools client, so the figures are already permission-scoped and
    audited by the data layer — and cannot include another org's data. Token-only; never PII.
    """
    if not hasattr(tools, "dashboard"):
        return None
    try:
        dash = tools.dashboard()
    except Exception:  # noqa: BLE001 — any tool failure degrades to "no live context"
        return None
    head = dash.get("headline") or {}
    if not head:
        return None
    lines = [
        "Current workforce snapshot (retrieved live, scoped to the caller):",
        f"  - scored: {head.get('total_scored', 0)}, high flight-risk: "
        f"{head.get('high_risk_count', 0)}, act-now (high risk + high value): "
        f"{head.get('act_now_count', 0)}.",
    ]
    by_div = dash.get("by_division") or []
    for d in by_div[:8]:
        name = d.get("division") or d.get("name")
        if name is None:
            continue
        hr = d.get("high_risk_count", d.get("high_risk", 0))
        lines.append(f"  - {name}: {hr} high flight-risk.")
    return "\n".join(lines)


def build_context(tools, *, org_id: str | None = None) -> str | None:
    """Assemble the per-org context block (static profile + live retrieved aggregates).

    Returns None when there is nothing useful to add (so the prompt stays lean). Built only
    from the passed-in scoped tools + this org's own profile — no cross-org state.
    """
    profile = load_org_profile(org_id)
    parts = [p for p in (profile.render(), _live_aggregates(tools)) if p]
    return "\n\n".join(parts) if parts else None
