"""Deterministic heuristic for difficulty scoring.

Score table (locked v0.5):

    +1.0  per action_count
    +0.5  per subject_count
    +1.5  if mentions fine motor (手指 / 指法 / 关节 / 微表情 ...)
    +2.0  if requires text rendering (specific characters / readable signs)
    +1.5  if requires complex physics (流体 / 碰撞 / 穿模危险 / 弹性)
    +1.0  if requires world knowledge (specific named landmark / culture)
    +1.0  if has occlusion / 遮挡
    +0.5  per number of axes_values that deviate from "default" (= first value)

Bands (B=0:3:2 → only medium/hard):
    s < 2     : easy   (rejected unless test mode)
    2 ≤ s < 5 : medium
    s ≥ 5     : hard

If LLM-declared difficulty disagrees with computed by > 1 band → override
with the heuristic; record discrepancy in QA log.
"""
from __future__ import annotations

# Keyword lexicons (will be tuned over time)
FINE_MOTOR_KW = {"手指", "指法", "关节", "微表情", "睫毛", "眼神", "握持", "捏"}
TEXT_KW = {"招牌", "字幕", "写下", "文字", "中文", "英文", "招贴"}
PHYSICS_KW = {"流体", "碰撞", "穿模", "弹性", "重力", "水花", "火焰", "烟雾"}
WORLD_KW = {"巴黎", "京都", "长城", "金字塔", "纽约", "悉尼", "里约", "威尼斯",
            "天安门", "埃菲尔", "泰姬陵", "大本钟"}
OCCLUSION_KW = {"遮挡", "部分遮挡", "藏在", "躲在"}


def score(prompt_zh: str, action_count: int, subject_count: int,
          axes_values: dict | None = None) -> float:
    """Compute heuristic difficulty score from prompt content + metadata."""
    s = 0.0
    s += action_count * 1.0
    s += subject_count * 0.5
    if any(k in prompt_zh for k in FINE_MOTOR_KW):
        s += 1.5
    if any(k in prompt_zh for k in TEXT_KW):
        s += 2.0
    if any(k in prompt_zh for k in PHYSICS_KW):
        s += 1.5
    if any(k in prompt_zh for k in WORLD_KW):
        s += 1.0
    if any(k in prompt_zh for k in OCCLUSION_KW):
        s += 1.0
    if axes_values:
        # Crude: more axes-values present → harder. Tune later.
        s += 0.5 * len(axes_values)
    return s


def to_band(s: float) -> str:
    """Map score to difficulty band. easy is allowed in output but B=0:3:2
    forbids easy from being targeted in P2."""
    if s < 2:
        return "easy"
    if s < 5:
        return "medium"
    return "hard"
