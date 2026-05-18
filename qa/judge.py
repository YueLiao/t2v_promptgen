"""LLM-judge semantic checks (batched).

Two judges run after the deterministic rules pass:

  1. naturalness_batch — 0-10 ZH/EN score per prompt, threshold 7
  2. coverage_audit_batch — LLM independently classifies which SL2 the prompt
     actually tests, compared against the generator's self-declared sl2_covered

Both batch ~10 prompts per LLM call to amortize prompt overhead.
"""
from __future__ import annotations

import json
from typing import Any

from ..core.schema import PromptEntry, SL2
from ..llm.base import LLMClient


_NATURALNESS_SYSTEM = """你是 T2V prompt 文字质检员。给每条 prompt 的中文 (zh) 和英文 (en) 分别打 0-10 分。

评分标准:
- 9-10 自然流畅,达到专业 prompt 水准
- 7-8 基本通顺,有小瑕疵但可用(阈值,7 以上算通过)
- 5-6 生硬,翻译腔明显,或描述含混
- 0-4 语病明显 / 不通顺 / 信息缺失

判断要点:
- 中文要像人写的中文,不是英文直译
- 英文要符合 prompt 工程惯例(简洁的画面描述句)
- 中英要描述同一画面(实质等价,非逐字翻译)
- 描述必须有动态(动作 + 时序),纯静态外观描述酌情扣 1-2 分

只返回 JSON,顶层 key = "scores":
{
  "scores": [
    {"id": "spec_xxx_001", "zh": 8, "en": 7, "issues": ["短语别扭"]},
    ...
  ]
}
issues 是 0-2 条短评,只在 < 7 时写。"""


_COVERAGE_SYSTEM = """你是 T2V 评测维度专家。

我会给你一个 SL2(失败模式)清单 + 一批 prompt。请独立判断:每条 prompt 实际能触发哪些 SL2,即"评测员看完这条 prompt 的视频后,真的会检查 SL2 列表里的哪几项"。

不要看 prompt 自报的 sl2_covered。一条 prompt 可触发 0~3 个 SL2。

只返回 JSON,顶层 key = "audit":
{
  "audit": [
    {"id": "spec_xxx_001", "judged_sl2": ["sl2_id_1", "sl2_id_2"]},
    ...
  ]
}
judged_sl2 必须是输入 SL2 清单里的 id,不要发明新 id。"""


def naturalness_batch(
    prompts: list[PromptEntry],
    client: LLMClient,
    batch_size: int = 10,
) -> dict[str, dict[str, Any]]:
    """Score prompt naturalness in batches.

    Returns: {prompt_id: {"zh": int, "en": int, "issues": [str]}}.
    Failed batches → all prompts in that batch get score None.
    """
    results: dict[str, dict[str, Any]] = {}

    for i in range(0, len(prompts), batch_size):
        chunk = prompts[i:i + batch_size]
        payload = [{"id": p.id, "zh": p.prompt_zh, "en": p.prompt_en} for p in chunk]
        user_msg = f"评分这批 {len(chunk)} 条 prompt:\n{json.dumps(payload, ensure_ascii=False)}"

        try:
            resp = client.generate(
                messages=[{"role": "user", "content": user_msg}],
                system=_NATURALNESS_SYSTEM,
                json_schema={"required": ["scores"]},
                temperature=0.0,
                max_tokens=2000,
            )
            data = resp.content if isinstance(resp.content, dict) else json.loads(resp.content)
            for item in data.get("scores", []):
                pid = item.get("id")
                if pid:
                    results[pid] = {
                        "zh": int(item.get("zh", 0)),
                        "en": int(item.get("en", 0)),
                        "issues": item.get("issues", []) or [],
                    }
        except Exception:
            # Batch failed; leave these prompts with no score
            continue

    return results


def coverage_audit_batch(
    prompts: list[PromptEntry],
    sl2_list: list[SL2],
    client: LLMClient,
    batch_size: int = 10,
) -> dict[str, list[str]]:
    """Independently re-classify which SL2 each prompt actually tests.

    Returns: {prompt_id: [sl2_id, ...]}.
    Failed batches → those prompts get empty list.
    """
    results: dict[str, list[str]] = {}
    valid_ids = {s.id for s in sl2_list}

    sl2_block = "\n".join(
        f"- {s.id}: {s.name} — {s.description}" for s in sl2_list
    )

    for i in range(0, len(prompts), batch_size):
        chunk = prompts[i:i + batch_size]
        payload = [{"id": p.id, "prompt_zh": p.prompt_zh} for p in chunk]
        user_msg = (
            f"SL2 清单:\n{sl2_block}\n\n"
            f"待审 prompt({len(chunk)} 条):\n"
            f"{json.dumps(payload, ensure_ascii=False)}"
        )

        try:
            resp = client.generate(
                messages=[{"role": "user", "content": user_msg}],
                system=_COVERAGE_SYSTEM,
                json_schema={"required": ["audit"]},
                temperature=0.0,
                max_tokens=2000,
            )
            data = resp.content if isinstance(resp.content, dict) else json.loads(resp.content)
            for item in data.get("audit", []):
                pid = item.get("id")
                if not pid:
                    continue
                judged = [s for s in item.get("judged_sl2", []) if s in valid_ids]
                results[pid] = judged
        except Exception:
            continue

    return results
