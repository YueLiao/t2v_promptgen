"""Pre-assign multi_enum card params per source_id, before the LLM call.

The user can pick multiple target values on a card (e.g. scene_shift with
target_scene = [E1, E5, E8] or the "🎲 随机全部" sentinel `["__all__"]`).
Without this module the rewrite system prompt would ask the LLM to "in the
list [E1/E5/E8] pick one uniformly per prompt" — which LLMs do badly
(bias to first option, repetition under high temp).

Instead we do the spread server-side, seeded for reproducibility:
  - For each multi_enum param on a selected card, build a round-robin
    cycle of length N (= number of source prompts), then deterministic
    shuffle with `random.Random(seed XOR hash(card.id, param.key))`.
  - Each prompt's assignment is recorded as
        assignments[source_id][card_id][param_key] = "E5 室内现代"
  - The LLM payload for that prompt includes `assigned`, and the system
    prompt instructs strict application — no LLM decision.

Single-target cards (the user pinned one option) and single-value enum
params (e.g. preserve_action=是/否) are passed through unchanged.

For preview: `summarize_assignments` returns
   {card_id: {param_key: {target: count, ...}}}
which the R2 page renders as "E1 × 3 · E5 × 4 · E8 × 3".
"""
from __future__ import annotations

import hashlib
import random
from typing import Any, Iterable

from .rewrite_cards import card_for


# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------

def _resolve_targets(value: Any, options: list[str]) -> list[str]:
    """Normalize a stored param value into the concrete target list to spread
    across. Mirrors `_render_param_value` semantics:
      - "__all__" sentinel or [] → all options
      - list[str] → filtered to known options
      - bare str  → [str]
    Returns [] for invalid configurations (caller treats as no assignment).
    """
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        value = list(options)
    if not value or "__all__" in value:
        return list(options)
    filtered = [v for v in value if v in options]
    return filtered or list(options)


def _seed_for(base_seed: int, card_id: str, param_key: str) -> int:
    """Stable per-(card, param) RNG seed derived from the run-level seed."""
    digest = hashlib.sha256(
        f"{base_seed}|{card_id}|{param_key}".encode("utf-8")
    ).digest()
    return int.from_bytes(digest[:8], "big", signed=False)


def _round_robin_spread(targets: list[str], n: int, rng: random.Random) -> list[str]:
    """Generate exactly N picks where every target appears floor(N/m) or
    ceil(N/m) times. The order is shuffled.

    >>> _round_robin_spread(["A","B","C"], 7, random.Random(0))
    # Each of A/B/C appears 2 or 3 times, shuffled.
    """
    if not targets or n <= 0:
        return []
    m = len(targets)
    base, extra = divmod(n, m)
    # Each target gets `base` copies; first `extra` targets get one more.
    # Pick which targets get the bonus copy randomly so the bias is fair.
    bonus_indices = list(range(m))
    rng.shuffle(bonus_indices)
    bonus_set = set(bonus_indices[:extra])
    picks: list[str] = []
    for i, t in enumerate(targets):
        picks.extend([t] * (base + (1 if i in bonus_set else 0)))
    rng.shuffle(picks)
    return picks


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def pre_assign(
    source_ids: list[str],
    directive,                          # core.rewrite_schema.RewriteDirective
    seed: int,
) -> dict[str, dict[str, dict[str, str]]]:
    """Returns assignments[source_id][card_id][param_key] = chosen_value.

    Only multi_enum params with 2+ effective targets are assigned (single-
    target and single-enum params are passed through unchanged in the LLM
    payload, so no entry is created here). Source ids preserve input order.
    """
    out: dict[str, dict[str, dict[str, str]]] = {sid: {} for sid in source_ids}
    if not source_ids or not directive or not directive.transforms:
        return out

    n = len(source_ids)
    for t in sorted(directive.transforms, key=lambda x: x.order):
        card = card_for(t.id)
        if card is None:
            continue
        for spec in card.params:
            if spec.type != "multi_enum":
                continue
            raw = (t.params or {}).get(spec.key, spec.default)
            targets = _resolve_targets(raw, spec.options or [])
            if len(targets) < 2:
                # Single target — nothing to spread. The render_card text
                # already pins it; no per-prompt assignment needed.
                continue
            rng = random.Random(_seed_for(seed, card.id, spec.key))
            picks = _round_robin_spread(targets, n, rng)
            for sid, pick in zip(source_ids, picks):
                out[sid].setdefault(card.id, {})[spec.key] = pick
    return out


def summarize_assignments(
    assignments: dict[str, dict[str, dict[str, str]]],
) -> dict[str, dict[str, dict[str, int]]]:
    """Aggregate counts per (card_id, param_key, target) for the preview UI.

    Returns {card_id: {param_key: {target: count}}} sorted by target name.
    """
    counts: dict[str, dict[str, dict[str, int]]] = {}
    for per_prompt in assignments.values():
        for card_id, params in per_prompt.items():
            for key, target in params.items():
                d = counts.setdefault(card_id, {}).setdefault(key, {})
                d[target] = d.get(target, 0) + 1
    # Sort dict keys deterministically by target name for stable display
    return {
        cid: {k: dict(sorted(v.items())) for k, v in p.items()}
        for cid, p in counts.items()
    }


def pre_assigned_keys_by_card(
    directive,
) -> dict[str, set[str]]:
    """Return {card_id: {param_key, ...}} of params that WILL be per-prompt
    assigned (multi_enum with 2+ effective targets). Lets the renderer skip
    repeating the candidate list for those keys.

    Mirrors the eligibility logic in `pre_assign` but without source_ids /
    seed — pure shape-only.
    """
    out: dict[str, set[str]] = {}
    if not directive or not directive.transforms:
        return out
    for t in directive.transforms:
        card = card_for(t.id)
        if card is None:
            continue
        for spec in card.params:
            if spec.type != "multi_enum":
                continue
            raw = (t.params or {}).get(spec.key, spec.default)
            targets = _resolve_targets(raw, spec.options or [])
            if len(targets) >= 2:
                out.setdefault(card.id, set()).add(spec.key)
    return out


def derive_seed(run_id: str, salt: int = 0) -> int:
    """Stable default seed for a run when the user hasn't picked one yet.
    `salt` lets the UI's "🎲 reroll" bump it without dragging in a saved
    field; pass the current rewrite_seed + 1 / Date.now() / anything stable.
    """
    digest = hashlib.sha256(f"{run_id}|{salt}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big", signed=False)
