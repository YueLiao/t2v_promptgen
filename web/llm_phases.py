"""LLM-backed dimension & prompt generation for the web UI.

This is the "real" path. If credentials are missing or any call fails,
the caller in app.py falls back to web.mock_data.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from ..core.schema import Axis, PromptEntry, SL2
from ..llm.base import LLMClient, make_client
from ..llm.providers import openai_compat, anthropic_client  # register


# ---------------------------------------------------------------------------
# Client builder (per-run credentials, not persisted)
# ---------------------------------------------------------------------------

def build_client(provider: str, model: str, api_key: str,
                 base_url: str | None = None) -> LLMClient:
    """Construct an LLM client from per-run credentials."""
    kwargs: dict[str, Any] = {"model": model, "api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url

    if provider in openai_compat.PROFILES or provider == "openai_compat":
        kwargs.setdefault("profile", provider if provider in openai_compat.PROFILES else "yibuapi")
        return make_client("openai_compat", **kwargs)
    return make_client(provider, **kwargs)


# ---------------------------------------------------------------------------
# Phase 0/1 — dimensions
# ---------------------------------------------------------------------------

_DIMENSIONS_SYSTEM = """你是 T2V(文生视频)模型评测专家。用户会描述一个想测试的"专项能力",你需要生成:
1. SL2 列表(专项能力下要测的具体失败模式,每条 6-20 个)
2. axes 列表(测试变量,3-6 个,每个 2-6 个取值,axes 之间正交)

严格规则:
- SL2.id 是 snake_case 英文,name 是中文
- 每个 SL2 必须能被评测员明确判定 Yes/No
- axes 是会显著影响失败概率的控制变量,不是"题材分类"
- 输出必须是合法的 JSON,顶层有 sl2_list 和 axes 两个键
- 不要在 JSON 外包裹任何文本,不要用 markdown 代码块

输出 schema:
{
  "sl2_list": [
    {
      "id": "snake_case_id",
      "name": "中文名",
      "inherits_from": ["指令遵循:相关 L2 名"],
      "description": "一句话描述失败模式",
      "judging_criteria_md": "## 判定为 Yes 的条件\\n- ...\\n\\n## 判定为 No 的情况\\n- ...",
      "stress_keywords": ["关键词1", "关键词2"]
    }
  ],
  "axes": [
    {"name": "中文轴名", "values": ["值1", "值2", "值3"]}
  ]
}"""


def generate_dimensions_real(
    description: str,
    client: LLMClient,
    previous_sl2: list[SL2] | None = None,
    previous_axes: list[Axis] | None = None,
    feedback: str = "",
    round_idx: int = 0,
) -> tuple[list[SL2], list[Axis]]:
    """Generate SL2 + axes via real LLM.

    Falls back to mock on parse failure (caller catches).
    """
    user_msg = f"专项能力描述:\n{description}\n\n"

    if round_idx > 0 and previous_sl2:
        user_msg += "上一轮已生成:\n"
        user_msg += "SL2: " + ", ".join(s.name for s in previous_sl2) + "\n"
        user_msg += "Axes: " + ", ".join(a.name for a in previous_axes or []) + "\n\n"
    if feedback:
        user_msg += f"用户反馈意见:\n{feedback}\n\n"
    user_msg += "请给出当前最佳的 SL2 列表 + axes,严格按 JSON schema 输出。"

    resp = client.generate(
        messages=[{"role": "user", "content": user_msg}],
        system=_DIMENSIONS_SYSTEM,
        json_schema={"required": ["sl2_list", "axes"]},
        temperature=0.3,
        max_tokens=4096,
    )

    data = resp.content if isinstance(resp.content, dict) else json.loads(resp.content)

    sl2_list = []
    for item in data.get("sl2_list", [])[:20]:
        try:
            sl2_list.append(SL2(
                id=item["id"],
                name=item["name"],
                inherits_from=item.get("inherits_from", []),
                description=item.get("description", ""),
                judging_criteria_md=item.get("judging_criteria_md", ""),
                stress_keywords=item.get("stress_keywords", []),
            ))
        except Exception:
            continue

    axes = []
    for ax in data.get("axes", [])[:6]:
        try:
            vals = ax.get("values", [])
            if not (2 <= len(vals) <= 6):
                vals = vals[:6] if len(vals) > 6 else (vals + ["默认"] * 2)[:2]
            axes.append(Axis(name=ax["name"], values=vals))
        except Exception:
            continue

    if not sl2_list or not axes:
        raise ValueError("LLM returned empty sl2_list or axes")
    return sl2_list, axes


# ---------------------------------------------------------------------------
# Phase 2 — prompts (real)
# ---------------------------------------------------------------------------

_PROMPTS_SYSTEM = """你是 T2V 评测 prompt 设计专家。给定一组 SL2(失败模式)、axes(测试变量)和"具体场景候选词表",生成 N 条 prompt。

