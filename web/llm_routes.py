"""Web routes for LLM provider config / connection test."""
from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

from ..llm.providers import openai_compat  # noqa: F401 - register
from ..llm.providers import anthropic_client  # noqa: F401 - register
from ..llm.config import LLMConfig, load as load_config
from ..llm.base import make_client

router = APIRouter(prefix="/api/llm", tags=["llm"])
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


@router.get("/profiles")
async def list_profiles():
    """Return list of supported provider profiles."""
    return JSONResponse({
        "profiles": list(openai_compat.PROFILES.keys()) + ["anthropic"],
        "current": load_config().__dict__,
    })


@router.post("/test")
async def test_connection(
    provider: str = Form(...),
    model: str = Form(...),
    api_key: str = Form(""),
    base_url: str = Form(""),
):
    """Smoke-test a provider config with a tiny call.

    Cheap call: ask the model to reply with literal "OK".
    Returns success + token usage + latency.
    """
    import time

    # Build a one-shot client (don't persist)
    kwargs: dict[str, Any] = {"model": model}
    if api_key:
        kwargs["api_key"] = api_key
    if base_url:
        kwargs["base_url"] = base_url

    if provider in openai_compat.PROFILES:
        kwargs["profile"] = provider
        provider_key = "openai_compat"
    else:
        provider_key = provider

    try:
        client = make_client(provider_key, **kwargs)
    except Exception as exc:
        return JSONResponse(
            {"ok": False, "stage": "init", "error": str(exc)},
            status_code=400,
        )

    t0 = time.time()
    try:
        resp = client.generate(
            messages=[{"role": "user", "content": "Reply with the two letters: OK"}],
            temperature=0.0,
            max_tokens=20,
        )
    except Exception as exc:
        return JSONResponse(
            {"ok": False, "stage": "call", "error": str(exc),
             "elapsed_ms": int((time.time() - t0) * 1000)},
            status_code=502,
        )

    elapsed = int((time.time() - t0) * 1000)
    content = resp.content if isinstance(resp.content, str) else str(resp.content)
    return JSONResponse({
        "ok": True,
        "provider": provider,
        "model": model,
        "base_url": getattr(client, "base_url", None),
        "elapsed_ms": elapsed,
        "response_preview": content[:120],
        "usage": {
            "input_tokens": resp.usage.input_tokens,
            "output_tokens": resp.usage.output_tokens,
            "cost_usd": resp.usage.cost_usd,
        },
        "finish_reason": resp.finish_reason,
    })


@router.get("/config")
async def get_config():
    """Return current LLM config (with API key masked)."""
    cfg = load_config()
    out = cfg.__dict__.copy()
    if out.get("api_key"):
        ak = out["api_key"]
        out["api_key"] = ak[:6] + "..." + ak[-4:] if len(ak) > 12 else "***"
    return JSONResponse(out)
