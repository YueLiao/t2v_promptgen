"""OpenAI-compatible provider — works with any endpoint that speaks the
OpenAI Chat Completions API (yibuapi proxy, ohmygpt, DeepSeek, Qwen DashScope
OpenAI mode, Together, Groq, local llama.cpp / vLLM ...).

Use one of these named profiles or define your own in config.yaml:

    yibuapi      → https://yibuapi.com/v1                (中转: Claude/Gemini/GPT)
    openai       → https://api.openai.com/v1
    deepseek     → https://api.deepseek.com/v1
    qwen         → https://dashscope.aliyuncs.com/compatible-mode/v1
    moonshot     → https://api.moonshot.cn/v1
    custom       → user-defined base_url

Config precedence:
    1. Explicit kwarg to constructor
    2. Profile defaults
    3. Env vars (T2V_LLM_API_KEY, T2V_LLM_BASE_URL)
    4. ~/.t2v_promptgen/config.yaml
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

from ..base import LLMClient, LLMResponse, Usage, register


# ---------------------------------------------------------------------------
# Profiles
# ---------------------------------------------------------------------------

PROFILES = {
    "yibuapi":    "https://yibuapi.com/v1",
    "openai":     "https://api.openai.com/v1",
    "deepseek":   "https://api.deepseek.com/v1",
    "qwen":       "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "moonshot":   "https://api.moonshot.cn/v1",
    "zhipu":      "https://open.bigmodel.cn/api/paas/v4",
    "siliconflow": "https://api.siliconflow.cn/v1",
}

# Pricing fallback (USD per 1M tokens) — proxy endpoints often don't expose pricing,
# user can override via config.yaml. Defaults are zeroed (proxy bills separately).
DEFAULT_PRICING = {
    "input": 0.0,
    "output": 0.0,
}


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

@register("openai_compat")
@register("yibuapi")            # alias for convenience
class OpenAICompatibleClient(LLMClient):
    """Single class for all OpenAI-compatible endpoints.

    Args:
        model: model id passed through to the endpoint (e.g. "gemini-2.5-pro",
               "claude-opus-4-7", "gpt-4o", "deepseek-chat", "qwen2.5-max")
        profile: named profile from PROFILES, or "custom"
        base_url: explicit base URL (overrides profile)
        api_key: explicit API key (else taken from env T2V_LLM_API_KEY)
        timeout: per-request timeout in seconds (default 60)
        max_retries: provider-side retry count for transient errors (default 3)
    """

    name = "openai_compat"

    def __init__(
        self,
        model: str,
        profile: str = "yibuapi",
        base_url: str | None = None,
        api_key: str | None = None,
        timeout: float = 60.0,
        max_retries: int = 3,
    ) -> None:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError(
                "Install `openai` first: pip install openai"
            ) from exc

        self.model = model
        self.profile = profile

        # Resolve base_url
        if base_url is None:
            if profile not in PROFILES:
                raise ValueError(
                    f"Unknown profile {profile!r}. "
                    f"Known: {list(PROFILES)}. Or pass base_url explicitly."
                )
            base_url = PROFILES[profile]
        self.base_url = base_url

        # Resolve API key
        if api_key is None:
            api_key = (
                os.environ.get(f"T2V_LLM_API_KEY_{profile.upper()}")
                or os.environ.get("T2V_LLM_API_KEY")
                or os.environ.get(f"{profile.upper()}_API_KEY")
            )
        if not api_key:
            raise RuntimeError(
                f"No API key found for profile {profile!r}. "
                f"Set T2V_LLM_API_KEY or pass api_key= ."
            )

        self._client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=max_retries,
        )

    # ------------------------------------------------------------------
    # Main entry
    # ------------------------------------------------------------------

    def generate(
        self,
        messages: list[dict],
        json_schema: dict | None = None,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        system: str | None = None,
    ) -> LLMResponse:
        """Single chat completion. Returns LLMResponse with parsed content.

        If `json_schema` given:
            Forces JSON output via response_format=json_object and validates
            the parsed dict matches schema keys. (Strict JSON-schema mode
            varies by backend — for proxies we use the lenient json_object
            mode and validate ourselves.)
        """
        # Inject system message if provided
        msgs = list(messages)
        if system:
            msgs = [{"role": "system", "content": system}] + msgs

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": msgs,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_schema is not None:
            kwargs["response_format"] = {"type": "json_object"}

        # SDK already retries via max_retries; add light manual retry only
        # for our own JSON parse failures (those wrap the body, not transport).
        # Don't double-retry transport errors — the SDK already does that.
        try:
            resp = self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            # Surface transport / API errors as-is with provider context
            raise RuntimeError(
                f"LLM call failed (model={self.model}, profile={self.profile}): "
                f"{type(exc).__name__}: {exc}"
            ) from exc

        # Defensive: empty choices is a real provider weirdness
        if not getattr(resp, "choices", None):
            raise RuntimeError(
                f"LLM returned no choices (model={self.model}). "
                f"Full response: {resp!r}"[:500]
            )

        # Parse
        choice = resp.choices[0]
        text = (choice.message.content or "") if hasattr(choice, "message") else ""
        usage_obj = getattr(resp, "usage", None)

        content: dict | str
        if json_schema is not None:
            content = _parse_json_lenient(text)
            _validate_schema_keys(content, json_schema)
        else:
            content = text

        out = LLMResponse()
        out.content = content
        out.usage = Usage()
        out.usage.input_tokens = getattr(usage_obj, "prompt_tokens", 0) or 0
        out.usage.output_tokens = getattr(usage_obj, "completion_tokens", 0) or 0
        out.usage.cost_usd = self._estimate_cost(out.usage.input_tokens,
                                                  out.usage.output_tokens)
        out.finish_reason = getattr(choice, "finish_reason", "stop") or "stop"
        out.raw = resp
        return out

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _estimate_cost(self, in_tok: int, out_tok: int) -> float:
        """Best-effort cost estimate. Proxy endpoints often bill differently;
        override DEFAULT_PRICING or via config to get accurate numbers."""
        p = DEFAULT_PRICING
        return (in_tok * p["input"] + out_tok * p["output"]) / 1_000_000


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _parse_json_lenient(text: str) -> dict:
    """Parse JSON from LLM output, tolerant of ```json fences and prose.

    String-aware brace counter (handles `{"x": "}"}` correctly).
    Empty input raises ValueError early.
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("Empty LLM response — cannot parse JSON")

    # Strip ```json / ``` fences
    if text.startswith("```"):
        lines = text.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Locate first `{` and balanced `}`, ignoring braces inside strings
    start = text.find("{")
    if start < 0:
        raise ValueError(f"No JSON object found in response: {text[:200]}")

    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        c = text[i]
        if in_string:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                in_string = False
            continue
        if c == '"':
            in_string = True
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError as exc:
                    raise ValueError(f"Malformed JSON in response: {exc}") from exc
    raise ValueError("Unbalanced braces in JSON response")


def _validate_schema_keys(obj: Any, schema: dict) -> None:
    """Lightweight check: ensure required top-level keys exist."""
    if not isinstance(obj, dict):
        raise ValueError(f"Expected dict, got {type(obj).__name__}")
    required = schema.get("required", [])
    missing = [k for k in required if k not in obj]
    if missing:
        raise ValueError(f"Response missing required keys: {missing}")