严格规则:
- 每条 prompt 必须明确测试至少 1 个 SL2 + 设置所有 axes 的具体值
- **每条 prompt 必须从"具体场景候选词表"中挑选 1 个场景**,把场景的 L3 名或 L4 名写进 prompt 文本里
- **严禁使用抽象占位词**:复杂场景 / 动态背景 / 一般场景 / 简单场景 / 普通环境 / 某地 / 某处 / 某物体 等
- 反例:"在动态背景中" / "在复杂场景下" / "背景为动态场景"
- 正例:"在咖啡店木桌前" / "在荷兰风车旁" / "在水族馆玻璃缸前" / "在篮球场罚球线后"
- 中文 30-120 字,英文 15-50 词,描述同一画面
- 中文用"镜头不动/慢慢推近/慢慢拉远/从左到右移动/从右到左移动/向上移动/向下移动/跟随主体/第一视角向前移动"之一
- 英文用对应电影分镜风格:"Static shot." / "Slow push-in." / "Camera tracks the subject." 等
- 禁用术语:三分法 / 写实风格 / 黄金时刻 / 景深 / HDR
- **不要在 prompt 里写判定标准**,如"应保持笔直" / "避免抖动" / "确保无脱锁" — 这些是评测员判定要点,不是画面描述
- 难度分配:medium 60% / hard 40%
- stress case ~30%,标 is_stress=true(stress 是专门为某个 SL2 设计的极端用例)
- 输出严格 JSON,顶层有 prompts 数组

输出 schema:
{
  "prompts": [
    {
      "id": "spec_xxx_001",
      "difficulty": "medium" | "hard",
      "is_stress": true | false,
      "sl2_covered": ["sl2_id_1", "sl2_id_2"],
      "axes_values": {"轴名": "值"},
      "scene_l1": "城市建筑",
      "scene_l2": "公共场所室外",
      "scene_l3": "建筑",
      "scene_l4": "教堂",
      "subject_count": 1,
      "action_count": 2,
      "camera_zh": "镜头慢慢推近",
      "camera_en": "Slow push-in",
      "prompt_zh": "...",
      "prompt_en": "..."
    }
  ]
}"""


def generate_prompts_real(
    capability: str,
    sl2_list: list[SL2],
    axes: list[Axis],
    target_size: int,
    client: LLMClient,
    batch_size: int = 15,
    scenes_per_batch: int = 40,
) -> list[PromptEntry]:
    """Generate target_size prompts via real LLM in batches.

    Each batch is given a fresh sample of concrete L3/L4 scenes from the tag
    library; the LLM must pick one scene per prompt and embed it in the prompt
    text (no abstract placeholders like "复杂场景").
    """
    from ..tags import default_library

    library = default_library()

    all_prompts: list[PromptEntry] = []
    sl2_summary = [{"id": s.id, "name": s.name, "stress_keywords": s.stress_keywords}
                   for s in sl2_list]
    axes_summary = [{"name": a.name, "values": a.values} for a in axes]

    while len(all_prompts) < target_size:
        n = min(batch_size, target_size - len(all_prompts))
        start_idx = len(all_prompts) + 1

        # Sample concrete scenes for this batch
        scenes = library.sample_diverse(scenes_per_batch)
        scenes_md = "\n".join(
            f"- {{l1:{t.l1}, l2:{t.l2}, l3:{t.l3}" +
            (f", l4:{t.l4}" if t.l4 else "") + "}"
            for t in scenes
        )

        user_msg = f"""专项能力: {capability}

SL2 列表(全集,选择要测的):
{json.dumps(sl2_summary, ensure_ascii=False, indent=2)}

Axes 列表(每条 prompt 必须设置所有轴的具体值):
{json.dumps(axes_summary, ensure_ascii=False, indent=2)}

可选具体场景词表(从中挑选 1 个作为画面背景,把 l3/l4 名称写进 prompt):
{scenes_md}

请生成 {n} 条 prompt,id 从 spec_{capability}_{start_idx:03d} 开始递增。
要求:
- 多样化覆盖不同 SL2 × axes 组合,不要重复
- 难度 medium 60% / hard 40%,is_stress 约 30%
- 每条 prompt 必须从场景词表挑选 1 个具体场景并写进文本(优先用 l4 名,无 l4 用 l3)
- 严禁出现"复杂场景/动态背景/简单背景"这类抽象词
- 不要写判定标准(应该 / 避免 / 确保...),只写画面内容
"""
        try:
            resp = client.generate(
                messages=[{"role": "user", "content": user_msg}],
                system=_PROMPTS_SYSTEM,
                json_schema={"required": ["prompts"]},
                temperature=0.5,
                max_tokens=8000,
            )
            data = resp.content if isinstance(resp.content, dict) else json.loads(resp.content)

            from ..qa.difficulty import score as diff_score
            for item in data.get("prompts", []):
                try:
                    sc = diff_score(
                        item["prompt_zh"],
                        action_count=item.get("action_count", 1),
                        subject_count=item.get("subject_count", 1),
                        axes_values=item.get("axes_values", {}),
                    )
                    difficulty = item.get("difficulty", "medium")
                    if difficulty == "easy":
                        difficulty = "medium"
                    all_prompts.append(PromptEntry(
                        id=item.get("id", f"spec_{capability}_{len(all_prompts)+1:03d}"),
                        capability=capability,
                        capability_version=1,
                        difficulty=difficulty,
                        difficulty_score=sc,
                        is_stress=item.get("is_stress", False),
                        sl2_covered=item.get("sl2_covered", []),
                        axes_values=item.get("axes_values", {}),
                        subject_count=item.get("subject_count", 1),
                        action_count=item.get("action_count", 1),
                        camera_zh=item.get("camera_zh"),
                        camera_en=item.get("camera_en"),
                        prompt_zh=item["prompt_zh"],
                        prompt_en=item["prompt_en"],
                        scene_l1=item.get("scene_l1"),
                        scene_l2=item.get("scene_l2"),
                        scene_l3=item.get("scene_l3"),
                        scene_l4=item.get("scene_l4"),
                        generated_at=datetime.now(),
                    ))
                except Exception:
                    continue
        except Exception:
            # Batch failed; bail with whatever we have
            break
    return all_prompts
