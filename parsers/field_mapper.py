"""Guess which file columns hold prompt text vs id vs metadata.

Two strategies:
  - heuristic_guess: pure rule-based, fast, no LLM
  - llm_guess: LLM picks based on sample rows; falls back to heuristic on fail

Heuristic patterns (case-insensitive):
  - 'prompt' / 'text' / 'description' / 'caption' / 'content'  → prompt_zh
  - 'en' / 'english' (in column name)                          → prompt_en
  - column whose values are mostly ASCII → likely prompt_en
  - 'id' / 'idx' / 'index' / 'row'                             → source_id
"""
from __future__ import annotations

import json
import re
from typing import Any

from ..core.rewrite_schema import FieldMapping


# ---------------------------------------------------------------------------
# Heuristic patterns
# ---------------------------------------------------------------------------

_PROMPT_PATTERNS = [
    re.compile(r"prompt", re.I),
    re.compile(r"description", re.I),
    re.compile(r"caption", re.I),
    re.compile(r"content", re.I),
    re.compile(r"^text$", re.I),
    re.compile(r"^body$", re.I),
]

_EN_PATTERNS = [
    re.compile(r"\ben\b", re.I),
    re.compile(r"english", re.I),
    re.compile(r"_en$", re.I),
]

_ZH_PATTERNS = [
    re.compile(r"\bzh\b", re.I),
    re.compile(r"chinese", re.I),
    re.compile(r"中文", re.I),
    re.compile(r"_zh$", re.I),
]

_ID_PATTERNS = [
    re.compile(r"^id$", re.I),
    re.compile(r"^idx$", re.I),
    re.compile(r"^index$", re.I),
    re.compile(r"^row(_?id|_?idx|idx)?$", re.I),    # row, row_id, rowidx, rowIdx
    re.compile(r"^uid$", re.I),
]


def _matches_any(name: str, patterns: list[re.Pattern]) -> bool:
    return any(p.search(name) for p in patterns)


def _is_mostly_ascii(values: list[Any]) -> bool:
    """Used to disambiguate which prompt column is English."""
    samples = [str(v) for v in values if v is not None and str(v).strip()][:20]
    if not samples:
        return False
    ascii_count = sum(1 for s in samples if all(ord(c) < 128 for c in s))
    return ascii_count / len(samples) >= 0.8


