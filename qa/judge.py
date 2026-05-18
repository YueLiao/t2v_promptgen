"""LLM-judge semantic checks.

Uses temperature 0.0 for stability. The judge prompts are fixed templates
loaded from `prompts_templates/` (separate dir for prompt versioning).
"""
from __future__ import annotations

from ..core.schema import Run


def naturalness(prompt_zh: str, prompt_en: str, llm) -> int:
    """Return 0-10 naturalness score. Threshold for pass = 7."""
    raise NotImplementedError


def coverage_audit(run: Run, llm) -> dict:
    """Verify the agent-declared sl2_covered fields actually match the prompt.

    The agent might claim it covers SL2-A but the prompt doesn't really test
    that SL2. This judge re-reads each prompt and re-classifies.
    Returns: {prompt_id: {declared: [...], judged: [...], missing: [...]}}
    """
    raise NotImplementedError
