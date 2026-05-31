"""Tests for the server-side pre-assignment (phases/rewrite_assign.py)."""
from collections import Counter

import pytest

from t2v_promptgen.core.rewrite_schema import RewriteDirective, Transform
from t2v_promptgen.phases.rewrite_assign import (
    derive_seed,
    pre_assign,
    summarize_assignments,
    _round_robin_spread,
    _resolve_targets,
)
from t2v_promptgen.phases.rewrite_cards import card_for


def _scene_directive(target_value, preserve="是") -> RewriteDirective:
    card = card_for("scene_shift")
    return RewriteDirective(transforms=[Transform(
        id="scene_shift", name_zh=card.name_zh,
        params={"target_scene": target_value, "preserve_action": preserve},
        order=0,
    )], free_text="—")


# ---------------------------------------------------------------------------
# _resolve_targets
# ---------------------------------------------------------------------------

def test_resolve_targets_all_sentinel_expands():
    opts = ["A", "B", "C"]
    assert _resolve_targets(["__all__"], opts) == ["A", "B", "C"]


def test_resolve_targets_empty_treated_as_all():
    opts = ["A", "B"]
    assert _resolve_targets([], opts) == ["A", "B"]


def test_resolve_targets_subset_filtered():
    opts = ["A", "B", "C", "D"]
    assert _resolve_targets(["B", "D", "Z"], opts) == ["B", "D"]


def test_resolve_targets_string_normalized():
    assert _resolve_targets("A", ["A", "B"]) == ["A"]


def test_resolve_targets_invalid_subset_falls_back_all():
    """All picks are unknown → return all options rather than empty."""
    assert _resolve_targets(["nope"], ["A", "B"]) == ["A", "B"]


# ---------------------------------------------------------------------------
# _round_robin_spread distributional properties
# ---------------------------------------------------------------------------

def test_round_robin_exact_division():
    import random
    picks = _round_robin_spread(["A", "B", "C"], 9, random.Random(0))
    c = Counter(picks)
    assert c == Counter({"A": 3, "B": 3, "C": 3})


def test_round_robin_uneven_division_off_by_one():
    """N=7, m=3 → counts must be 2 or 3 per target."""
    import random
    picks = _round_robin_spread(["A", "B", "C"], 7, random.Random(0))
    c = Counter(picks)
    assert sum(c.values()) == 7
    assert max(c.values()) - min(c.values()) <= 1


def test_round_robin_n_smaller_than_m():
    """N=2, m=5 → only 2 targets get picked, no duplicates."""
    import random
    picks = _round_robin_spread(["A", "B", "C", "D", "E"], 2, random.Random(0))
    assert len(picks) == 2
    assert len(set(picks)) == 2


def test_round_robin_deterministic_under_seed():
    import random
    p1 = _round_robin_spread(["A", "B", "C"], 6, random.Random(42))
    p2 = _round_robin_spread(["A", "B", "C"], 6, random.Random(42))
    assert p1 == p2


def test_round_robin_empty_inputs():
    import random
    assert _round_robin_spread([], 5, random.Random(0)) == []
    assert _round_robin_spread(["A"], 0, random.Random(0)) == []


# ---------------------------------------------------------------------------
# pre_assign end-to-end
# ---------------------------------------------------------------------------

def test_pre_assign_default_all_spreads_uniformly():
    """scene_shift with default ["__all__"] → 18 E# targets,
    9 prompts → each of 9 distinct E# values picked once (or twice)."""
    directive = _scene_directive(["__all__"])
    source_ids = [f"src_{i}" for i in range(9)]
    assignments = pre_assign(source_ids, directive, seed=42)
    picks = [a["scene_shift"]["target_scene"] for a in assignments.values()]
    # 9 picks across 18 candidates: every pick must be unique
    assert len(set(picks)) == 9
    # Determinism
    assignments2 = pre_assign(source_ids, directive, seed=42)
    assert assignments == assignments2


def test_pre_assign_subset_balanced():
    """3 picks × 12 prompts → exactly 4 each."""
    directive = _scene_directive(["E1 室外自然", "E5 室内现代", "E8 奇幻虚拟"])
    source_ids = [f"src_{i}" for i in range(12)]
    assignments = pre_assign(source_ids, directive, seed=42)
    counts = Counter(a["scene_shift"]["target_scene"] for a in assignments.values())
    assert counts == Counter({"E1 室外自然": 4, "E5 室内现代": 4, "E8 奇幻虚拟": 4})


