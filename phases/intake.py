"""Phase 0 — Intake.

Tasks:
    1. Take user's free-form capability description.
    2. Use LLM to extract a canonical capability_slug (snake_case ASCII).
    3. Check memory for existing slug:
       - exact match → ask user: continue from latest / fresh / cherry-pick
       - near match (fuzzy) → present candidates, ask user to pick or confirm new
       - no match → fresh start
    4. Agent produces a draft SL2 list + axes (decision N) to seed P1.
       The draft is a starting point only; P1 iterates from there.
    5. Collect generation hyperparams (set_size, ratios, provider, model).

This phase does NOT block on user input internally — caller (CLI) gathers
input and feeds it in via a call dict.
"""
from __future__ import annotations

from ..core.schema import Run


def run(run: Run) -> None:
    """Drive P0. Mutates `run` with slug, description, draft SL2/axes."""
    raise NotImplementedError


def standardize_slug(description: str, existing: list[str]) -> tuple[str, list[str]]:
    """LLM-assisted slug normalization. Delegates to memory.store.slug_for."""
    raise NotImplementedError


def draft_dimensions(description: str, llm) -> tuple[list, list]:
    """Generate a first-cut SL2 + axes pair for review in P1.

    Returns (sl2_list, axes). These are suggestions only — user can wipe in P1.
    """
    raise NotImplementedError
