"""Phase 1 — dimensions sizing helpers.

The actual LLM-driven SL2 + axes generation lives in
`web/llm_phases.generate_dimensions_real` (called from web.app); this
module just exposes the small pure helper `compute_min_set_size`.
"""
from __future__ import annotations

# Knobs for dimension constraints — referenced by prompt construction and
# the dimensions UI. Constants kept here so they have a single source of
# truth.
MAX_SL2 = 20
MIN_AXIS_VALUES = 2
MAX_AXIS_VALUES = 6


def compute_min_set_size(axes: list, multiplier: float = 1.5,
                         floor: int = 40, cap: int = 120) -> int:
    """C3 dynamic sizing: ceil(cartesian_product × multiplier), clamped.

    `axes` is an iterable of objects with a `.values` attribute (Axis from
    core.schema). Used by both the web flow and the CLI to pick a default
    target_set_size when the user picks "auto".
    """
    from math import ceil
    if not axes:
        return floor
    product = 1
    for a in axes:
        product *= len(a.values)
    return max(floor, min(cap, ceil(product * multiplier)))