def test_pre_assign_single_pick_omits_assignment():
    """User pinned ONE target → no per-prompt assignment needed (the rendered
    fragment already pins it). Saves payload bytes + tokens."""
    directive = _scene_directive(["E1 室外自然"])
    source_ids = [f"src_{i}" for i in range(5)]
    assignments = pre_assign(source_ids, directive, seed=42)
    # All entries empty — single-pick is handled by fragment text, not assigned
    for sid, a in assignments.items():
        assert a == {}


def test_pre_assign_no_directive_returns_empty_buckets():
    source_ids = ["a", "b"]
    assignments = pre_assign(source_ids, None, seed=0)
    assert assignments == {"a": {}, "b": {}}


def test_pre_assign_preserves_source_id_order():
    """Output dict iteration order must follow input source_ids order."""
    directive = _scene_directive(["__all__"])
    source_ids = ["z", "a", "m", "b"]
    assignments = pre_assign(source_ids, directive, seed=1)
    assert list(assignments.keys()) == source_ids


def test_pre_assign_seed_changes_distribution():
    """Different seeds → different per-prompt assignments
    (but same TOTAL counts, since balanced)."""
    directive = _scene_directive(["E1 室外自然", "E5 室内现代"])
    sids = [f"s{i}" for i in range(8)]
    a1 = pre_assign(sids, directive, seed=1)
    a2 = pre_assign(sids, directive, seed=2)
    # At least one per-prompt assignment must differ
    diffs = [a1[s] != a2[s] for s in sids]
    assert any(diffs)
    # But total per-target counts stay balanced (4 each in both)
    c1 = Counter(a1[s]["scene_shift"]["target_scene"] for s in sids)
    c2 = Counter(a2[s]["scene_shift"]["target_scene"] for s in sids)
    assert c1 == c2 == Counter({"E1 室外自然": 4, "E5 室内现代": 4})


def test_pre_assign_multiple_cards_independent():
    """scene_shift + style_apply both multi → both assigned independently."""
    card_a = card_for("scene_shift")
    card_b = card_for("style_apply")
    directive = RewriteDirective(transforms=[
        Transform(id="scene_shift", name_zh=card_a.name_zh,
                  params={"target_scene": ["__all__"], "preserve_action": "是"},
                  order=0),
        Transform(id="style_apply", name_zh=card_b.name_zh,
                  params={"target_style": ["Y1 写实电影", "Y5 古风", "Y20 写实/无风格化"]},
                  order=1),
    ], free_text="—")
    sids = [f"s{i}" for i in range(9)]
    assignments = pre_assign(sids, directive, seed=42)
    for sid in sids:
        a = assignments[sid]
        assert "scene_shift" in a
        assert "style_apply" in a
        assert a["scene_shift"]["target_scene"]
        assert a["style_apply"]["target_style"] in ("Y1 写实电影", "Y5 古风", "Y20 写实/无风格化")
    # Style counts balanced 3/3/3
    sc = Counter(assignments[s]["style_apply"]["target_style"] for s in sids)
    assert sc == Counter({"Y1 写实电影": 3, "Y5 古风": 3, "Y20 写实/无风格化": 3})


def test_pre_assign_unknown_card_ignored():
    """Stale card id in transforms (e.g. removed card) is silently skipped."""
    directive = RewriteDirective(transforms=[
        Transform(id="scene_shift", name_zh="x",
                  params={"target_scene": ["E1 室外自然", "E5 室内现代"], "preserve_action": "是"},
                  order=0),
    ], free_text="—")
    # Inject a fake card id
    directive.transforms.append(Transform(
        id="action_chain_extend",   # use a real id but treat as if missing
        name_zh="y", params={}, order=1,
    ))
    sids = ["a", "b", "c", "d"]
    # Should not raise; action_chain_extend's multi_enum gets default __all__
    assignments = pre_assign(sids, directive, seed=0)
    for a in assignments.values():
        assert "scene_shift" in a


# ---------------------------------------------------------------------------
# summarize_assignments
# ---------------------------------------------------------------------------

def test_summarize_assignments_counts():
    directive = _scene_directive(["E1 室外自然", "E5 室内现代", "E8 奇幻虚拟"])
    sids = [f"s{i}" for i in range(6)]
    assignments = pre_assign(sids, directive, seed=0)
    summary = summarize_assignments(assignments)
    counts = summary["scene_shift"]["target_scene"]
    assert sum(counts.values()) == 6
    assert set(counts.keys()) <= {"E1 室外自然", "E5 室内现代", "E8 奇幻虚拟"}


