"""Render the bilingual evaluator handbook as Markdown.

One section per SL2. Each section has identical structure to allow
training consistency. Decision J: this is the "维度说明书" given to GSB
AB-test evaluators — they tick Yes/No per SL2 per video.

Template per SL2:

    ## SL2-{idx} ｜ {chinese name}（{slug}）

    ### Description / 描述
    {description}

    ### Yes（出现失败）判定标准 / Yes Criteria
    {判定 markdown body}

    ### No（未触发失败）的情况 / No Cases
    - 主体未出现在画面中,无法判定
    - 角度极端看不清,无法判定
    - {其他来自 judging_criteria_md 的 No 段落}

    ### 评测员注意事项 / Evaluator Notes
    {notes}

    ### 示例帧 / Example Frames
    [Pass 示例] | [Fail 示例]  ← v1 placeholder, v2 will inject real frames
"""
from __future__ import annotations

from pathlib import Path

from ..core.schema import CapabilityVersion


def render(cap: CapabilityVersion, lang: str = "zh-en") -> str:
    """Render handbook to a Markdown string.

    lang: 'zh' | 'en' | 'zh-en' (bilingual side-by-side, default)
    """
    raise NotImplementedError


def write(cap: CapabilityVersion, path: Path, lang: str = "zh-en") -> None:
    """Write handbook to disk."""
    raise NotImplementedError
