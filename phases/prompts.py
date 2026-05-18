"""Phase 2 — Prompt generation.

Steps:
    1. Build coverage matrix from SL2_list × axes cartesian product.
    2. Compute per-difficulty / per-SL2 quotas:
         - medium 60%, hard 40% (decision B = 0:3:2)
         - stress ≥ 30% (decision D1), drawn from medium+hard pool
    3. Load few-shot anchors from seed_pool[slug] (decision O).
    4. Generate prompts cell-by-cell or batched, each via structured tool-use.
    5. Each prompt declares which SL2 it covers; self-check against the
       declared SL2 list and axes-values is done by the agent and recorded.
    6. Bilingual: ZH and EN generated together to keep semantic parity.

P2 can be re-entered for partial regen (driven by P3 retry or P4 feedback).
In that case only the requested cells are regenerated, not the full set.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from ..core.schema import Run

if TYPE_CHECKING:
    from ..core.schema import PromptEntry


def run(run: Run, regenerate_ids: list[str] | None = None) -> None:
    """Generate (or partially regenerate) prompts.

    regenerate_ids: if set, only those prompts are regenerated; others kept.
    Reads:    run.sl2_list, run.axes, run.target_set_size, run.prompts (for ids)
    Writes:   run.prompts
    """
    raise NotImplementedError


def compute_quotas(set_size: int, sl2_list: list, axes: list,
                   stress_ratio: float = 0.3) -> dict:
    """Compute how many prompts per difficulty and per SL2."""
    raise NotImplementedError


def cover_matrix(sl2_list: list, axes: list) -> list[dict]:
    """Build the list of {sl2_id, axes_values} cells that must be covered ≥1.

    Returned cells are the minimum allocation; the generation budget may
    create multiple prompts per cell to hit the target set size.
    """
    raise NotImplementedError


def generate_one(
    cell: dict,
    difficulty: str,
    is_stress: bool,
    sl2_list: list,
    axes: list,
    seed_examples: list,
    llm,
) -> "PromptEntry":
    """Generate one prompt with full structured output.

    Returns a PromptEntry (not yet QA-passed). Caller is responsible for
    appending to run.prompts and running QA.
    """
    raise NotImplementedError
