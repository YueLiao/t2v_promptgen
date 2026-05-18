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
from ..phases.qa import run as run_qa_phase, QAReport


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

_PROMPTS_SYSTEM = """你是 T2V 评测 prompt 设计专家。给定一组 SL2(失败模式)、axes(测试变量)、"具体场景候选词表"和"本轮多样性配额",生成 N 条 prompt。

# 视频特性铁律(违反就废)

T2V 生成的是 **5 秒视频**,不是静态图。每条 prompt 都必须描述**有动作、有时序**的画面。

## A. 主体动作(每条必需)
- 每条至少 **2 个动作动词**,hard 难度至少 **3 个**
- 动词要可视化:走、跑、抓起、放下、转身、抬手、弯腰、握紧、松开、推、拉、旋转、翻转、张开、合上、挥、举、点头、眨眼、迈步、蹲下、起立、撞、滚、漂浮、滴落、燃烧、绽放……
- 仅描述位置 / 外观 / 静态状态 = 废稿
- ❌ 反例:"一只五指分明的手在握住胡萝卜,纹理清晰"(单时刻+外观)
- ❌ 反例:"他站在咖啡店前"(只有位置)
- ❌ 反例:"剑身静止,手指包裹剑柄"(明写"静止"是大忌)
- ✅ 正例:"她左手抓起胡萝卜,右手用刀切下三段,随即将刀放回案板"

## B. 时序结构(每条都要有,hard 强制 ≥3 个时序词)
- 用时序词切成两段以上:
  - 起初…接着…最后… / 先…然后… / 随即 / 紧接着
  - 突然 / 逐渐 / 慢慢 / 加速 / 减速 / 短暂停顿后…
- ✅ 正例:"水滴从指尖滑落,在桌面溅开一圈涟漪,随即扩散消失"

## C. 严禁的静态信号
- "保持不变 / 纹丝不动 / 始终静止 / 一动不动" 全部废稿

# 主体多样性(强制,不能整批都是人)

每批 prompt 的 subject_type 必须分散,目标分布(可有 ±5% 浮动):

| subject_type | 占比 | 例子 |
|---|---|---|
| `human`           | **≤40%** | 人物动作、人物互动 |
| `animal`          | 15-25% | 猫狗鸟鱼昆虫;野生 / 宠物 / 群体 |
| `object`          | 20-30% | 物体自身运动:杯子、工具、纸、机械、玩具 |
| `vehicle`         | 5-10%  | 汽车、自行车、船、飞机、火车 |
| `natural_phenomenon` | 10-15% | 水、火、烟、雨、雪、云、闪电 |
| `abstract_effect` | 0-5%   | 光线、粒子、能量场、烟花 |

⚠ **每条 prompt 必须显式选一个 subject_type**,我会按批统计实际分布并在下批告诉你需要补什么类型。**当某类已超配额时,绝对不要再生成那类**。

# 难度梯度(必须真的有梯度)

**medium (60%)** — 单主体 + 2-3 段动作 + 简单因果
**hard (40%)** — 必须满足下面 **至少 2 条**:
  1. **多主体**(2-3 个独立运动的主体)
  2. **3+ 段时序动作**,且中间有节奏变化
  3. **跨域 SL2 覆盖**(同时测 2 个 SL2,且来自不同子类别)
  4. **微妙的物理 / 状态依赖**(因果链 / 不可逆变化 / 速度突变 / 遮挡前后一致)
  5. **罕见动词组合**(撞 + 弹、断 + 弹回、融化 + 凝固、消散 + 聚合)

# Stress case(必占 30%,标 is_stress=true)

stress 不只是"难",而是**故意挑战模型的薄弱环节**。一条 stress 必须组合 ≥2 种刁难:
- 多主体 + 异步动作(两人击掌但时序错位)
- 高速 + 遮挡(快速移动物体被柱子瞬间挡住)
- 不可逆 + 多步(冰块融化成水后被加热蒸发,中间不能"复原")
- 微小 + 复杂(蚂蚁排队搬食物,需保持队形)
- 透明 / 反射 + 形变(玻璃杯倾倒,液体折射变化)
- 群体同步(鸟群转向、机械齿轮咬合)

# 其他规则
- 每条 prompt 测 **≥1 个 SL2**(hard 难度建议 ≥2),设置所有 axes 的值
- 每条 prompt 从场景词表挑 1 个,把 L4(优先)或 L3 名写进文本
- 严禁抽象词:复杂场景 / 动态背景 / 普通环境 / 某地 / 某处
- 中文 30-120 字,英文 15-50 词,同一画面
- 镜头必填:中文"镜头不动 / 慢慢推近 / 慢慢拉远 / 从左到右移动 / 从右到左移动 / 向上移动 / 向下移动 / 跟随主体 / 第一视角向前移动",英文对应电影术语
- 禁用摄影术语:三分法 / 写实风格 / 黄金时刻 / 景深 / HDR
- 不要写判定标准:不要"应该 / 避免 / 确保"
- 输出严格 JSON,顶层 prompts 数组,不要 markdown

# 输出 schema
{
  "prompts": [
    {
      "id": "spec_xxx_001",
      "difficulty": "medium" | "hard",
      "is_stress": true | false,
      "sl2_covered": ["sl2_id_1", "sl2_id_2"],
      "axes_values": {"轴名": "值"},
      "scene_l1": "...", "scene_l2": "...", "scene_l3": "...", "scene_l4": "...",
      "subject_type": "human|animal|object|vehicle|natural_phenomenon|abstract_effect",
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

    # Target distribution (must match _PROMPTS_SYSTEM)
    SUBJECT_QUOTA = {
        "human": 0.40,
        "animal": 0.20,
        "object": 0.25,
        "vehicle": 0.08,
        "natural_phenomenon": 0.12,
        "abstract_effect": 0.03,
    }
    # Running counters across batches
    subject_counts: dict[str, int] = {k: 0 for k in SUBJECT_QUOTA}
    scene_l3_counts: dict[str, int] = {}
    verb_counts: dict[str, int] = {}

    def _quota_hint(done: int, target: int) -> str:
        """Build a steering message about what's over/under-represented."""
        if done == 0:
            return ""
        lines = ["【本轮多样性进度 — 接下来生成时请按此倾斜】"]
        remaining = max(target - done, 1)
        for st, target_frac in SUBJECT_QUOTA.items():
            target_count = int(target_frac * target)
            cur = subject_counts.get(st, 0)
            cur_frac = cur / done if done else 0
            if cur >= target_count:
                lines.append(f"- {st}: 已 {cur}({cur_frac:.0%}) ≥ 目标 {target_count}({target_frac:.0%}) → ⛔ 不要再生成此类")
            elif cur_frac < target_frac * 0.7:
                deficit = target_count - cur
                lines.append(f"- {st}: 已 {cur}({cur_frac:.0%}) << 目标 {target_frac:.0%} → ✅ 本批多生成,至少补 {deficit} 条")
            else:
                lines.append(f"- {st}: 已 {cur}({cur_frac:.0%}) 进度正常,可继续")
        # Scene over-use warning
        hot_scenes = [(s, n) for s, n in scene_l3_counts.items() if n >= 3]
        if hot_scenes:
            tops = ", ".join(f"{s}({n})" for s, n in hot_scenes[:5])
            lines.append(f"- ⚠ 场景已重复过多 (≥3 次):{tops} → 避开这些 L3")
        # Verb over-use
        hot_verbs = [(v, n) for v, n in verb_counts.items() if n >= max(3, target // 6)]
        if hot_verbs:
            tops = ", ".join(f"{v}({n})" for v, n in hot_verbs[:5])
            lines.append(f"- ⚠ 动词已用太多次:{tops} → 换其他动词")
        return "\n".join(lines)

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

        diversity_hint = _quota_hint(len(all_prompts), target_size)

        user_msg = f"""专项能力: {capability}

SL2 列表(全集,选择要测的):
{json.dumps(sl2_summary, ensure_ascii=False, indent=2)}

Axes 列表(每条 prompt 必须设置所有轴的具体值):
{json.dumps(axes_summary, ensure_ascii=False, indent=2)}

可选具体场景词表(从中挑选 1 个作为画面背景,把 l3/l4 名称写进 prompt):
{scenes_md}

{diversity_hint}

请生成 {n} 条 prompt,id 从 spec_{capability}_{start_idx:03d} 开始递增。
要求:
- 每条 prompt 显式选 subject_type,按上面的【多样性进度】倾斜
- 难度 medium 60% / hard 40%;is_stress 约 30%(stress 必须组合 ≥2 种刁难,不只是"更难")
- hard 难度的至少满足 system prompt 里"hard 标准"的 2 项
- 多样化:同一 L3 场景一批最多 2 次,同一动作动词一批最多 3 次
- 不要重复 SL2 × axes 组合
- 严禁抽象词、判定标准、静态描述
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
                    motion_hits = count_kw(prompt_zh, MOTION_VERB_KW)
                    has_static = any(k in prompt_zh for k in STATIC_KW)
                    declared_verbs = item.get("motion_verbs") or []
                    if motion_hits < 2 and len(declared_verbs) < 2:
                        continue
                    if has_static:
                        continue

                    # Subject-diversity gate: drop if this subject_type is already
                    # over quota (hard cap; the LLM was told but sometimes ignores).
                    subject_type = (item.get("subject_type") or "human").lower()
                    if subject_type not in SUBJECT_QUOTA:
                        subject_type = "object"   # bucket unknowns into 'object'
                    target_count = int(SUBJECT_QUOTA[subject_type] * target_size) + 1
                    if subject_counts.get(subject_type, 0) >= target_count:
                        # Already at quota → reject and let loop re-fetch a different type
                        continue

                    sc = diff_score(
                        prompt_zh,
                        action_count=item.get("action_count", 1),
                        subject_count=item.get("subject_count", 1),
                        axes_values=item.get("axes_values", {}),
                        sl2_covered=item.get("sl2_covered", []),
                    )
                    # Trust LLM-declared difficulty as primary; only override
                    # when scorer strongly disagrees. Keeps the 60/40 split
                    # while catching obvious mismatches.
                    declared = item.get("difficulty", "medium")
                    if declared == "easy":
                        declared = "medium"
                    if declared == "medium" and sc >= 9:
                        difficulty = "hard"        # clearly more complex
                    elif declared == "hard" and sc < 5:
                        difficulty = "medium"      # not actually hard
                    else:
                        difficulty = declared
                    entry = PromptEntry(
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
                        subject_type=subject_type,
                        motion_verbs=item.get("motion_verbs", []) or [],
                        temporal_markers=item.get("temporal_markers", []) or [],
                        generated_at=datetime.now(),
                    )
                    all_prompts.append(entry)

                    # Update running diversity counters
                    subject_counts[subject_type] = subject_counts.get(subject_type, 0) + 1
                    l3 = item.get("scene_l3")
                    if l3:
                        scene_l3_counts[l3] = scene_l3_counts.get(l3, 0) + 1
                    for v in (item.get("motion_verbs") or [])[:3]:
                        verb_counts[v] = verb_counts.get(v, 0) + 1
                except Exception:
                    continue
        except Exception:
            # Batch failed; bail with whatever we have
            break
    return all_prompts


# ---------------------------------------------------------------------------
# Phase 3 — QA (real, batched LLM judges)
# ---------------------------------------------------------------------------

def run_qa_real(
    prompts: list[PromptEntry],
    sl2_list: list[SL2],
    axes: list[Axis],
    client: LLMClient | None = None,
) -> QAReport:
    """Wrap phases.qa.run with the same error-tolerant pattern as the others.

    Mutates `prompts` in place: each gets qa_* fields populated. Returns a
    set-level QAReport.

    If `client` is None or every LLM judge call fails, deterministic rules
    still run; LLM-tier scores stay None and prompts pass if rules pass.
    """
    return run_qa_phase(prompts, sl2_list, axes, client=client)
