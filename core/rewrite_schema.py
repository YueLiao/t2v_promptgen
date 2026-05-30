"""Pydantic schemas for the prompt-rewrite feature (R0-R6).

See docs/design_prompt_rewrite.md §5 for the data model rationale.
All models have explicit field constraints and validators to enforce
the invariants listed in §5.3.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Supported formats and reserved card ids
# ---------------------------------------------------------------------------

SourceFormat = Literal["json", "jsonl", "txt", "csv", "xlsx"]

TransformId = Literal[
    # 主体类
    "subject_swap",
    "add_interaction",
    "add_micro_action",
    # 场景类
    "scene_shift",
    "style_apply",
    "camera_set",
    # 时序类
    "add_temporal",
    "add_causal_chain",
    "add_irreversibility",
    # 动作类
    "action_chain_extend",
    "speed_adjust",
    # 难度类
    "difficulty_up",
    # Reserved for v0.10
    "multi_subject",
    "localize_zh",
    "stress_inject",
    "bilingualize",
]


# ---------------------------------------------------------------------------
# SourceFile — metadata about the uploaded file
# ---------------------------------------------------------------------------

MAX_FILE_BYTES = 10 * 1024 * 1024     # 10 MB hard cap
MAX_ROWS = 5000


class SourceFile(BaseModel):
    """Metadata about the user-uploaded file. Content lives in source_prompts."""
    filename: str = Field(..., min_length=1, max_length=255)
    format: SourceFormat
    size_bytes: int = Field(..., ge=0, le=MAX_FILE_BYTES)
    row_count: int = Field(..., ge=1, le=MAX_ROWS)
    encoding: str = Field(default="utf-8", max_length=32)
    sheet_name: str | None = Field(default=None, max_length=64)   # xlsx only
    sample: list[dict] = Field(default_factory=list, max_length=5)
    uploaded_at: datetime = Field(default_factory=datetime.now)

    @field_validator("filename")
    @classmethod
    def _no_path_separators(cls, v: str) -> str:
        # Path-traversal guard — see §15 Security
        if "/" in v or "\\" in v or v.startswith("."):
            raise ValueError("filename must not contain path separators")
        return v


# ---------------------------------------------------------------------------
# SourcePrompt — one normalized row after parsing + mapping
# ---------------------------------------------------------------------------

MAX_PROMPT_TEXT_LEN = 2000


class SourcePrompt(BaseModel):
    """Single normalized prompt from the source file.

    `original_text` / `original_text_en` are filled after field-mapping;
    pre-mapping they may be empty and metadata holds all raw columns.
    """
    source_id: str = Field(..., min_length=1, max_length=128)
    original_text: str = Field(default="", max_length=MAX_PROMPT_TEXT_LEN)
    original_text_en: str | None = Field(default=None, max_length=MAX_PROMPT_TEXT_LEN)
    metadata: dict = Field(default_factory=dict)
    selected: bool = True

    # Set by R3 if this row's rewrite failed
    failed_to_rewrite: bool = False
    fail_reason: str | None = Field(default=None, max_length=512)

    @field_validator("original_text", "original_text_en")
    @classmethod
    def _strip(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return v.strip()


# ---------------------------------------------------------------------------
# Transform — one preset rewrite card
# ---------------------------------------------------------------------------

class Transform(BaseModel):
    """A single rewrite directive card chosen by the user.

    Card definitions live in `phases/rewrite_cards.py` (PR-2). This schema
    is only the user-selected instantiation: which card + params + order.
    """
    id: TransformId
    name_zh: str = Field(..., min_length=1, max_length=32)
    params: dict = Field(default_factory=dict)
    order: int = Field(..., ge=0, le=99)


# ---------------------------------------------------------------------------
# RewriteDirective — full intent (cards + free text + scope)
# ---------------------------------------------------------------------------

MAX_FREE_TEXT_LEN = 1000


class RewriteDirective(BaseModel):
    """Complete rewrite intent. At least one of transforms / free_text required."""
    transforms: list[Transform] = Field(default_factory=list, max_length=11)
    free_text: str = Field(default="", max_length=MAX_FREE_TEXT_LEN)
    target_capability: str | None = Field(default=None, max_length=64)   # capability_slug
    preserve_original: bool = True
    selected_source_ids: list[str] = Field(default_factory=list)         # empty = all

    @model_validator(mode="after")
    def _at_least_one_directive(self) -> "RewriteDirective":
        # Invariant: empty transforms AND empty free_text is meaningless.
        # UI also enforces this; backend rejects to be safe.
        if not self.transforms and not self.free_text.strip():
            raise ValueError("transforms 和 free_text 至少一项非空")
        return self

    @model_validator(mode="after")
    def _unique_transform_ids(self) -> "RewriteDirective":
        # Cards can't be applied twice in one batch (DIRECTIVE_CONFLICT in §11).
        ids = [t.id for t in self.transforms]
        if len(ids) != len(set(ids)):
            raise ValueError("同一张卡片不能选两次")
        return self


# ---------------------------------------------------------------------------
# FieldMapping — file columns → internal semantic keys
# ---------------------------------------------------------------------------

class FieldMapping(BaseModel):
    """User-confirmed mapping from raw file column names to semantic keys.

    `prompt_zh` and `prompt_en` are the only meaningful semantic keys;
    `source_id` is optional (defaults to row index). Everything else from
    the source file goes into SourcePrompt.metadata.
    """
    prompt_zh: str | None = Field(default=None, max_length=128)
    prompt_en: str | None = Field(default=None, max_length=128)
    source_id: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def _bilingual_at_least_one(self) -> "FieldMapping":
        # MAPPING_INVALID per §11 — at least one prompt column is required.
        if not self.prompt_zh and not self.prompt_en:
            raise ValueError("prompt_zh 和 prompt_en 至少映射一个")
        return self


# ---------------------------------------------------------------------------
# Parse error taxonomy (§11)
# ---------------------------------------------------------------------------

class ParseError(Exception):
    """All file-parsing failures raise this. Use `code` for routing in §11."""
    def __init__(self, code: str, message: str, location: str | None = None):
        self.code = code
        self.location = location
        super().__init__(f"[{code}] {message}" + (f" @ {location}" if location else ""))