def test_summarize_assignments_empty():
    assert summarize_assignments({}) == {}
    assert summarize_assignments({"a": {}, "b": {}}) == {}


# ---------------------------------------------------------------------------
# derive_seed
# ---------------------------------------------------------------------------

def test_derive_seed_stable_for_run_id():
    assert derive_seed("abc123") == derive_seed("abc123")


def test_derive_seed_different_for_different_runs():
    assert derive_seed("abc123") != derive_seed("xyz789")


def test_derive_seed_salt_changes_value():
    """The 🎲 reroll button bumps salt to get a new spread."""
    assert derive_seed("abc", salt=0) != derive_seed("abc", salt=1)


# ---------------------------------------------------------------------------
# Review-round regressions
# ---------------------------------------------------------------------------

def test_iterate_rewrite_uses_pre_assign_on_subset():
    """iterate_rewrite calls rewrite_run with a scoped directive
    (selected_source_ids = rejected). rewrite_run then calls pre_assign
    only over that subset, NOT over the full pool. We verify by mocking
    rewrite_prompts_real and inspecting the assignments kwarg it receives.

    Catches a future regression where someone refactors pre_assign to
    use the full pool even during iterate.
    """
    from datetime import datetime
    from unittest.mock import patch
    from t2v_promptgen.core.rewrite_schema import SourcePrompt
    from t2v_promptgen.core.schema import Run, Phase
    from t2v_promptgen.phases.rewrite import iterate_rewrite

    # Build a rewrite run with 6 source prompts
    now = datetime.now()
    run = Run(
        id="iter1", capability_slug="custom_rewrite",
        capability_display_name="测试改写",
        created_at=now, updated_at=now,
        phase=Phase.P4_REVIEW, source="rewrite",
        rewrite_seed=99,
    )
    run.source_prompts = [
        SourcePrompt(source_id=f"s{i}", original_text=f"原 {i}",
                      selected=True, failed_to_rewrite=False)
        for i in range(6)
    ]
    run.rewrite_directive = _scene_directive(["E1 室外自然", "E5 室内现代"])

    captured = {}
    def fake_rewrite(source_prompts, directive, client, temperature=0.4,
                     assignments=None):
        captured["sids"] = [sp.source_id for sp in source_prompts]
        captured["assignments"] = assignments
        return [], []   # no entries returned — keep test focused

    with patch("t2v_promptgen.web.llm_phases.rewrite_prompts_real",
                side_effect=fake_rewrite):
        iterate_rewrite(run, ["s1", "s3", "s5"], "", client=object())

    # Should have been called with only the 3 rejected ids
    assert set(captured["sids"]) == {"s1", "s3", "s5"}
    # And assignments dict should only cover those 3 sids
    assert set(captured["assignments"].keys()) == {"s1", "s3", "s5"}
    # Each has a target_scene from the [E1, E5] subset
    for sid in ("s1", "s3", "s5"):
        a = captured["assignments"][sid]
        assert a["scene_shift"]["target_scene"] in ("E1 室外自然", "E5 室内现代")


def test_summarize_assignments_targets_sorted_lex():
    """Display ordering: bucket keys appear in lexicographic target order.
    Locks the contract so a future dict-ordering refactor surfaces here."""
    fake = {
        "s1": {"c": {"k": "Z 第三"}},
        "s2": {"c": {"k": "A 第一"}},
        "s3": {"c": {"k": "M 第二"}},
    }
    summary = summarize_assignments(fake)
    keys = list(summary["c"]["k"].keys())
    assert keys == sorted(keys)
    assert keys[0] == "A 第一"


def test_run_rewrite_seed_persists_through_jsondump():
    """Pydantic round-trip preserves the new rewrite_seed field, so old
    DB rows (where seed is missing) load as None — and explicitly-set
    seeds survive save/load."""
    from datetime import datetime
    from t2v_promptgen.core.schema import Run, Phase
    r1 = Run(
        id="seedtest1", capability_slug="x",
        created_at=datetime.now(), updated_at=datetime.now(),
        phase=Phase.P1_DIMENSIONS,
        rewrite_seed=12345,
    )
    j = r1.model_dump_json()
    r2 = Run.model_validate_json(j)
    assert r2.rewrite_seed == 12345

    # Missing field path: simulate an older DB row by stripping the key
    import json
    d = json.loads(j)
    d.pop("rewrite_seed", None)
    r3 = Run.model_validate_json(json.dumps(d))
    assert r3.rewrite_seed is None
