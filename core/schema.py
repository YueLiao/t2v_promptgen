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

from .rewrite_schema import SourceFile, SourcePrompt, RewriteDirective  # noqa: F401

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

    # Subject categorization for diversity enforcement
    # human / animal / object / vehicle / natural_phenomenon / abstract_effect
    subject_type: str | None = Field(
        default=None,
        description="Coarse subject category used to enforce cross-batch diversity"
    )

    # Self-declared motion verbs and temporal markers (also used by P3 gate)
    motion_verbs: list[str] = Field(default_factory=list)
    temporal_markers: list[str] = Field(default_factory=list)

    generated_at: datetime
    generation_round: int = 1                # incremented on regen

    # ---- Rewrite-feature fields (None for generate-source runs) ----
    source_id: str | None = Field(
        default=None,
        description="Link to SourcePrompt.source_id when this came from rewrite",
    )
    rewrite_diff: str | None = Field(
        default=None,
        description="LLM-authored one-liner describing what changed",
    )
    rewrite_kept_score: int | None = Field(
        default=None, ge=0, le=10,
        description="How well original meaning was preserved (0-10); threshold 5",
    )
    rewrite_adherence_score: int | None = Field(
        default=None, ge=0, le=10,
        description="How well the LLM followed the directive (0-10); threshold 7",
    )
    rewrite_accepted: bool | None = Field(
        default=None,
        description="R5 reviewer decision; None = not reviewed yet",
    )

    # ---- QA results (populated by P3; None/empty before P3 ran) ----
    qa_rule_errors: list[str] = Field(
        default_factory=list,
        description="Deterministic rule violations: length / banned terms / missing fields"
    )
    qa_naturalness_zh: int | None = Field(
        default=None,
        description="LLM-judged Chinese naturalness 0-10, threshold 7"
    )
    qa_naturalness_en: int | None = Field(
        default=None,
        description="LLM-judged English naturalness 0-10, threshold 7"
    )
    qa_naturalness_issues: list[str] = Field(
        default_factory=list,
        description="Short notes from naturalness judge"
    )
    qa_judged_sl2: list[str] = Field(
        default_factory=list,
        description="SL2 ids the LLM judge independently thinks this prompt covers"
    )
    qa_coverage_match: bool | None = Field(
        default=None,
        description="True iff judged_sl2 overlaps declared sl2_covered"
    )
    qa_passed: bool = Field(
        default=True,
        description="Aggregate pass flag (rules empty AND naturalness ≥7 AND coverage match)"
    )
    needs_human_review: bool = Field(
        default=False,
        description="QA flagged but kept; review page highlights it for the human"
    )


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
    capability_display_name: str | None = Field(
        default=None,
        description="Human-readable zh name shown in UI; falls back to slug when None"
    )
    # Rewrite multi-target spread seed. Server-side pre-assignment uses this
    # so the user gets deterministic + reproducible distribution; "🎲 换一种
    # 分摊" in the UI just bumps this. None = derive from run.id on first use.
    rewrite_seed: int | None = Field(default=None)
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

    # P1 8-dimension tag recommendations (from annotation_schema D1-D8)
    # Map: dim_code ("D1", "D2", ...) → list of value codes ("S1", "A30", ...)
    # Populated by LLM in P1, editable by user in the dimensions UI.
    recommended_tags: dict[str, list[str]] = Field(default_factory=dict)

    # Frozen snapshot of the original LLM recommendation (before any user edits)
    # Used to show "AI 推" markers in the UI even after user toggles
    original_ai_tags: dict[str, list[str]] = Field(default_factory=dict)

    # User-added custom tags (this-run-only, not promoted to global registry)
    # Map: dim_code → list of {code, name_zh}
    custom_tags: dict[str, list[dict]] = Field(default_factory=dict)

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

    # ---- Rewrite-feature fields (default to None / empty for generate runs) ----
    source: Literal["generate", "rewrite"] = Field(
        default="generate",
        description="Task type: 'generate' = from-scratch, 'rewrite' = rewrite uploaded list",
    )
    source_file: SourceFile | None = None
    source_prompts: list[SourcePrompt] = Field(default_factory=list)
    field_mapping: dict[str, str] = Field(default_factory=dict)
    rewrite_directive: RewriteDirective | None = None
    rewrite_round: int = Field(default=0, ge=0)
    rewrite_max_rounds: int = Field(default=3, ge=1, le=10)


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
