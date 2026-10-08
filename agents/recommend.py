"""Shared building blocks for the L1-L3 recommending agents.

A `Recommendation` bundles an insight-derived proposal (L2) with a `proposed_action`
the user can approve (L3). Nothing here executes anything: a proposed action is inert
data until a human approves it via the agent service's `/approve` endpoint, which
records the approval in the audit trail (and, once the engagement layer is wired, also
creates an opt-in notification). All dollar figures are MODELED ESTIMATES.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .autonomy import Level, enforce


@dataclass
class ProposedAction:
    """An inert, human-approvable action (L3). Carries no side effect by itself."""
    kind: str                       # e.g. "compensation_review"
    employee_token: str
    level: int = int(Level.APPROVE)
    params: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        enforce(self.level)         # defense in depth: never above L3


@dataclass
class Recommendation:
    employee_token: str
    title: str
    rationale: str                  # grounded in reason codes / panel — never invented
    drivers: list[dict] = field(default_factory=list)   # the reason codes it read
    estimates: dict = field(default_factory=dict)        # modeled $ figures (is_estimate)
    proposed_action: ProposedAction | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        if self.estimates:
            d["estimates_caveat"] = "modeled estimate — not a precise individual figure"
        return d


def capital_lookup(employee_360: dict) -> dict:
    """Pull the capital figures (modeled estimates) out of a 360 record, keyed by metric."""
    return {c["metric"]: c for c in employee_360.get("capital", [])}


def panel_lookup(employee_360: dict) -> dict:
    """Pull panel scores out of a 360 record: metric -> {score, reason_codes}."""
    return {m["metric"]: m for m in employee_360.get("panel", [])}
