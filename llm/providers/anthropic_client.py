"""Anthropic provider.

A1 fix (2026-06): the previous version of this file raised
`NotImplementedError` from `generate()`. The web routes registered
"anthropic" in their provider dropdowns, so any user picking it silently
fell through to mock data (the broad `except Exception` in
`_try_real_*` paths). That looked like "API doesn't work" while the
actual provider call never happened.

The fix routes "anthropic" through `OpenAICompatibleClient` with a
preset `base_url` (Anthropic publishes an OpenAI-compatible endpoint
at `https://api.anthropic.com/v1`). Same registry, same generate()
signature, no caller changes needed.

If/when we need first-party Anthropic SDK features (tool_use with
forced schema, streaming, beta features), this file can grow a real
implementation. For now the OpenAI-compat path covers every code path
in this repo.
"""
from __future__ import annotations

from ..base import register
from .openai_compat import OpenAICompatibleClient


# Public pricing (USD per million tokens). Used by llm/budget if the
# upstream PRICING_USD_PER_1M table doesn't already know a model.
PRICING = {
    "claude-opus-4-7":   {"input": 15.0, "output": 75.0},
    "claude-opus-4-8":   {"input": 15.0, "output": 75.0},
    "claude-sonnet-4-5": {"input": 3.0,  "output": 15.0},
    "claude-sonnet-4-6": {"input": 3.0,  "output": 15.0},
    "claude-haiku-4-5":  {"input": 0.8,  "output": 4.0},
}


@register("anthropic")
class AnthropicClient(OpenAICompatibleClient):
    """Anthropic via OpenAI-compat endpoint.

    Inherits the full OpenAI-compat generate() implementation, just
    pinned to the Anthropic profile. Callers construct this exactly
    like before — `make_client(provider="anthropic", model="...", api_key=...)`.
    """
    name = "anthropic"

    def __init__(
        self,
        model: str = "claude-opus-4-7",
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 60.0,
        max_retries: int = 3,
    ) -> None:
        super().__init__(
            model=model,
            profile="anthropic",
            base_url=base_url,    # allow override (e.g. for self-hosted proxies)
            api_key=api_key,
            timeout=timeout,
            max_retries=max_retries,
        )
