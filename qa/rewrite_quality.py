"""Rewrite-specific QA judges (R4).

Two new LLM-based judges run in addition to the existing P3 stack:

  1. **Keep score** — "How much of the original prompt's intent survived?"
     0-10. Low (<5) = LLM rewrote too aggressively, original meaning lost.
     High (>8) but adherence low = LLM ignored the directive.

  2. **Adherence score** — "Did the LLM actually execute the directive?"
     0-10. Low (<7) = LLM produced a fluent prompt but didn't apply the
     requested transformations (cards + free text).

Both run batched 10/call at temperature 0.0 for stability. Returns
dicts keyed by source_id so callers can attach scores to PromptEntry.
"""
from __future__ import annotations

import json

from ..core.rewrite_schema import RewriteDirective, SourcePrompt
from ..core.schema import PromptEntry
from ..llm.base import LLMClient


_KEEP_SYSTEM = """你是 T2V 改写质量评审。每对 (原 prompt, 改写后 prompt) 给一个**保持率**分数 0-10。

评分标准:
- 9-10 原意完全保留,只在指令要求的维度上改;主体 / 核心动作 / 重要细节都还在
- 7-8 主要内容保留,少量细节微调,原意可识别
- 5-6 部分核心改变,但仍能看出和原 prompt 有关
- 3-4 严重偏离原意,只剩弱关联
- 0-2 完全推翻了原 prompt,看不出关系

阈值:**7 分为保持率"通过"** (低于 5 算严重失败,UI 会标红警告)

返回 JSON,顶层 key = "scores":
{
  "scores": [
    {"source_id": "1", "score": 8, "note": "可选:< 7 时一句话说明丢失了什么"},
    ...
  ]
}"""


_ADHERENCE_SYSTEM = """你是 T2V 改写质量评审。给定**改写指令**和每条 (原 prompt, 改写后 prompt),判断改写是否**真的执行了指令**,打 0-10 分。

评分标准:
- 9-10 指令的每条要求都体现在新 prompt 里,文本可直接看出来
- 7-8 主要指令都执行了,小部分未完全落实
- 5-6 指令部分执行,另有一些被忽略
- 3-4 大部分指令没执行,改写偏离了 directive
- 0-2 几乎没按指令改,看不出 directive 的痕迹

阈值:**7 分为指令遵循"通过"**

返回 JSON,顶层 key = "scores":
{
  "scores": [
    {"source_id": "1", "score": 9, "note": "可选:< 7 时一句话说哪些指令没落实"},
    ...
  ]
}"""


def _batch(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _build_pairs_payload(
    pairs: list[tuple[SourcePrompt, PromptEntry]],
) -> str:
    """Serialize pairs to compact JSON for the LLM prompt."""
    return json.dumps(
        [
            {
                "source_id": sp.source_id,
                "original": (sp.original_text or sp.original_text_en or "").strip(),
                "rewritten": pe.prompt_zh or pe.prompt_en or "",
            }
            for sp, pe in pairs
        ],
        ensure_ascii=False,
        indent=2,
    )


def _parse_scores(data: dict, valid_ids: set[str]) -> dict[str, int]:
    """Pick scores from LLM response. Filter unknown ids."""
    out: dict[str, int] = {}
    for item in (data.get("scores") or []):
        sid = str(item.get("source_id") or "")
        if sid not in valid_ids:
            continue
        try:
            score = int(item.get("score"))
        except (TypeError, ValueError):
            continue
        out[sid] = max(0, min(10, score))
    return out


def measure_keep_scores(
    pairs: list[tuple[SourcePrompt, PromptEntry]],
    client: LLMClient,
    batch_size: int = 10,
) -> dict[str, int]:
    """LLM-judge keep score for each (original, rewritten) pair.

    Returns {source_id: 0-10}. Missing ids = judge call failed for that batch.
    """
    if client is None or not pairs:
        return {}

    results: dict[str, int] = {}
    for chunk in _batch(pairs, batch_size):
        valid_ids = {sp.source_id for sp, _ in chunk}
        user_msg = f"评分这批 {len(chunk)} 对:\n{_build_pairs_payload(chunk)}"
        try:
            resp = client.generate(
                messages=[{"role": "user", "content": user_msg}],
                system=_KEEP_SYSTEM,
                json_schema={"required": ["scores"]},
                temperature=0.0,
                max_tokens=2000,
            )
            data = resp.content if isinstance(resp.content, dict) else json.loads(resp.content)
            results.update(_parse_scores(data, valid_ids))
        except Exception:
            continue
    return results


def _format_directive(directive: RewriteDirective) -> str:
    """Render the directive for the adherence judge."""
    from ..phases.rewrite_cards import card_for, render_card

    lines: list[str] = []
    if directive.transforms:
        for t in sorted(directive.transforms, key=lambda x: x.order):
            card = card_for(t.id)
            if card is None:
                continue
            lines.append(f"  {t.order + 1}. [{card.name_zh}] {render_card(card, t.params)}")
    if directive.free_text.strip():
        lines.append(f"  自由意图:{directive.free_text.strip()}")
    return "\n".join(lines) if lines else "(空)"


def measure_adherence_scores(
    pairs: list[tuple[SourcePrompt, PromptEntry]],
    directive: RewriteDirective,
    client: LLMClient,
    batch_size: int = 10,
) -> dict[str, int]:
    """LLM-judge adherence score for each (original, rewritten) pair."""
    if client is None or not pairs or directive is None:
        return {}

    directive_text = _format_directive(directive)

    results: dict[str, int] = {}
    for chunk in _batch(pairs, batch_size):
        valid_ids = {sp.source_id for sp, _ in chunk}
        user_msg = (
            f"【改写指令】\n{directive_text}\n\n"
            f"【{len(chunk)} 对待评分】\n{_build_pairs_payload(chunk)}"
        )
        try:
            resp = client.generate(
                messages=[{"role": "user", "content": user_msg}],
                system=_ADHERENCE_SYSTEM,
                json_schema={"required": ["scores"]},
                temperature=0.0,
                max_tokens=2000,
            )
            data = resp.content if isinstance(resp.content, dict) else json.loads(resp.content)
            results.update(_parse_scores(data, valid_ids))
        except Exception:
            continue
    return results


# ---------------------------------------------------------------------------
# Aggregation helper — attach scores back to PromptEntries
# ---------------------------------------------------------------------------

def attach_scores_to_entries(
    pairs: list[tuple[SourcePrompt, PromptEntry]],
    keep: dict[str, int],
    adherence: dict[str, int],
    keep_threshold: int = 5,
    adherence_threshold: int = 7,
) -> dict:
    """Write keep/adherence scores onto PromptEntry; return summary."""
    n_total = len(pairs)
    keep_pass = adh_pass = both_pass = 0

    for sp, pe in pairs:
        k = keep.get(sp.source_id)
        a = adherence.get(sp.source_id)
        pe.rewrite_kept_score = k
        pe.rewrite_adherence_score = a

        k_ok = (k is None) or (k >= keep_threshold)
        a_ok = (a is None) or (a >= adherence_threshold)
        if k_ok:
            keep_pass += 1
        if a_ok:
            adh_pass += 1
        if k_ok and a_ok:
            both_pass += 1

    keeps = [v for v in keep.values()]
    adhs = [v for v in adherence.values()]
    return {
        "total": n_total,
        "keep_pass": keep_pass,
        "adherence_pass": adh_pass,
        "both_pass": both_pass,
        "keep_avg": round(sum(keeps) / len(keeps), 1) if keeps else None,
        "adherence_avg": round(sum(adhs) / len(adhs), 1) if adhs else None,
    }
