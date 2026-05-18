"""LLM provider abstraction.

All providers implement the same generate() signature with strict JSON
schema support (tool-use / function-calling style). One run = one provider
(decision G).
"""
from __future__ import annotations

from typing import Any, Protocol


class Usage:
    """Token usage + cost tracking returned by every generate() call."""
    input_tokens: int
    output_tokens: int
    cost_usd: float


class LLMResponse:
    """Container for one generate() call.

    If `json_schema` was supplied to generate(), `content` is a parsed dict
    matching the schema. Otherwise `content` is raw text.
    """
    content: dict | str
    usage: Usage
    finish_reason: str        # "stop" | "length" | "content_filter" | ...
    raw: Any                  # provider-native response object, for debugging


class LLMClient(Protocol):
    """Provider-agnostic LLM client.

    Implementations live under `t2v_promptgen.llm.providers.*`.
    """

    name: str                  # "anthropic" | "openai" | "google" | ...
    model: str                 # provider-specific model id

    def generate(
        self,
        messages: list[dict],
        json_schema: dict | None = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        system: str | None = None,
    ) -> LLMResponse:
        """Single completion. JSON-schema-constrained when json_schema given.

        Provider must:
            - Retry transient errors up to 3 times (provider-side, not orchestrator)
            - Return cost_usd in Usage (compute from public pricing table)
            - Raise on schema validation failure (no silent string fallback)
        """
        ...


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_PROVIDERS: dict[str, type[LLMClient]] = {}


def register(name: str):
    """Decorator: register a provider class under a name for the factory."""
    def deco(cls: type[LLMClient]) -> type[LLMClient]:
        _PROVIDERS[name] = cls
        return cls
    return deco


def make_client(provider: str, model: str, **kw) -> LLMClient:
    """Factory. Provider name must be registered."""
    if provider not in _PROVIDERS:
        raise ValueError(
            f"Unknown provider {provider!r}. Available: {list(_PROVIDERS)}"
        )
    return _PROVIDERS[provider](model=model, **kw)
