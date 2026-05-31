"""8-dimension coverage analytics for a finished run.

Builds a per-D# breakdown showing which D-values appear in how many
generated prompts. Used by the review page to surface gaps in the
prompt set's spread.

Counted on:
  - PromptEntry.subject_type    → D1 主体
  - PromptEntry.subject_count   → D1 (as bucketed 1 / 2 / 3+)  [not a D# value]
  - PromptEntry.camera_zh       → D4 镜头(by matching name to D4 values)
  - PromptEntry.scene_l1..l4    → D6 场景(by L1 → D6 bucket mapping, best-effort)
  - run.recommended_tags        → declared D1-D8 selections (compare planned vs realized)

This is best-effort coverage from generated content. Things we don't
actually know per-prompt (because the LLM only emits text not D-codes
explicitly):
  - D2 动作 / D3 速度 / D5 景别 / D7 风格 / D8 功能
For these we report the *planned* count from recommended_tags only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from .annotation_schema import ALL_DIMENSIONS, AnnotationDimension, AnnotationValue
from .schema import PromptEntry, Run


# ---------------------------------------------------------------------------
# Per-value coverage
# ---------------------------------------------------------------------------

@dataclass
class ValueCoverage:
    code: str             # e.g. "S1"
    name_zh: str          # "单人角色"
    planned: bool         # was it in recommended_tags?
    hit_count: int        # how many prompts have it (best-effort)


@dataclass
class DimCoverage:
    code: str             # "D1"
    name_zh: str          # "主体"
    values: list[ValueCoverage]
    countable: bool       # True for D1/D4/D6 (we can count from prompts);
                          # False for others where we only show planned
    @property
    def planned_count(self) -> int:
        return sum(1 for v in self.values if v.planned)
    @property
    def hit_count(self) -> int:
        return sum(1 for v in self.values if v.hit_count > 0)
    @property
    def total_prompts_hit(self) -> int:
        return sum(v.hit_count for v in self.values)


@dataclass
class CoverageReport8D:
    total_prompts: int
    dims: list[DimCoverage]

    @property
    def gaps_count(self) -> int:
        """Number of (dim, value) pairs that were planned but unhit (for countable dims)."""
        n = 0
        for d in self.dims:
            if not d.countable:
                continue
            for v in d.values:
                if v.planned and v.hit_count == 0:
                    n += 1
        return n


# ---------------------------------------------------------------------------
# Counting helpers
# ---------------------------------------------------------------------------

# Map PromptEntry.subject_type strings → D1 codes
_SUBJECT_TYPE_TO_D1 = {
    "human": "S1",
    "animal": "S4",
    "object": "S3",
    "vehicle": "S3",     # vehicle goes to "no role" bucket — closest D1 match
    "natural_phenomenon": "S3",
    "abstract_effect": "S3",
}


def _count_d1(prompts: list[PromptEntry]) -> dict[str, int]:
    """D1: count by mapped subject_type, with S2 detected via subject_count>=2 (human) or S5 (mixed)."""
    counts: dict[str, int] = {}
    for p in prompts:
        st = (p.subject_type or "").lower()
        # First map by subject_type
        code = _SUBJECT_TYPE_TO_D1.get(st)
        # Then refine: human with subject_count>=2 → S2
        if st == "human" and (p.subject_count or 1) >= 2:
            code = "S2"
        if code:
            counts[code] = counts.get(code, 0) + 1
    return counts


# Map known D4 camera names (display strings stored in prompt.camera_zh)
# to D4 value codes.
def _build_d4_lookup() -> dict[str, str]:
    """Map fragment of camera_zh text → D4 code."""
    lookup: dict[str, str] = {}
    for d in ALL_DIMENSIONS:
        if d.code != "D4":
            continue
        for v in d.values:
            # name_zh might be "推" / "POV" / "微距/极近"
            for token in v.name_zh.split("/"):
                token = token.strip()
                if token:
                    lookup[token] = v.code
    return lookup


_D4_LOOKUP = _build_d4_lookup()


def _count_d4(prompts: list[PromptEntry]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for p in prompts:
        text = (p.camera_zh or "").strip()
        if not text:
            continue
        # Find longest D4 token contained in text
        for token, code in _D4_LOOKUP.items():
            if token in text:
                counts[code] = counts.get(code, 0) + 1
                break    # one per prompt
    return counts


# Map scene_l1 (CN) → D6 E# code (rough; tag library uses different categories)
_SCENE_L1_TO_D6 = {
    "人类活动场景": "E1",     # too broad; map to "室外自然" as default
    "动物活动场景": "E1",
    "自然风景": "E1",
    "常见事物": "E5",
    "城市建筑": "E2",
    "交通工具": "E9",
    "室内环境": "E5",
    "超现实场景": "E8",
}


def _count_d6(prompts: list[PromptEntry]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for p in prompts:
        l1 = (p.scene_l1 or "").strip()
        code = _SCENE_L1_TO_D6.get(l1)
        if code:
            counts[code] = counts.get(code, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# Top-level
# ---------------------------------------------------------------------------

_COUNTABLE_DIMS = {"D1", "D4", "D6"}


def build_coverage_report(run: Run) -> CoverageReport8D:
    """Compute per-dim coverage for a Run."""
    rec = run.recommended_tags or {}
    prompts = run.prompts or []

    # Count hits per countable dim
    hits_d1 = _count_d1(prompts)
    hits_d4 = _count_d4(prompts)
    hits_d6 = _count_d6(prompts)
    hits_by_dim = {"D1": hits_d1, "D4": hits_d4, "D6": hits_d6}

    out_dims: list[DimCoverage] = []
    for d in ALL_DIMENSIONS:
        planned_set = set(rec.get(d.code, []))
        hits = hits_by_dim.get(d.code, {})
        values = [
            ValueCoverage(
                code=v.code,
                name_zh=v.name_zh,
                planned=v.code in planned_set,
                hit_count=hits.get(v.code, 0),
            )
            for v in d.values
        ]
        out_dims.append(DimCoverage(
            code=d.code,
            name_zh=d.name_zh,
            values=values,
            countable=d.code in _COUNTABLE_DIMS,
        ))

    return CoverageReport8D(total_prompts=len(prompts), dims=out_dims)
