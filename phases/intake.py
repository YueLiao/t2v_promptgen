"""Phase 0 — Intake.

The main job is to turn the user's free-form description into a canonical
capability slug. Two implementations:

  - classify_capability_llm(description, client): LLM picks from a known list
    or coins a new snake_case slug if nothing fits. Returns metadata + confidence.

  - keyword_fallback(description): used when no LLM client is available.
"""
from __future__ import annotations

import json
import re

from ..core.capability_registry import (
    KNOWN_CAPABILITIES,
    KNOWN_SLUGS,
    keyword_fallback,
)
from ..llm.base import LLMClient


_INTAKE_SYSTEM = """你是 T2V 评测系统的意图分类器。给定用户对"想测什么能力"的自由描述,你需要:

1. 从【已知专项能力】里挑一个最匹配的(看 scope 而不是仅看关键词)
2. 如果都明显不匹配,新造一个 snake_case 英文 slug(2-3 个单词,下划线连接)

判断要点:
- 看用户描述的【核心意图】是什么,而不是描述里出现了什么名词。比如描述里提到"物理碰撞 / 物体形变"作为测试条件,但核心意图是"测时序",应判 temporal_consistency 而非 physics。
- confidence:high(明确匹配)、medium(基本匹配但有跨界)、low(模棱两可)
- is_new=true 时必须新造 slug,且 slug 必须是 snake_case ASCII

只返回 JSON,不要 markdown:
{
  "slug": "temporal_consistency",
  "display_name_zh": "视频时序一致性",
  "scope": "本次任务的一句话定位(不照抄,要总结)",
  "confidence": "high",
  "is_new": false,
  "related_known": ["physics", "human_body"],
  "reasoning": "(一句话:为什么选这个 slug)"
}"""


def classify_capability_llm(
    description: str,
    client: LLMClient,
) -> dict:
    """LLM-driven capability classification.

    Returns dict with keys: slug, display_name_zh, scope, confidence, is_new,
    related_known, reasoning. On any LLM failure, raises — caller falls back.
    """
    known_block = "\n".join(
        f"- {c.slug}: {c.display_name_zh} — {c.scope}"
        for c in KNOWN_CAPABILITIES
    )
    user_msg = (
        f"【已知专项能力】\n{known_block}\n\n"
        f"【用户描述】\n{description}\n\n"
        f"分类并返回 JSON。"
    )

    resp = client.generate(
        messages=[{"role": "user", "content": user_msg}],
        system=_INTAKE_SYSTEM,
        json_schema={"required": ["slug", "confidence", "is_new"]},
        temperature=0.0,
        max_tokens=500,
    )
    data = resp.content if isinstance(resp.content, dict) else json.loads(resp.content)

    # Defensive normalization
    slug = (data.get("slug") or "").strip()
    if not re.fullmatch(r"[a-z][a-z0-9_]*", slug):
        # Coerce / fallback if the LLM returned something invalid
        slug = keyword_fallback(description)
        data["slug"] = slug
        data["confidence"] = "low"

    # If LLM said is_new=true but the slug is actually in our registry, fix it
    if slug in KNOWN_SLUGS:
        data["is_new"] = False
    else:
        data["is_new"] = True

    data.setdefault("display_name_zh", slug.replace("_", " "))
    data.setdefault("scope", "")
    data.setdefault("confidence", "medium")
    data.setdefault("related_known", [])
    data.setdefault("reasoning", "")
    return data


def classify_with_fallback(
    description: str,
    client: LLMClient | None = None,
) -> dict:
    """Try LLM first, fall back to keyword match on any error.

    Always returns a dict with the same shape as classify_capability_llm.
    The fallback case is marked with confidence='low' and source='keyword'.
    """
    if client is not None:
        try:
            result = classify_capability_llm(description, client)
            result["source"] = "llm"
            return result
        except Exception as exc:
            # Fall through to keyword
            pass

    slug = keyword_fallback(description)
    from ..core.capability_registry import get
    entry = get(slug)
    return {
        "slug": slug,
        "display_name_zh": entry.display_name_zh if entry else slug.replace("_", " "),
        "scope": entry.scope if entry else "",
        "confidence": "low",
        "is_new": slug == "custom_capability",
        "related_known": [],
        "reasoning": "无 LLM 客户端或调用失败,按关键词兜底",
        "source": "keyword",
    }
