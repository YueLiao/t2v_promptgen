"""Known T2V specialty capabilities.

This registry is the controlled vocabulary the P0 LLM classifier picks from.
The classifier may also coin a new slug if the user's description doesn't
match anything here — the new slug then gets folded back into the registry
on its first successful run (handled by phases/intake.register_new).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CapabilityEntry:
    slug: str
    display_name_zh: str
    scope: str
    examples: list[str]    # keyword hints (for fallback + LLM context)


KNOWN_CAPABILITIES: list[CapabilityEntry] = [
    CapabilityEntry(
        slug="human_hand",
        display_name_zh="人手生成",
        scope="测视频生成时手部细节:手指数量、关节角度、握持稳定性、与物体接触、皮肤质感等",
        examples=["人手", "hand", "手指", "握", "持物", "指法"],
    ),
    CapabilityEntry(
        slug="human_body",
        display_name_zh="人体动作",
        scope="测人体姿态、运动连贯性、肢体比例、关节自由度",
        examples=["人体", "body", "姿态", "肢体", "运动"],
    ),
    CapabilityEntry(
        slug="camera_motion",
        display_name_zh="镜头运动",
        scope="测推/拉/摇/移/跟随等镜头运动的轨迹和速度准确性",
        examples=["运镜", "camera", "镜头", "推近", "跟随", "摇移"],
    ),
    CapabilityEntry(
        slug="physics",
        display_name_zh="物理仿真",
        scope="测刚体碰撞、流体、布料、形变、重力等物理规律的真实感",
        examples=["物理", "physics", "碰撞", "重力", "流体", "布料"],
    ),
    CapabilityEntry(
        slug="text_rendering",
        display_name_zh="文字渲染",
        scope="测视频中招牌、字幕、标签等可读文字的字符正确性",
        examples=["文字", "text", "招牌", "字幕", "字符"],
    ),
    CapabilityEntry(
        slug="temporal_consistency",
        display_name_zh="时序一致性",
        scope="测 5 秒内多步动作顺序、因果链完整性、连续变化不可逆、遮挡前后一致、速度感、状态记忆",
        examples=["时序", "temporal", "动作顺序", "因果", "不可逆", "同步"],
    ),
    CapabilityEntry(
        slug="aesthetic",
        display_name_zh="美学质量",
        scope="测构图、色彩、光影、整体观感的电影感",
        examples=["美学", "构图", "电影感", "光影", "审美"],
    ),
    CapabilityEntry(
        slug="emotion",
        display_name_zh="情绪表达",
        scope="测人物表情、肢体语言、氛围情绪的传达准确性",
        examples=["情绪", "emotion", "表情", "氛围"],
    ),
    CapabilityEntry(
        slug="animal",
        display_name_zh="动物生成",
        scope="测动物形态结构、行为合理性、毛发、特征",
        examples=["动物", "animal", "毛发", "宠物"],
    ),
    CapabilityEntry(
        slug="fluid_dynamics",
        display_name_zh="流体动力学",
        scope="测水、烟、火、云等流体的动态合理性",
        examples=["流体", "水", "烟", "火", "云", "粒子"],
    ),
]

KNOWN_SLUGS = [c.slug for c in KNOWN_CAPABILITIES]


def get(slug: str) -> CapabilityEntry | None:
    for c in KNOWN_CAPABILITIES:
        if c.slug == slug:
            return c
    return None


def keyword_fallback(description: str) -> str:
    """Naive keyword-match fallback when no LLM is available.

    Checked in registry order — first hit wins. The registry is intentionally
    ordered with more specific capabilities (e.g. temporal_consistency) listed
    before broader ones (physics) so a passing mention doesn't mis-classify.
    """
    # Specific intent markers first
    for c in KNOWN_CAPABILITIES:
        for kw in c.examples:
            if kw.lower() in description.lower() or kw in description:
                return c.slug
    return "custom_capability"
