"""The autonomy ladder — a hard, central guardrail shared by every agent.

    L1  insight          read-only; surfaces what the data/scores say
    L2  recommendation   proposes an intervention, still no side effects
    L3  click-to-approve prepares an action a HUMAN must approve before anything happens
    L4  auto-execution   OUT OF SCOPE — NOT IMPLEMENTED (see note below)

Every agent action declares its level; `enforce()` refuses anything above MAX_LEVEL.
Nothing in this codebase performs an action without a human approving it first.

WHY L4 IS DEFERRED (do not enable without a deliberate, gated rollout):
    Level 4 would let the system auto-execute a previously approved KIND of action
    (e.g. auto-send a retention offer) without a human in the loop for that specific
    instance. For a people-analytics system that influences pay, promotion, and
    employment decisions, that crosses into regulated-employment territory (adverse
    action, discrimination, works-council / GDPR Art. 22 automated-decision rules).
    It must be a per-action, opt-in, separately reviewed capability — so it is left
    here only as a documented, disabled placeholder.
"""
from __future__ import annotations

from enum import IntEnum


class Level(IntEnum):
    INSIGHT = 1
    RECOMMENDATION = 2
    APPROVE = 3
    # AUTO_EXECUTE = 4  # INTENTIONALLY NOT DEFINED — auto-execution is out of scope.


# The ceiling. Raising this (or defining Level 4) is a deliberate, reviewed decision.
MAX_LEVEL = Level.APPROVE


class AutonomyError(Exception):
    """Raised when an action exceeds the permitted autonomy ceiling."""


def enforce(level: int) -> Level:
    """Validate a requested autonomy level. Refuses L4+ (auto-execution)."""
    if level < Level.INSIGHT or level > MAX_LEVEL:
        raise AutonomyError(
            f"autonomy level {level} not permitted; capped at L{int(MAX_LEVEL)} "
            "(human approval required — auto-execution is out of scope)"
        )
    return Level(level)


def execute_autonomously(*_args, **_kwargs):
    """DISABLED Level-4 placeholder. Auto-execution is intentionally not built.

    Kept as an explicit, named seam so the boundary is visible in code review. It must
    never be wired to anything; calling it fails closed.
    """
    raise AutonomyError(
        "Level 4 auto-execution is out of scope and disabled. Actions require explicit "
        "human approval (L3). Enabling L4 is a future, per-action, opt-in, compliance-"
        "gated capability."
    )
