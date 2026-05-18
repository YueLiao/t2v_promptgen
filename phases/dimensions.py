"""Phase 1 — SL2 & Axes iteration (≤ 5 rounds).

Each round:
    1. Full rewrite of SL2 list + axes (no incremental edits to prevent drift).
    2. LLM receives:
         - Original user description
         - Previous round's SL2/axes (if any)
         - User feedback from previous round
         - Constraints: SL2 ≤ 20, each axis 2-6 values, axes orthogonal
    3. Result is validated structurally and returned to orchestrator for user review.

The orchestrator owns the loop; this module only exposes `run_round()`.
"""
from __future__ import annotations

from ..core.schema import Run

MAX_SL2 = 20
MIN_AXIS_VALUES = 2
MAX_AXIS_VALUES = 6


def run_round(run: Run) -> None:
    """One round of dimension generation.

    Reads:    run.user_description, run.sl2_list, run.axes, run.p1_round
    Writes:   run.sl2_list, run.axes, increments run.p1_round
    """
    raise NotImplementedError


def compute_min_set_size(axes: list, multiplier: float = 1.5,
                         floor: int = 40, cap: int = 120) -> int:
    """C3 dynamic sizing: ceil(cartesian_product × 1.5), clamped to [floor, cap]."""
    from math import ceil
    if not axes:
        return floor
    product = 1
    for a in axes:
        product *= len(a.values)
    return max(floor, min(cap, ceil(product * multiplier)))


def validate_structure(sl2_list: list, axes: list) -> list[str]:
    """Structural validation. Returns list of error strings (empty = ok)."""
    raise NotImplementedError
