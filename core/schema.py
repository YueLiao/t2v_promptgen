"""Pydantic data models for t2v_promptgen.

Three layers of objects:

1. Capability definition objects — SL2, Axis, CapabilityVersion
   (saved to ~/.t2v_promptgen/memory/capabilities/{slug}/vN.yaml)

2. Prompt entries — PromptEntry
   (the actual generated content; written to runs.db + exported as jsonl)

3. Run-state objects — Run, RunPhase
   (mid-flight state for resume; SQLite-backed)
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = 1


# ---------------------------------------------------------------------------
# Capability definition
# ---------------------------------------------------------------------------

class SL2(BaseModel):
    """Specialty L2 — a fine-grained failure mode under a specialty capability.

    Inherits semantically from one or more general L1+L2 pairs but does not
    overwrite them. Evaluator handbook is rendered from these.
    """
    id: str = Field(..., description="Stable snake_case id, e.g. 'hand_finger_count'")
    name: str = Field(..., description="Chinese display name, e.g. '手指数量错误'")
    inherits_from: list[str] = Field(
        default_factory=list,
        description="Reference to general L1:L2 pairs, e.g. '物理规律与常识:人手/脸/体结构畸形'",
    )
    description: str = Field(..., description="One-paragraph definition of the failure mode")
    judging_criteria_md: str = Field(
        ...,
        description="Markdown body for evaluator handbook: when to mark Yes/No, edge cases",
    )
    stress_keywords: list[str] = Field(
        default_factory=list,
        description="Keywords that hint a prompt is a stress case for this SL2",
    )


class Axis(BaseModel):
    """A test variable (controlled axis) for a specialty capability.

    Each axis has 2-6 enum-like values. Axes are orthogonal — the Cartesian
    product defines the minimum coverage matrix.
    """
    name: str = Field(..., description="Chinese name, e.g. '持物角度'")
    values: list[str] = Field(..., min_length=2, max_length=6)


class CapabilityVersion(BaseModel):
    """A frozen snapshot of one capability's SL2 + axes + meta.

    Written to ~/.t2v_promptgen/memory/capabilities/{slug}/vN.yaml after each
    successful run. Future runs can inherit, modify, or discard.
    """
    slug: str = Field(..., description="Canonical snake_case, e.g. 'human_hand'")
    display_name: str = Field(..., description="Chinese display name")
    version: int = Field(..., ge=1)
    created_at: datetime
    description: str = Field(..., description="Free-form user description from intake")

    sl2_list: list[SL2]
    axes: list[Axis]

    decisions_log: list[dict] = Field(
        default_factory=list,
        description="Audit trail of P1 edits: [{phase, round, action, payload}, ...]",
    )

    # Generation hyperparams used in the run that produced this version
    set_size: int
    difficulty_ratio: dict[str, float] = Field(
        default={"medium": 0.6, "hard": 0.4}
    )
    stress_ratio: float = 0.3

    provider: str = "anthropic"
    model: str = "claude-opus-4-7"

    schema_version: int = SCHEMA_VERSION


# ---------------------------------------------------------------------------
# Prompt entry
# ---------------------------------------------------------------------------

Difficulty = Literal["medium", "hard"]   # easy disabled by B=0:3:2


class PromptEntry(BaseModel):
    """One specialty prompt + metadata.

    Note: no `check_items` field — evaluator handbook covers SL2 judging
    criteria centrally, prompts only declare which SL2 they cover.
    """
    id: str = Field(..., description="e.g. 'spec_hand_001'")
    capability: str = Field(..., description="capability slug")
    capability_version: int

    difficulty: Difficulty
    difficulty_score: float = Field(..., ge=0, description="Heuristic score from qa/difficulty.py")
    is_stress: bool = False

    sl2_covered: list[str] = Field(..., description="SL2 ids this prompt is intended to test")
    axes_values: dict[str, str] = Field(..., description="{axis_name: value} for this prompt")

    subject_count: int = Field(..., ge=1)
    action_count: int = Field(..., ge=1)

    camera_zh: str | None
    camera_en: str | None
    prompt_zh: str
    prompt_en: str

    # Concrete scene grounding (from L3/L4 tag library; v0.8+)
    scene_l1: str | None = None
    scene_l2: str | None = None
    scene_l3: str | None = None
    scene_l4: str | None = None

    generated_at: datetime
    generation_round: int = 1                # incremented on regen


# ---------------------------------------------------------------------------
# Run state
# ---------------------------------------------------------------------------

class Phase(str, Enum):
    P0_INTAKE = "P0_INTAKE"
    P1_DIMENSIONS = "P1_DIMENSIONS"
    P2_PROMPTS = "P2_PROMPTS"
    P3_QA = "P3_QA"
    P4_REVIEW = "P4_REVIEW"
    P5_EXPORT = "P5_EXPORT"
    DONE = "DONE"


class Run(BaseModel):
    """A complete generation run, persisted to runs.db for resume support."""
    id: str = Field(..., description="UUID")
    capability_slug: str
    created_at: datetime
    updated_at: datetime
    phase: Phase

    # P0 outputs
    user_description: str | None = None
    inherited_from_version: int | None = None     # None = fresh start

    # P1 in-progress / final
    sl2_list: list[SL2] = Field(default_factory=list)
    axes: list[Axis] = Field(default_factory=list)
    p1_round: int = 0
    p1_max_rounds: int = 5

    # P2/P3 in-progress
    prompts: list[PromptEntry] = Field(default_factory=list)
    p2_round: int = 0
    p3_retries_remaining: int = 2

    # P4 in-progress
    p4_round: int = 0
    p4_max_rounds: int = 3

    # Config
    target_set_size: int | None = None
    provider: str = "anthropic"
    model: str = "claude-opus-4-7"
    cost_usd_used: float = 0.0
    cost_usd_limit: float = 5.0


# ---------------------------------------------------------------------------
# Coverage report (output)
# ---------------------------------------------------------------------------

class CoverageReport(BaseModel):
    """Generated at P5; shows SL2 × axes-cell hit map."""
    capability: str
    capability_version: int
    total_prompts: int
    sl2_hit_counts: dict[str, int]                      # {sl2_id: count}
    axes_cells_hit: dict[str, int]                      # {"持物角度=胸前|光照=侧光": count}
    difficulty_breakdown: dict[str, int]                # {"medium": N, "hard": N}
    stress_ratio_actual: float
    missing_combos: list[dict] = Field(
        default_factory=list,
        description="Cells with 0 coverage (should be empty after a successful run)",
    )
