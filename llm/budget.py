"""Per-run LLM budget tracking + hard cap.

Wraps any LLMClient so every generate() call:
  1. Looks up the current limit + already-used USD from the bound run
  2. Estimates this call's cost (input/output tokens × per-1M pricing)
  3. Raises BudgetExceededError BEFORE the call if it would push over
  4. After the call, accumulates the actual cost into run.cost_usd_used

Pricing table covers the providers we ship with. Unknown models fall back
to zero (no budget enforcement) — proxy endpoints often hide pricing.

Usage:
    from t2v_promptgen.llm.budget import BudgetedClient, BudgetExceededError

    client = build_client(...)
    bclient = BudgetedClient(client, run=run, hook=update_run_callback)
    bclient.generate(...)   # raises BudgetExceededError if over limit
"""
from __future__ import annotations

import threading
from typing import Any, Callable

from ..core.schema import Run
from .base import LLMClient, LLMResponse


class BudgetExceededError(RuntimeError):
    """Raised before an LLM call when it would exceed run.cost_usd_limit."""
    def __init__(self, used: float, limit: float, estimated: float):
        super().__init__(
            f"budget exceeded: used ${used:.4f} + estimated ${estimated:.4f} "
            f"would exceed limit ${limit:.4f}"
        )
        self.used = used
        self.limit = limit
        self.estimated = estimated


# Per-1M-token prices in USD. Keep conservative — overestimating is safer
# than underestimating for a hard cap. Falls back to 0 for unknown models
# (which disables enforcement, so the cap acts as guidance only).
PRICING_USD_PER_1M = {
    # input, output
    "deepseek-chat":      (0.27, 1.10),
    "deepseek-v4-pro":    (0.55, 2.19),     # estimated; check current rates
    "deepseek-reasoner":  (0.55, 2.19),
    "gpt-4o":             (2.50, 10.00),
    "gpt-4o-mini":        (0.15, 0.60),
    "o1":                 (15.00, 60.00),
    "claude-opus-4-7":    (15.00, 75.00),
    "claude-sonnet-4-5":  (3.00, 15.00),
    "qwen2.5-max":        (1.60, 6.40),
    "qwen-plus":          (0.40, 1.20),
    "moonshot-v1-32k":    (1.65, 1.65),
    "glm-4-plus":         (7.00, 7.00),
}


def estimate_cost(model: str, in_tokens: int, out_tokens: int) -> float:
    """Estimate USD cost of one call. Returns 0 for unknown models.

    Lookup order:
      1. Exact model name match.
      2. `model` is a known model + hyphen-suffix (e.g. "deepseek-chat-0125"
         matches "deepseek-chat"). Longest known key wins to avoid
         "deepseek" accidentally matching "deepseek-reasoner-v2".
    Unknown models / dated variants without an explicit entry return 0
    (no enforcement) — add an explicit entry to PRICING_USD_PER_1M when
    you ship a new provider.
    """
    if not model:
        return 0.0
    pricing = PRICING_USD_PER_1M.get(model)
    if not pricing:
        # Longest-key-first prefix match with explicit hyphen boundary
        for known in sorted(PRICING_USD_PER_1M.keys(), key=len, reverse=True):
            if model.startswith(known + "-"):
                pricing = PRICING_USD_PER_1M[known]
                break
    if not pricing:
        return 0.0
    p_in, p_out = pricing
    return (in_tokens * p_in + out_tokens * p_out) / 1_000_000


def estimate_call_upper_bound(model: str, max_tokens: int,
                               messages: list[dict] | None = None) -> float:
    """Conservative pre-call estimate: assume max input + max output."""
    # Rough input estimate: 1 token ≈ 3 chars for mixed CJK
    in_chars = sum(len(str(m.get("content", ""))) for m in (messages or []))
    in_tokens_est = in_chars // 3
    return estimate_cost(model, in_tokens_est, max_tokens)


class BudgetedClient(LLMClient):
    """Wraps an LLMClient with per-run budget enforcement.

    Args:
      inner: the real LLMClient (e.g. OpenAICompatibleClient)
      run: the Run to charge against (uses cost_usd_used + cost_usd_limit)
      on_charge: optional callback called as on_charge(actual_usd) after
                 each successful call — lets the caller persist run state.

    Thread safety: the read-modify-write of `run.cost_usd_used` is protected
    by a per-run lock keyed by `run.id` (string), so concurrent background
    tasks (judge + rewrite, etc.) on the same run can't lose updates.
    Caller should invoke `release_lock(run_id)` when the run is permanently
    deleted to drop the lock entry — otherwise the dict grows monotonically.
    """

    # Per-run locks keyed by run.id (str). Stable across Run object identity
    # so reloads from SQLite reuse the same lock for the same logical run.
    _LOCKS: dict[str, threading.Lock] = {}
    _LOCKS_GUARD = threading.Lock()

    @classmethod
    def _lock_for(cls, run: Run) -> threading.Lock:
        key = run.id
        lk = cls._LOCKS.get(key)
        if lk is not None:
            return lk
        with cls._LOCKS_GUARD:
            lk = cls._LOCKS.get(key)
            if lk is None:
                lk = threading.Lock()
                cls._LOCKS[key] = lk
        return lk

    @classmethod
    def release_lock(cls, run_id: str) -> None:
        """Drop the per-run lock entry. Call this when a run is deleted so
        the lock dict doesn't grow without bound."""
        with cls._LOCKS_GUARD:
            cls._LOCKS.pop(run_id, None)

    def __init__(self, inner: LLMClient, run: Run,
                 on_charge: Callable[[float], None] | None = None):
        self.inner = inner
        self.run = run
        self.on_charge = on_charge
        # Forward attrs the protocol exposes
        self.name = getattr(inner, "name", "budgeted")
        self.model = getattr(inner, "model", "")

    def generate(self, messages: list[dict], json_schema: dict | None = None,
                 temperature: float = 0.3, max_tokens: int = 4096,
                 system: str | None = None) -> LLMResponse:
        lock = self._lock_for(self.run)
        # Pre-call check (read snapshot under lock so concurrent calls see
        # consistent "used" totals when deciding to block).
        with lock:
            used = self.run.cost_usd_used
            limit = self.run.cost_usd_limit
            full_msgs = ([{"role": "system", "content": system}] if system else []) + list(messages)
            estimated = estimate_call_upper_bound(self.model, max_tokens, full_msgs)
            # Only enforce if model has known pricing (else estimated = 0 and we don't block)
            if limit and limit > 0 and estimated > 0 and (used + estimated) > limit:
                raise BudgetExceededError(used=used, limit=limit, estimated=estimated)

        # The LLM call itself is the slow part — don't hold the lock across it.
        resp = self.inner.generate(messages=messages, json_schema=json_schema,
                                    temperature=temperature, max_tokens=max_tokens,
                                    system=system)

        # Post-call accounting. Prefer our local estimate (consistent with the
        # pricing table used for the cap); if unknown model, fall back to
        # whatever the provider reported (often non-zero for OpenAI/Anthropic).
        local_est = estimate_cost(
            self.model,
            resp.usage.input_tokens,
            resp.usage.output_tokens,
        )
        provider_reported = getattr(resp.usage, "cost_usd", 0) or 0
        actual = local_est if local_est > 0 else float(provider_reported)
        with lock:
            self.run.cost_usd_used = round(self.run.cost_usd_used + actual, 6)
        # Override cost_usd in usage so callers see real number
        if actual > 0:
            resp.usage.cost_usd = actual
        if self.on_charge:
            try:
                self.on_charge(actual)
            except Exception:
                pass
        return resp
