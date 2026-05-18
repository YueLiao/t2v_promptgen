"""Deterministic heuristic for difficulty scoring.

Score table (v0.6 — added temporal/dynamic factors):

    +1.0  per action_count
    +0.5  per subject_count
    +0.5  per temporal marker found in text (先/然后/接着/突然/逐渐...) capped at +2.0
    +0.5  per motion verb found in text (走/跑/握/转身...) capped at +2.0
    -1.5  if contains "静止/不动/纹丝不动/一动不动" (static signal — should be rare)
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

# Temporal markers — segmenting time within the 5s window (≥2 = clear progression)
TEMPORAL_KW = {"先", "然后", "接着", "随即", "紧接着", "最后", "起初", "开始时",
               "突然", "逐渐", "慢慢", "缓慢", "迅速", "加速", "减速",
               "前 1 秒", "中段", "后半段", "短暂停顿", "停顿后"}

# Motion verbs — concrete visible actions (not "is/has/stand")
MOTION_VERB_KW = {"走", "跑", "跳", "蹲", "起身", "坐下", "转身", "回头",
                  "抓", "握", "松开", "放下", "拿起", "拾起", "推", "拉", "举", "抬",
                  "挥", "摆", "弯", "伸", "张开", "合上", "翻", "旋转",
                  "迈步", "迈出", "踏", "跨", "迎面", "走向", "走来", "走去",
                  "点头", "摇头", "眨眼", "眨", "微笑", "皱眉",
                  "推开", "拉开", "拨", "按", "敲", "踢", "扔", "抛", "接",
                  "倒", "斟", "饮", "啜", "咬", "嚼",
                  "穿过", "走过", "穿越", "经过", "划过", "掠过",
                  "升起", "落下", "倾斜", "倒下", "起立",
                  "环顾", "凝视", "注视", "扫视"}

# Static signals — should generally be absent (videos describe motion, not still frames)
STATIC_KW = {"静止", "不动", "纹丝不动", "一动不动", "保持不变", "静态"}


def count_kw(text: str, kw_set: set[str]) -> int:
    """Count distinct keywords from kw_set that appear in text."""
    return sum(1 for k in kw_set if k in text)


def score(prompt_zh: str, action_count: int, subject_count: int,
          axes_values: dict | None = None,
          sl2_covered: list[str] | None = None) -> float:
    """Compute heuristic difficulty score from prompt content + metadata."""
    s = 0.0
    s += action_count * 1.0
    s += subject_count * 0.5

    # Multi-subject bonus (above the first)
    if subject_count >= 2:
        s += 1.5 * (subject_count - 1)

    # Cross-SL2 coverage bonus: testing 2+ SL2 in one prompt = harder
    if sl2_covered and len(sl2_covered) >= 2:
        s += 1.0 * (len(sl2_covered) - 1)

    # Temporal / dynamic rewards (capped)
    temporal_hits = count_kw(prompt_zh, TEMPORAL_KW)
    motion_hits = count_kw(prompt_zh, MOTION_VERB_KW)
    s += min(temporal_hits, 5) * 0.5
    s += min(motion_hits, 5) * 0.5

    # Bonus for ≥3 temporal markers (real multi-step description)
    if temporal_hits >= 3:
        s += 1.0

    # Static-signal penalty
    if any(k in prompt_zh for k in STATIC_KW):
        s -= 1.5

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
        s += 0.5 * len(axes_values)
    return s


def to_band(s: float) -> str:
    """Map score to difficulty band.

    Raised thresholds (v0.7): more prompts need genuine complexity to reach
    hard.  s<3 = easy (rejected), 3≤s<6 = medium, s≥6 = hard.
    """
    if s < 3:
        return "easy"
    if s < 6:
        return "medium"
    return "hard"
