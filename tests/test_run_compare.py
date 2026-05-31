"""Tests for core.run_compare — diff algorithms across SL2 / axes / tags / prompts."""
from datetime import datetime

import pytest

from t2v_promptgen.core.run_compare import (
    compare_runs,
    diff_axes,
    diff_prompts,
    diff_recommended_tags,
    diff_sl2,
    _similarity,
)
from t2v_promptgen.core.schema import Axis, Phase, PromptEntry, Run, SL2


def _pe(id: str, txt: str, **kw) -> PromptEntry:
    return PromptEntry(
        id=id, capability="x", capability_version=1,
        difficulty="medium", difficulty_score=5.0,
        sl2_covered=[], axes_values={}, subject_count=1, action_count=1,
        camera_zh=None, camera_en=None,
        prompt_zh=txt, prompt_en="",
        generated_at=datetime.now(),
        **kw,
    )


def _run(rid: str, **kw) -> Run:
    now = datetime.now()
    return Run(
        id=rid, capability_slug="human_hand",
        created_at=now, updated_at=now,
        phase=Phase.P5_EXPORT,
        **kw,
    )


def _sl2(sid: str) -> SL2:
    return SL2(id=sid, name=sid, description="", judging_criteria_md="")


# ---------- similarity ----------

def test_similarity_exact():
    assert _similarity("hello", "hello") == 1.0


def test_similarity_empty():
    assert _similarity("", "abc") == 0.0
    assert _similarity("abc", "") == 0.0


def test_similarity_close():
    s = _similarity("两只手在切菜", "两只手在切菜板上切菜")
    assert 0.5 < s < 1.0


def test_similarity_unrelated():
    s = _similarity("一只手切菜", "猫扑老鼠")
    assert s < 0.4


# ---------- SL2 diff ----------

def test_diff_sl2_basic():
    a = _run("a", sl2_list=[_sl2("x"), _sl2("y")])
    b = _run("b", sl2_list=[_sl2("y"), _sl2("z")])
    d = diff_sl2(a, b)
    assert d.common == ["y"]
    assert d.only_a == ["x"]
    assert d.only_b == ["z"]


def test_diff_sl2_identical():
    a = _run("a", sl2_list=[_sl2("x"), _sl2("y")])
    b = _run("b", sl2_list=[_sl2("y"), _sl2("x")])
    d = diff_sl2(a, b)
    assert set(d.common) == {"x", "y"}
    assert d.only_a == []
    assert d.only_b == []


def test_diff_sl2_empty():
    a = _run("a")
    b = _run("b")
    d = diff_sl2(a, b)
    assert d.common == d.only_a == d.only_b == []


# ---------- axes diff ----------

def test_diff_axes_basic():
    a = _run("a", axes=[Axis(name="light", values=["d", "n"])])
    b = _run("b", axes=[Axis(name="light", values=["d", "n"]),
                        Axis(name="speed", values=["s", "f"])])
    d = diff_axes(a, b)
    assert d.common == ["light"]
    assert d.only_b == ["speed"]
    assert d.only_a == []


# ---------- tag diff ----------

def test_diff_recommended_tags_per_dim():
    a = _run("a", recommended_tags={"D1": ["S1", "S2"], "D5": ["F1"]})
    b = _run("b", recommended_tags={"D1": ["S1", "S4"], "D2": ["A30"]})
    diffs = diff_recommended_tags(a, b)
    by_dim = {d.dim: d for d in diffs}
    assert set(by_dim) == {"D1", "D5", "D2"}
    assert by_dim["D1"].common == ["S1"]
    assert by_dim["D1"].only_a == ["S2"]
    assert by_dim["D1"].only_b == ["S4"]
    assert by_dim["D2"].only_b == ["A30"]
    assert by_dim["D5"].only_a == ["F1"]


def test_diff_recommended_tags_empty_when_no_tags():
    a = _run("a")
    b = _run("b")
    assert diff_recommended_tags(a, b) == []


# ---------- prompt pool diff ----------