def _is_mostly_chinese(values: list[Any]) -> bool:
    """Used to disambiguate which prompt column is Chinese."""
    samples = [str(v) for v in values if v is not None and str(v).strip()][:20]
    if not samples:
        return False
    cn_count = 0
    for s in samples:
        cn_chars = sum(1 for c in s if "一" <= c <= "鿿")
        if cn_chars >= max(1, len(s) // 4):
            cn_count += 1
    return cn_count / len(samples) >= 0.5


# ---------------------------------------------------------------------------
# Heuristic guess
# ---------------------------------------------------------------------------

def heuristic_guess(columns: list[str], sample_rows: list[dict]) -> FieldMapping:
    """Pure rule-based mapping. Returns best-effort; UI lets user override.

    Returns a FieldMapping. May leave prompt_zh OR prompt_en None if
    we can't decide — the caller's validation will reject only when BOTH
    are missing (forced UI prompt).
    """
    # Build (col_name, sample_values) lookup
    col_values: dict[str, list] = {c: [] for c in columns}
    for row in sample_rows:
        for c in columns:
            col_values[c].append(row.get(c))

    prompt_zh: str | None = None
    prompt_en: str | None = None
    source_id: str | None = None

    # Pass 1: name pattern match
    prompt_candidates: list[str] = []   # columns that look prompt-y
    for col in columns:
        if _matches_any(col, _ID_PATTERNS):
            if source_id is None:
                source_id = col
        # Both ZH-specific and prompt-generic patterns count
        is_prompt_like = _matches_any(col, _PROMPT_PATTERNS)
        is_en_marked = _matches_any(col, _EN_PATTERNS)
        is_zh_marked = _matches_any(col, _ZH_PATTERNS)

        if is_zh_marked and not prompt_zh:
            prompt_zh = col
        elif is_en_marked and not prompt_en:
            prompt_en = col
        elif is_prompt_like:
            prompt_candidates.append(col)

    # Pass 2: disambiguate generic prompt candidates by content
    for col in prompt_candidates:
        vals = col_values[col]
        if not prompt_zh and _is_mostly_chinese(vals):
            prompt_zh = col
            continue
        if not prompt_en and _is_mostly_ascii(vals):
            prompt_en = col
            continue
        # If neither marker fits but we still need a prompt column
        if not prompt_zh and not prompt_en:
            # Assign to whichever the content looks like
            if _is_mostly_chinese(vals):
                prompt_zh = col
            else:
                prompt_en = col
            continue
        if not prompt_zh:
            prompt_zh = col
        elif not prompt_en:
            prompt_en = col

    # Pass 3: if still no prompt assigned, pick the longest-string column —
    # but only if it's plausibly prompt-like (avg length ≥ 10 chars)
    if not prompt_zh and not prompt_en:
        best_col = None
        best_len = 0.0
        for col in columns:
            if col == source_id:
                continue
            vals = [v for v in col_values[col] if v is not None]
            if not vals:
                continue
            avg = sum(len(str(v)) for v in vals) / len(vals)
            if avg > best_len:
                best_len = avg
                best_col = col
        # Threshold guard — short strings are likely id / metadata, not prompts
        if best_col and best_len >= 10:
            if _is_mostly_chinese(col_values[best_col]):
                prompt_zh = best_col
            else:
                prompt_en = best_col

    # Don't crash if no valid mapping at all — caller's FieldMapping
    # validator will reject and UI will force user choice.
    if not prompt_zh and not prompt_en:
        # Build an invalid-by-design mapping; let UI handle
        return FieldMapping.model_construct(prompt_zh=None, prompt_en=None, source_id=source_id)

    return FieldMapping(prompt_zh=prompt_zh, prompt_en=prompt_en, source_id=source_id)


# ---------------------------------------------------------------------------
# LLM-assisted guess
# ---------------------------------------------------------------------------

_LLM_SYSTEM = """你是一个 CSV/JSON 列名识别助手。给定一组列名和前几行样本数据,挑出:
- prompt_zh: 含中文 prompt 文本的列(若没有则 null)
- prompt_en: 含英文 prompt 文本的列(若没有则 null)
- source_id: 含原始 id 的列(若没有则 null)

规则:
- prompt_zh 和 prompt_en 至少要确定一个
- 同一列不能同时是 prompt_zh 和 prompt_en
- 输出严格 JSON,顶层 keys: mapping (object), confidence (high|medium|low), reasoning (string)

mapping 字段格式:
{"prompt_zh": "列名" | null, "prompt_en": "列名" | null, "source_id": "列名" | null}

reasoning 一句话说明为什么这么选。"""


def llm_guess(
    columns: list[str],
    sample_rows: list[dict],
    client: Any = None,        # LLMClient | None — kept Any to avoid import cycle
) -> tuple[FieldMapping, str]:
    """LLM-assisted guess. Falls back to heuristic on any failure.

    Returns (mapping, reasoning_text).
    """
    if client is None:
        m = heuristic_guess(columns, sample_rows)
        return m, "无 LLM 客户端,使用启发式猜测"

    user_msg = (
        f"列名: {json.dumps(columns, ensure_ascii=False)}\n\n"
        f"前 {len(sample_rows)} 行样本:\n{json.dumps(sample_rows[:3], ensure_ascii=False, indent=2)}\n\n"
        f"请按 JSON schema 返回。"
    )
    try:
        resp = client.generate(
            messages=[{"role": "user", "content": user_msg}],
            system=_LLM_SYSTEM,
            json_schema={"required": ["mapping"]},
            temperature=0.0,
            max_tokens=400,
        )
        data = resp.content if isinstance(resp.content, dict) else json.loads(resp.content)
        m = data.get("mapping") or {}

        # Validate: LLM may return column names that don't exist
        for k in ("prompt_zh", "prompt_en", "source_id"):
            v = m.get(k)
            if v is not None and v not in columns:
                m[k] = None

        # Validate: at least one prompt column
        if not m.get("prompt_zh") and not m.get("prompt_en"):
            # LLM gave a bad mapping — fall back
            fallback = heuristic_guess(columns, sample_rows)
            return fallback, "LLM 输出无 prompt 列,回退启发式"

        mapping = FieldMapping(
            prompt_zh=m.get("prompt_zh"),
            prompt_en=m.get("prompt_en"),
            source_id=m.get("source_id"),
        )
        return mapping, data.get("reasoning") or "LLM 推断"
    except Exception as exc:
        # Any LLM error → heuristic
        fallback = heuristic_guess(columns, sample_rows)
        return fallback, f"LLM 调用失败 ({type(exc).__name__}),回退启发式"
