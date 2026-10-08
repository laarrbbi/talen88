"""The autonomy ceiling is a hard guardrail: L1-L3 allowed, L4 refused everywhere."""
from __future__ import annotations

import pytest

from agents.autonomy import (MAX_LEVEL, AutonomyError, Level, enforce,
                             execute_autonomously)


def test_ceiling_is_level_3():
    assert int(MAX_LEVEL) == 3


@pytest.mark.parametrize("lvl", [1, 2, 3])
def test_levels_1_to_3_allowed(lvl):
    assert int(enforce(lvl)) == lvl


@pytest.mark.parametrize("lvl", [4, 5, 0, -1])
def test_level_4_and_out_of_range_refused(lvl):
    with pytest.raises(AutonomyError):
        enforce(lvl)


def test_level_4_enum_not_defined():
    # Auto-execution must not even be expressible as a named level.
    assert not hasattr(Level, "AUTO_EXECUTE")


def test_auto_execute_placeholder_is_disabled():
    with pytest.raises(AutonomyError):
        execute_autonomously()
