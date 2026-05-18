"""Anthropic provider (default).

Default model: claude-opus-4-7 (decision R). Uses the tool_use mechanism
for json_schema-constrained generation.
"""
from __future__ import annotations

from ..base import LLMClient, LLMResponse, register

# Public pricing (USD per million tokens) — update when model id finalized
PRICING = {
    "claude-opus-4-7": {"input": 15.0, "output": 75.0},
    # add more as needed
}


@register("anthropic")
class AnthropicClient(LLMClient):
    name = "anthropic"

    def __init__(self, model: str = "claude-opus-4-7",
                 api_key: str | None = None) -> None:
        self.model = model
        self.api_key = api_key
        # lazy import anthropic SDK in actual impl
        # self._sdk = anthropic.Anthropic(api_key=api_key)

    def generate(
        self,
        messages: list[dict],
        json_schema: dict | None = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        system: str | None = None,
    ) -> LLMResponse:
        """Call Anthropic Messages API.

        If json_schema given: pass as a single forced tool with
        tool_choice={"type": "tool", "name": "output"}. Result.tool_use.input
        becomes content (dict).

        Retries: 3x on 5xx/timeout/overloaded with exponential backoff.
        Cost: compute from PRICING[model] × usage.
        """
        raise NotImplementedError


# Stubs for other providers (each file analogous)
# - openai_client.py     (gpt-4o, o1, json_schema response format)
# - google_client.py     (gemini-2.5-pro, response_schema)
# - qwen_client.py       (qwen-max, json output)
# - deepseek_client.py
# - zhipu_client.py
