"""LLM provider abstraction.

All providers implement the same generate() signature with strict JSON
schema support (tool-use / function-calling style). One run = one provider
(decision G).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class Usage:
    """Token usage + cost tracking returned by every generate() call.

    Defaults to zero so accounting code can always read these attrs without
    AttributeError when a provider forgets to set one.
    """
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


@dataclass
class LLMResponse:
    """Container for one generate() call.

    If `json_schema` was supplied to generate(), `content` is a parsed dict
    matching the schema. Otherwise `content` is raw text.

    All fields have safe defaults so callers can construct a partial response
    in tests / error paths without juggling required-init args.
    """
    content: dict | str = ""
    usage: Usage = field(default_factory=Usage)
    finish_reason: str = "stop"      # "stop" | "length" | "content_filter" | ...
    raw: Any = None                  # provider-native response object, for debugging


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
