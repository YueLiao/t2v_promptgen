"""Run schema invariants — `source` field exclusivity (P1-2 from audit).

A single Run model carries fields for both generate and rewrite flows.
Without enforcement, clone_run / goto_phase / direct construction can
silently produce a structurally invalid Run (e.g. a generate run with a
rewrite_directive set). The model_validator on Run blocks these at
construction time so the bug class can't recur.
"""
from datetime import datetime

import pytest
from pydantic import ValidationError

from t2v_promptgen.core.rewrite_schema import RewriteDirective, SourceFile, SourcePrompt
from t2v_promptgen.core.schema import Phase, Run


def _base_kwargs(**over):
    """Minimal valid Run kwargs (generate-mode defaults)."""
    now = datetime.now()
    return {
        "id": "test1234",
        "capability_slug": "x",
        "created_at": now,
        "updated_at": now,
        "phase": Phase.P1_DIMENSIONS,
        **over,
    }


# ---------------------------------------------------------------------------
# Generate-mode exclusivity
# ---------------------------------------------------------------------------

def test_generate_run_constructs_cleanly():
    r = Run(**_base_kwargs())
    assert r.is_generate
    assert not r.is_rewrite


def test_generate_run_rejects_rewrite_directive():
    with pytest.raises(ValidationError, match="rewrite-only"):
        Run(**_base_kwargs(
            rewrite_directive=RewriteDirective(transforms=[], free_text="x"),
        ))


def test_generate_run_rejects_source_file():
    sf = SourceFile(filename="x.csv", format="csv", size_bytes=10,
                    row_count=1, columns=["text"], detected_encoding="utf-8")
    with pytest.raises(ValidationError, match="rewrite-only"):
        Run(**_base_kwargs(source_file=sf))


def test_generate_run_rejects_source_prompts():
    sp = SourcePrompt(source_id="s1", original_text="x")
    with pytest.raises(ValidationError, match="rewrite-only"):
        Run(**_base_kwargs(source_prompts=[sp]))


def test_generate_run_rejects_rewrite_seed():
    with pytest.raises(ValidationError, match="rewrite-only"):
        Run(**_base_kwargs(rewrite_seed=42))


def test_generate_run_rejects_field_mapping():
    with pytest.raises(ValidationError, match="rewrite-only"):
        Run(**_base_kwargs(field_mapping={"prompt": "text"}))


# ---------------------------------------------------------------------------
# Rewrite-mode exclusivity
# ---------------------------------------------------------------------------

def test_rewrite_run_constructs_cleanly():
    r = Run(**_base_kwargs(source="rewrite"))
    assert r.is_rewrite
    assert not r.is_generate


def test_rewrite_run_with_rewrite_fields_ok():
    sf = SourceFile(filename="x.csv", format="csv", size_bytes=20,
                    row_count=2, columns=["text"], detected_encoding="utf-8")
    sp = [SourcePrompt(source_id="s1", original_text="x")]
    r = Run(**_base_kwargs(
        source="rewrite",
        source_file=sf,
        source_prompts=sp,
        field_mapping={"text": "prompt"},
        rewrite_seed=7,
    ))
    assert r.is_rewrite
    assert r.source_file == sf
    assert r.rewrite_seed == 7


def test_rewrite_run_rejects_recommended_tags():
    """Rewrite mode has no P1 design phase → tag-recommendation state must be empty."""
    with pytest.raises(ValidationError, match="P1 tag-recommendation"):
        Run(**_base_kwargs(
            source="rewrite",
            recommended_tags={"D1": ["S1"]},
        ))


def test_rewrite_run_rejects_custom_tags():
    with pytest.raises(ValidationError, match="P1 tag-recommendation"):
        Run(**_base_kwargs(
            source="rewrite",
            custom_tags={"D1": [{"code": "X1", "name_zh": "test"}]},
        ))


# ---------------------------------------------------------------------------
# Properties + round-trip
# ---------------------------------------------------------------------------

def test_is_generate_is_rewrite_mutually_exclusive():
    g = Run(**_base_kwargs())
    r = Run(**_base_kwargs(source="rewrite"))
    assert g.is_generate and not g.is_rewrite
    assert r.is_rewrite and not r.is_generate


def test_validator_fires_on_json_roundtrip():
    """A malicious / corrupted DB row trying to load a generate+rewrite
    mix must still be rejected on model_validate_json."""
    import json
    bad = {
        "id": "bad12345",
        "capability_slug": "x",
        "capability_display_name": None,
        "rewrite_seed": None,
        "created_at": datetime.now().isoformat(),
        "updated_at": datetime.now().isoformat(),
        "phase": "P1_DIMENSIONS",
        "source": "generate",
        "rewrite_directive": {"transforms": [], "free_text": "leaked",
                                "target_capability": None, "preserve_original": True,
                                "selected_source_ids": []},
    }
    with pytest.raises(ValidationError, match="rewrite-only"):
        Run.model_validate_json(json.dumps(bad))


def test_clean_generate_round_trip():
    r1 = Run(**_base_kwargs(capability_display_name="测试"))
    r2 = Run.model_validate_json(r1.model_dump_json())
    assert r2.source == "generate"
    assert r2.capability_display_name == "测试"
