"""LLM config loader.

Precedence (highest first):
    1. Explicit constructor kwargs (programmatic)
    2. Env vars:
         T2V_LLM_PROVIDER          provider name (e.g. "yibuapi")
         T2V_LLM_MODEL             model id
         T2V_LLM_BASE_URL          base URL (for custom profiles)
         T2V_LLM_API_KEY           default API key
         T2V_LLM_API_KEY_<PROFILE> per-profile override
    3. ~/.t2v_promptgen/config.yaml
    4. Hard-coded defaults (anthropic + claude-opus-4-7)

config.yaml shape:

    default_provider: yibuapi
    default_model: gemini-2.5-pro
    cost_limit_usd_per_run: 5.0
    profiles:
      yibuapi:
        base_url: https://yibuapi.com/v1     # override profile default
        api_key: sk-...                       # plain text or read env via $VAR
      openai:
        api_key: $OPENAI_API_KEY
    pricing_overrides:                        # optional
      yibuapi:
        gemini-2.5-pro:
          input: 1.25
          output: 5.0
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_PATH = Path.home() / ".t2v_promptgen" / "config.yaml"


@dataclass
class LLMConfig:
    provider: str = "anthropic"
    model: str = "claude-opus-4-7"
    base_url: str | None = None
    api_key: str | None = None
    cost_limit_usd_per_run: float = 5.0
    extra: dict[str, Any] = field(default_factory=dict)


def load(path: Path | None = None) -> LLMConfig:
    """Load LLMConfig with precedence above. Path may not exist (returns defaults)."""
    cfg = LLMConfig()
    path = path or DEFAULT_CONFIG_PATH

    # Layer 3: file
    if path.exists():
        try:
            import yaml
        except ImportError:
            raise RuntimeError("Install pyyaml: pip install pyyaml")
        with path.open(encoding="utf-8") as f:
            doc = yaml.safe_load(f) or {}
        cfg.provider = doc.get("default_provider", cfg.provider)
        cfg.model = doc.get("default_model", cfg.model)
        cfg.cost_limit_usd_per_run = float(
            doc.get("cost_limit_usd_per_run", cfg.cost_limit_usd_per_run)
        )
        profiles = doc.get("profiles") or {}
        prof = profiles.get(cfg.provider) or {}
        cfg.base_url = prof.get("base_url", cfg.base_url)
        ak = prof.get("api_key")
        if ak:
            cfg.api_key = _resolve_env_ref(ak)
        cfg.extra = doc

    # Layer 2: env vars (highest priority short of explicit kwargs)
    cfg.provider = os.environ.get("T2V_LLM_PROVIDER", cfg.provider)
    cfg.model = os.environ.get("T2V_LLM_MODEL", cfg.model)
    cfg.base_url = os.environ.get("T2V_LLM_BASE_URL", cfg.base_url)

    env_key_specific = os.environ.get(f"T2V_LLM_API_KEY_{cfg.provider.upper()}")
    env_key_generic = os.environ.get("T2V_LLM_API_KEY")
    cfg.api_key = env_key_specific or env_key_generic or cfg.api_key

    return cfg


def _resolve_env_ref(val: str) -> str:
    """If val starts with $, look up env var; else return as-is."""
    if isinstance(val, str) and val.startswith("$"):
        return os.environ.get(val[1:], "")
    return val


def make_client(cfg: LLMConfig | None = None):
    """Build an LLM client from config (or default loader)."""
    cfg = cfg or load()
    from .base import make_client as factory
    kwargs: dict[str, Any] = {}
    if cfg.base_url:
        kwargs["base_url"] = cfg.base_url
    if cfg.api_key:
        kwargs["api_key"] = cfg.api_key
    # If using a proxy profile, pass profile name through
    if cfg.provider in {"yibuapi", "openai_compat", "deepseek", "qwen",
                        "moonshot", "zhipu", "siliconflow", "openai"}:
        kwargs["profile"] = cfg.provider
        return factory("openai_compat", model=cfg.model, **kwargs)
    return factory(cfg.provider, model=cfg.model, **kwargs)
