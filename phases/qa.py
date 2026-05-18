"""Phase 3 — Machine QA gate.

Two-tier:

  1. Deterministic rules (fast, fail-fast):
       length bounds, banned terms, required fields, bilingual non-empty,
       difficulty score matches declared, axes_values is subset of axes spec,
       sl2_covered all exist in sl2_list

  2. LLM judge (slower, semantic):
       naturalness 0-10 (threshold 7),
       coverage matrix matches (every SL2 × axes cell hit ≥ 1),
       stress ratio within [0.28, 0.32]

If fails → orchestrator regens locally (max 2 retries).
On final fail → flag entries with needs_human_review and pass through to P4.
"""
from __future__ import annotations

from ..core.schema import Run


def run(run: Run) -> bool:
    """Run QA on the current run.prompts.

    Returns True if all checks pass, False otherwise. Side effect: tags
    individual PromptEntry instances with QA flags (stored in run extras).
    """
    raise NotImplementedError


def check_rules(prompt) -> list[str]:
    """Deterministic checks on one PromptEntry. Returns error strings."""
    raise NotImplementedError


def check_coverage(run: Run) -> dict:
    """Coverage matrix check across the set. Returns {hit_cells, missing_cells}."""
    raise NotImplementedError


def check_stress_ratio(prompts: list) -> tuple[float, bool]:
    """Compute actual stress ratio. Returns (ratio, within_window)."""
    raise NotImplementedError
