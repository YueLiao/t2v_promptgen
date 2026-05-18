"""LLM judge for the P1 dimensions design (SL2 list + axes).

After the dimensions generator produces a draft, this judge critiques it
against the user's original description. Five things it looks for:

1. 可独立判定性 — each SL2 must be Yes/No answerable from a 5s video
2. 正交性       — SL2s shouldn't semantically overlap
3. 覆盖完整性   — does the list cover the failure modes the user described?
4. 可视性       — can the failure actually be seen on screen?
5. 粒度一致性   — all SL2s at roughly the same conceptual level

For axes: orthogonality, trigger relevance (does the axis stress the SL2s?),
value count (2-6), concrete vs abstract values.

Single LLM call returns a structured critique with per-item severity and
overall verdict.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Literal

from ..core.schema import SL2, Axis
from ..llm.base import LLMClient


Severity = Literal["info", "warn", "error"]
Verdict = Literal["good_to_go", "minor_issues", "needs_revision"]


@dataclass
class DimensionsCritique:
    score: int = 0                      # 0-10
    verdict: Verdict = "good_to_go"
    summary: str = ""
    sl2_issues: list[dict] = field(default_factory=list)
    axes_issues: list[dict] = field(default_factory=list)
    gaps: list[dict] = field(default_factory=list)
    redundancies: list[dict] = field(default_factory=list)
    suggested_feedback: str = ""        # Drop-in text for the regen feedback box
    judge_ran: bool = False             # False = no LLM, all defaults

    def to_dict(self) -> dict:
        return {
            "score": self.score,
            "verdict": self.verdict,
            "summary": self.summary,
            "sl2_issues": self.sl2_issues,
            "axes_issues": self.axes_issues,
            "gaps": self.gaps,
            "redundancies": self.redundancies,
            "suggested_feedback": self.suggested_feedback,
            "judge_ran": self.judge_ran,
        }


_SYSTEM = """你是 T2V 评测专家,审查一组"评测维度设计"的质量。

输入:
- 用户对该能力的原始描述
- 生成器产出的检查项 (SL2) 列表
- 生成器产出的测试变量 (axes) 列表

你要找出 5 类问题:

1. **可独立判定性** — 每个 SL2 必须能被评测员看着 5 秒视频明确勾"是 / 否"。模糊的(如"整体质量好")不行。
2. **正交性** — SL2 之间不能语义重叠。axes 之间也不能(比如"光照"和"时间段"如果都编码亮度)。
3. **覆盖完整性** — 看用户描述里提到的所有失败模式,SL2 列表有没有漏掉重要的。
4. **可视性** — 失败必须 5 秒视频里能直接看见。"模型创作意图"这种看不见。
5. **粒度一致性** — 所有 SL2 应该在同一抽象层次。把"手指数量"(具体)和"人手整体"(笼统)并列就不一致。

axes 还要检查:
- 值是不是 2-6 个
- 值是不是具体(如"顺光/侧光/逆光"是具体,"好/中/差"是抽象)
- 这个 axis 真的能触发某些 SL2 吗?(无用 axis 浪费组合数)

严格 JSON 输出,不要 markdown:
{
  "score": 0-10 整数,
  "verdict": "good_to_go" | "minor_issues" | "needs_revision",
  "summary": "一句话总评",
  "sl2_issues": [
    {"sl2_id": "...", "severity": "info|warn|error", "issue": "...", "suggestion": "..."}
  ],
  "axes_issues": [
    {"axis_name": "...", "severity": "info|warn|error", "issue": "...", "suggestion": "..."}
  ],
  "gaps": [
    {"what": "缺少 ... 的检查项", "why": "用户描述提到了 ... 但 SL2 没覆盖"}
  ],
  "redundancies": [
    {"items": ["sl2_a_id", "sl2_b_id"], "issue": "两条几乎同义"}
  ],
  "suggested_feedback": "如果让用户'重新生成',这条文字直接当作反馈意见 — 列出最关键的 2-3 条修改建议"
}

评分基准:
- 9-10 good_to_go: 列表干净,粒度一致,无明显空缺
- 6-8  minor_issues: 有 1-2 处可改但不影响使用
- 0-5  needs_revision: 多处严重问题,建议重生

只列你真正发现的问题,没问题就空数组。不要凑数。"""


def judge_dimensions(
    description: str,
    sl2_list: list[SL2],
    axes: list[Axis],
    client: LLMClient | None = None,
) -> DimensionsCritique:
    """Judge an SL2 + axes draft. Returns a critique (defaults if no client)."""
    if client is None or not sl2_list:
        return DimensionsCritique()

    sl2_payload = [
        {
            "id": s.id,
            "name": s.name,
            "description": s.description,
            "stress_keywords": s.stress_keywords,
        }
        for s in sl2_list
    ]
    axes_payload = [
        {"name": a.name, "values": a.values} for a in axes
    ]

    user_msg = (
        f"【用户原始描述】\n{description}\n\n"
        f"【生成的 SL2 检查项】\n{json.dumps(sl2_payload, ensure_ascii=False, indent=2)}\n\n"
        f"【生成的 axes 测试变量】\n{json.dumps(axes_payload, ensure_ascii=False, indent=2)}\n\n"
        f"请评审并按 JSON schema 返回。"
    )

    try:
        resp = client.generate(
            messages=[{"role": "user", "content": user_msg}],
            system=_SYSTEM,
            json_schema={"required": ["score", "verdict", "summary"]},
            temperature=0.0,
            max_tokens=2500,
        )
        data = resp.content if isinstance(resp.content, dict) else json.loads(resp.content)
    except Exception:
        return DimensionsCritique()

    # Defensive parsing
    valid_sl2_ids = {s.id for s in sl2_list}
    valid_axes_names = {a.name for a in axes}

    sl2_issues = [
        item for item in (data.get("sl2_issues") or [])
        if isinstance(item, dict) and item.get("sl2_id") in valid_sl2_ids
    ]
    axes_issues = [
        item for item in (data.get("axes_issues") or [])
        if isinstance(item, dict) and item.get("axis_name") in valid_axes_names
    ]
    gaps = [item for item in (data.get("gaps") or []) if isinstance(item, dict)]
    redundancies = [
        item for item in (data.get("redundancies") or []) if isinstance(item, dict)
    ]

    verdict = data.get("verdict", "minor_issues")
    if verdict not in ("good_to_go", "minor_issues", "needs_revision"):
        verdict = "minor_issues"

    return DimensionsCritique(
        score=int(data.get("score", 0) or 0),
        verdict=verdict,
        summary=str(data.get("summary", "")),
        sl2_issues=sl2_issues,
        axes_issues=axes_issues,
        gaps=gaps,
        redundancies=redundancies,
        suggested_feedback=str(data.get("suggested_feedback", "")),
        judge_ran=True,
    )
