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

# 视频特性铁律(违反就废)

T2V 生成的是 **5 秒视频**,不是静态图。每条 prompt 都必须描述**有动作、有时序**的画面。

## A. 主体动作(每条必需)
- 每条至少含 **2 个动作动词**,清晰描述主体在做什么
- 动词要可视化:走、跑、抓起、放下、转身、抬手、弯腰、握紧、松开、推、拉、旋转、翻转、张开、合上、挥、举、点头、眨眼、迈步、蹲下、起立……
- 仅描述位置 / 外观 / 静态状态 = 废稿
- ❌ 反例:"一只五指分明的手在握住胡萝卜,纹理清晰"(单时刻+外观)
- ❌ 反例:"他站在咖啡店前"(只有位置)
- ❌ 反例:"剑身静止,手指包裹剑柄"(明写"静止"是大忌)
- ✅ 正例:"她左手抓起胡萝卜,右手用刀切下三段,随即将刀放回案板"
- ✅ 正例:"他推开玻璃门,环顾四周,快步走向吧台,在凳子上坐下"

## B. 时序结构(至少 40% 用例必需 — hard 难度强烈建议)
- 用时序词把动作切成两段以上:
  - 起初…接着…最后… / 先…然后… / 开始时…到结尾… / 随即 / 紧接着
  - 突然 / 逐渐 / 慢慢 / 加速 / 减速 / 短暂停顿后…
  - 在前 1 秒 / 中段 / 最后 1 秒 / 视频后半段
- ❌ 反例:"他握住门把手"(单一时刻,看不出 5 秒在发生什么)
- ✅ 正例:"他先轻拨门把手转动一圈,接着用力下压,最后整扇门向内推开"
- ✅ 正例:"水滴从指尖滑落,在桌面溅开一圈涟漪,随即扩散消失"

## C. 速度 / 节奏(加分项)
- 暗示节奏:缓慢地、迅速地、节奏均匀、忽快忽慢、停顿后突然加速
- 这能让评测员清楚:模型有没有把握时间感

## D. 严禁出现的静态信号
- "保持不变 / 纹丝不动 / 始终静止 / 一动不动" — 全部废稿
- 例外:某 SL2 明确测"该静止的物体有没有乱动"(如背景稳定性),这时静态描述合法,但仍需主体有动作

# 其他规则
- 每条 prompt 明确测至少 1 个 SL2 + 设置所有 axes 的具体值
- 每条 prompt 必须从"具体场景候选词表"挑 1 个,把 L4(优先)或 L3 名写进文本
- 严禁抽象占位词:复杂场景 / 动态背景 / 一般场景 / 简单场景 / 普通环境 / 某地 / 某处 / 某物体
- 中文 30-120 字,英文 15-50 词,同一画面
- 镜头(必填一项):中文用"镜头不动 / 慢慢推近 / 慢慢拉远 / 从左到右移动 / 从右到左移动 / 向上移动 / 向下移动 / 跟随主体 / 第一视角向前移动"之一,英文对应电影术语
- 禁用摄影圈术语:三分法 / 写实风格 / 黄金时刻 / 景深 / HDR
- 不要写判定标准:不要"应保持笔直 / 避免抖动 / 确保无脱锁" — 那是评测员看的,不是画面描述
- 难度 medium 60% / hard 40%;stress case ~30% 标 is_stress=true
- 输出严格 JSON,顶层 prompts 数组,不要 markdown 代码块

# 输出 schema
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
      "motion_verbs": ["推开", "环顾", "走向"],
      "temporal_markers": ["先", "然后", "最后"],
      "camera_zh": "镜头慢慢推近",
      "camera_en": "Slow push-in",
      "prompt_zh": "...",
      "prompt_en": "..."
    }
  ]
}

motion_verbs 至少 2 个,temporal_markers ≥40% 的 prompt 必须非空。"""


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

        # Sample concrete scenes for this batch — adapted to capability if known
        scenes = library.sample_for_capability(capability, scenes_per_batch)
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

            from ..qa.difficulty import (
                score as diff_score,
                count_kw,
                MOTION_VERB_KW,
                STATIC_KW,
            )
            for item in data.get("prompts", []):
                try:
                    prompt_zh = item["prompt_zh"]

                    # Dynamic-quality gate: reject prompts that describe a still image
                    # (zero motion verbs) or explicitly say "静止/不动" without an SL2
                    # justifying it. Caller will re-loop to make up the count.
                    motion_hits = count_kw(prompt_zh, MOTION_VERB_KW)
                    has_static = any(k in prompt_zh for k in STATIC_KW)
                    declared_verbs = item.get("motion_verbs") or []
                    if motion_hits < 2 and len(declared_verbs) < 2:
                        # Looks static — skip
                        continue
                    if has_static:
                        # Explicit "静止/不动" — almost always wrong for T2V; skip
                        continue

                    sc = diff_score(
                        prompt_zh,
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
