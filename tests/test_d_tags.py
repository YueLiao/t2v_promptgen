"""手术 2 (audit upgrade #5): LLM emits 8-dim tags directly.

Coverage analysis used to rely on:
  - subject_type → D1 mapping
  - camera_zh substring match → D4 (fragile, needed a hand-curated denylist)
  - scene_l1 → D6 (dropped subject-driven L1s → silent undercount)

Now `PromptEntry.d_tags` is a first-class field. When set, coverage.py
uses it directly; when empty (older prompts), the legacy heuristics
still fire so reports keep working.
"""
from datetime import datetime

import pytest

from t2v_promptgen.core.schema import Phase, PromptEntry, Run
from t2v_promptgen.web.llm_phases import _validate_d_tags


# ---------------------------------------------------------------------------
# PromptEntry.d_tags field
# ---------------------------------------------------------------------------

def _pe(pid="p1", **kw) -> PromptEntry:
    return PromptEntry(
        id=pid, capability="cap", capability_version=1,
        difficulty="medium", difficulty_score=5.0,
        sl2_covered=[], axes_values={},
        subject_count=kw.pop("subject_count", 1),
        action_count=1,
        camera_zh=None, camera_en=None,
        prompt_zh="x", prompt_en="x",
        generated_at=datetime.now(),
        **kw,
    )


def test_promptentry_d_tags_defaults_empty():
    p = _pe()
    assert p.d_tags == {}


def test_promptentry_d_tags_round_trip():
    p1 = _pe(d_tags={"D1": ["S2"], "D4": ["C2"], "D6": ["E5"]})
    p2 = PromptEntry.model_validate_json(p1.model_dump_json())
    assert p2.d_tags == {"D1": ["S2"], "D4": ["C2"], "D6": ["E5"]}


# ---------------------------------------------------------------------------
# _validate_d_tags: parser robustness against malformed LLM output
# ---------------------------------------------------------------------------

def test_validate_d_tags_keeps_known_codes():
    out = _validate_d_tags({"D1": ["S1"], "D4": ["C2", "C4"]})
    assert out == {"D1": ["S1"], "D4": ["C2", "C4"]}


def test_validate_d_tags_drops_unknown_dims():
    out = _validate_d_tags({"D1": ["S1"], "Dx": ["?"], "garbage": ["S2"]})
    assert "D1" in out
    assert "Dx" not in out
    assert "garbage" not in out


def test_validate_d_tags_drops_unknown_codes():
    out = _validate_d_tags({"D1": ["S1", "S_GHOST"], "D4": ["NOT_A_CODE"]})
    assert out["D1"] == ["S1"]
    # D4 ended up empty → dropped from output entirely
    assert "D4" not in out


def test_validate_d_tags_rejects_non_dict():
    assert _validate_d_tags(None) == {}
    assert _validate_d_tags("string") == {}
    assert _validate_d_tags([1, 2, 3]) == {}


def test_validate_d_tags_rejects_non_list_values():
    out = _validate_d_tags({"D1": "S1", "D4": {"k": "v"}, "D6": ["E1"]})
    assert out == {"D6": ["E1"]}


def test_validate_d_tags_drops_non_string_codes():
    out = _validate_d_tags({"D1": ["S1", 42, None]})
    assert out == {"D1": ["S1"]}


# ---------------------------------------------------------------------------
# coverage.py uses d_tags when present
# ---------------------------------------------------------------------------

def _run_with(prompts) -> Run:
    now = datetime.now()
    r = Run(
        id="dtagrun", capability_slug="cap",
        created_at=now, updated_at=now,
        phase=Phase.P4_REVIEW,
    )
    r.prompts = prompts
    return r


def test_coverage_uses_d_tags_for_d1():
    """When d_tags is populated for D1, count from it directly — NOT from
    the subject_type heuristic."""
    from t2v_promptgen.core.coverage import build_coverage_report
    # subject_type would map to S3 (object), but d_tags says S5
    prompts = [_pe("p1", subject_type="object", d_tags={"D1": ["S5"]})]
    r = build_coverage_report(_run_with(prompts))
    d1 = next(d for d in r.dims if d.code == "D1")
    by_code = {v.code: v.hit_count for v in d1.values}
    assert by_code["S5"] == 1
    assert by_code["S3"] == 0


def test_coverage_falls_back_to_heuristic_when_d_tags_empty():
    """No d_tags → legacy subject_type → D1 heuristic still works
    (backward compat for prompts generated before this PR)."""
    from t2v_promptgen.core.coverage import build_coverage_report
    prompts = [_pe("p1", subject_type="human", subject_count=2)]   # → S2
    r = build_coverage_report(_run_with(prompts))
    d1 = next(d for d in r.dims if d.code == "D1")
    by_code = {v.code: v.hit_count for v in d1.values}
    assert by_code["S2"] == 1


def test_coverage_d_tags_enables_previously_uncountable_dims():
    """D2/D3/D5/D7/D8 had NO heuristic (planned-only). With d_tags they
    become countable and gaps are computed against actual hits."""
    from t2v_promptgen.core.coverage import build_coverage_report
    prompts = [
        _pe("p1", d_tags={"D2": ["A30"], "D7": ["Y1"]}),
        _pe("p2", d_tags={"D2": ["A30"], "D7": ["Y5"]}),
    ]
    r = build_coverage_report(_run_with(prompts))
    d2 = next(d for d in r.dims if d.code == "D2")
    d7 = next(d for d in r.dims if d.code == "D7")
    assert d2.countable is True
    assert d7.countable is True
    a30_count = next(v.hit_count for v in d2.values if v.code == "A30")
    assert a30_count == 2


def test_coverage_d6_no_longer_undercounts_subject_driven_l1():
    """Before 手术 2: scene_l1='人类活动场景' was dropped from D6 mapping →
    silent zero. With d_tags the LLM can explicitly say 'E5 室内现代' on
    a 人类活动场景 prompt and we count it."""
    from t2v_promptgen.core.coverage import build_coverage_report
    prompts = [
        _pe("p1", scene_l1="人类活动场景", d_tags={"D6": ["E5"]}),
        _pe("p2", scene_l1="人类活动场景", d_tags={"D6": ["E1"]}),
    ]
    r = build_coverage_report(_run_with(prompts))
    d6 = next(d for d in r.dims if d.code == "D6")
    by_code = {v.code: v.hit_count for v in d6.values}
    assert by_code["E5"] == 1
    assert by_code["E1"] == 1


def test_coverage_dim_uncountable_without_d_tags_or_heuristic():
    """No d_tags + no heuristic (D2/D3/D5/D7/D8) → dim stays uncountable
    so the UI doesn't show false-zero hit chips."""
    from t2v_promptgen.core.coverage import build_coverage_report
    prompts = [_pe("p1")]    # nothing at all
    r = build_coverage_report(_run_with(prompts))
    d2 = next(d for d in r.dims if d.code == "D2")
    assert d2.countable is False
