"""Seed pool — historical confirmed prompts per capability, used as few-shot
anchors during P2 generation.

Policy (locked):
    - 200 entries per capability hard cap
    - On overflow: time-based eviction (drop oldest by `generated_at`)
    - Only PromptEntry instances that survived P4 confirmation are added
    - Quality-based eviction is v2

Layout:
    ~/.t2v_promptgen/memory/seed_pool/
    └── {slug}.jsonl       (one PromptEntry per line, append-only-ish)
"""
from __future__ import annotations

from pathlib import Path

from ..core.schema import PromptEntry

MAX_POOL_SIZE = 200
DEFAULT_SEED_ROOT = Path.home() / ".t2v_promptgen" / "memory" / "seed_pool"


def add(slug: str, entries: list[PromptEntry],
        root: Path = DEFAULT_SEED_ROOT) -> int:
    """Append entries to pool. If over MAX_POOL_SIZE, evict oldest first.

    Returns final pool size after eviction.
    """
    raise NotImplementedError


def sample(slug: str, k: int, sl2_filter: list[str] | None = None,
           axes_filter: dict[str, str] | None = None,
           root: Path = DEFAULT_SEED_ROOT) -> list[PromptEntry]:
    """Sample k entries from the pool for few-shot anchoring.

    sl2_filter: prefer entries that cover any of these SL2 ids
    axes_filter: prefer entries matching these axis-value pairs

    If filtered set < k, fall back to random sample from full pool.
    Returns empty list if pool doesn't exist.
    """
    raise NotImplementedError


def size(slug: str, root: Path = DEFAULT_SEED_ROOT) -> int:
    """Return current pool size for a slug."""
    raise NotImplementedError


def clear(slug: str, root: Path = DEFAULT_SEED_ROOT) -> None:
    """Wipe the pool for a slug. Destructive."""
    raise NotImplementedError