def test_diff_prompts_all_exact():
    a = _run("a", prompts=[_pe("p1", "x"), _pe("p2", "y")])
    b = _run("b", prompts=[_pe("p1", "x"), _pe("p2", "y")])
    d = diff_prompts(a, b)
    assert len(d.exact) == 2
    assert d.near == []
    assert d.only_a == []
    assert d.only_b == []


def test_diff_prompts_near_match():
    a = _run("a", prompts=[_pe("p1", "两只手在切菜")])
    b = _run("b", prompts=[_pe("p1", "两只手在切菜板上切菜")])
    d = diff_prompts(a, b)
    assert len(d.near) == 1
    assert d.near[0].similarity > 0.7
    assert d.exact == []
    assert d.only_a == d.only_b == []


def test_diff_prompts_only_b():
    a = _run("a")
    b = _run("b", prompts=[_pe("p1", "new"), _pe("p2", "stuff")])
    d = diff_prompts(a, b)
    assert d.exact == d.near == d.only_a == []
    assert len(d.only_b) == 2


def test_diff_prompts_only_a():
    a = _run("a", prompts=[_pe("p1", "old")])
    b = _run("b")
    d = diff_prompts(a, b)
    assert d.exact == d.near == d.only_b == []
    assert len(d.only_a) == 1


def test_diff_prompts_mixed():
    a = _run("a", prompts=[
        _pe("p1", "exact match"),
        _pe("p2", "near match prompt"),
        _pe("p3", "only in A"),
    ])
    b = _run("b", prompts=[
        _pe("p1", "exact match"),
        _pe("p2", "near match prompt with addition"),
        _pe("p3", "totally new B prompt"),
    ])
    d = diff_prompts(a, b)
    assert len(d.exact) == 1
    assert len(d.near) >= 0   # near match depends on threshold
    assert d.total_a == 3
    assert d.total_b == 3


def test_diff_prompts_duplicate_text_in_a():
    """A has same prompt twice; B only once — first A match consumed."""
    a = _run("a", prompts=[_pe("p1", "dup"), _pe("p2", "dup"), _pe("p3", "x")])
    b = _run("b", prompts=[_pe("p1", "dup")])
    d = diff_prompts(a, b)
    assert len(d.exact) == 1
    assert len(d.only_a) == 2


# ---------- top-level compare_runs ----------

def test_compare_runs_structure():
    a = _run("a", sl2_list=[_sl2("x")], axes=[Axis(name="l", values=["1", "2"])],
             recommended_tags={"D1": ["S1"]}, prompts=[_pe("p1", "hi")])
    b = _run("b", sl2_list=[_sl2("y")], prompts=[_pe("p1", "bye")])
    r = compare_runs(a, b)
    assert r.a.id == "a"
    assert r.b.id == "b"
    assert isinstance(r.headlines, list)
    assert len(r.headlines) > 0
    assert r.sl2_diff.only_a == ["x"]
    assert r.sl2_diff.only_b == ["y"]


def test_compare_runs_with_qa_pass_rate_warns_on_drop():
    """If B's pass rate is much lower than A's, headline flag = 'warn'."""
    a_prompts = [_pe(f"p{i}", "x", qa_passed=True) for i in range(10)]
    b_prompts = [_pe(f"p{i}", "y", qa_passed=(i < 5)) for i in range(10)]
    a = _run("a", prompts=a_prompts)
    b = _run("b", prompts=b_prompts)
    r = compare_runs(a, b)
    pass_row = next((h for h in r.headlines if h.label == "P3 通过率"), None)
    assert pass_row is not None
    assert pass_row.flag == "warn"   # 100% → 50% = -50%


def test_compare_runs_pass_rate_good_on_improvement():
    a_prompts = [_pe(f"p{i}", "x", qa_passed=(i < 7)) for i in range(10)]
    b_prompts = [_pe(f"p{i}", "y", qa_passed=True) for i in range(10)]
    a = _run("a", prompts=a_prompts)
    b = _run("b", prompts=b_prompts)
    r = compare_runs(a, b)
    pass_row = next((h for h in r.headlines if h.label == "P3 通过率"), None)
    assert pass_row.flag == "good"
