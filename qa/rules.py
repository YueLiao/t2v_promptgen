"""Deterministic rule checks for one PromptEntry.

These run before any LLM judge call — fast and stable.
"""
from __future__ import annotations

ZH_LEN_MIN, ZH_LEN_MAX = 30, 120
EN_WORDS_MIN, EN_WORDS_MAX = 15, 60

BANNED_TERMS_ZH = {"三分法", "写实风格", "黄金时刻", "边缘光", "景深", "构图美学"}
BANNED_TERMS_EN = {"rule of thirds", "photorealistic style", "golden hour",
                   "rim light", "depth of field"}


def check_one(prompt) -> list[str]:
    """Return list of error strings (empty = passes all rules)."""
    errors: list[str] = []

    # Length
    if not (ZH_LEN_MIN <= len(prompt.prompt_zh) <= ZH_LEN_MAX):
        errors.append(f"prompt_zh length {len(prompt.prompt_zh)} out of [{ZH_LEN_MIN},{ZH_LEN_MAX}]")

    en_words = len(prompt.prompt_en.split())
    if not (EN_WORDS_MIN <= en_words <= EN_WORDS_MAX):
        errors.append(f"prompt_en words {en_words} out of [{EN_WORDS_MIN},{EN_WORDS_MAX}]")

    # Banned terms
    for term in BANNED_TERMS_ZH:
        if term in prompt.prompt_zh:
            errors.append(f"prompt_zh contains banned term: {term}")
    for term in BANNED_TERMS_EN:
        if term.lower() in prompt.prompt_en.lower():
            errors.append(f"prompt_en contains banned term: {term}")

    # Required fields
    if not prompt.sl2_covered:
        errors.append("sl2_covered is empty")
    if not prompt.axes_values:
        errors.append("axes_values is empty")

    return errors
