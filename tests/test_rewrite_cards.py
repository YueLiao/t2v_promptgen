"""Tests for phases.rewrite_cards — card definitions + rendering."""
import typing

import pytest

from t2v_promptgen.core.rewrite_schema import Transform, TransformId
from t2v_promptgen.phases.rewrite_cards import (
    ALL_CARDS, CARDS_BY_ID, GROUPS,
    card_for, cards_in_group, cards_to_ui_dict, render_card,
)


def test_card_count():
    assert len(ALL_CARDS) == 12


def test_group_counts():
    assert len(cards_in_group("主体类")) == 3
    assert len(cards_in_group("场景类")) == 3
    assert len(cards_in_group("时序类")) == 3
    assert len(cards_in_group("动作类")) == 2
    assert len(cards_in_group("难度类")) == 1


def test_unique_ids():
    ids = [c.id for c in ALL_CARDS]
    assert len(ids) == len(set(ids))


def test_every_id_is_valid_transform_id():
    valid = set(typing.get_args(TransformId))
    for c in ALL_CARDS:
        assert c.id in valid, f"{c.id} not a valid TransformId"


def test_every_id_can_become_a_transform():
    for c in ALL_CARDS:
        t = Transform(id=c.id, name_zh=c.name_zh, order=0)
        assert t.id == c.id


def test_render_with_defaults():
    """Every card must render cleanly with default params."""
    for c in ALL_CARDS:
        params = {p.key: p.default for p in c.params}
        text = render_card(c, params)
        assert text  # non-empty
        assert "{" not in text  # all placeholders resolved
        assert "}" not in text


def test_render_with_user_params():
    spec = card_for("add_temporal")
    text = render_card(spec, {"segments": "4 段以上"})
    assert "4 段以上" in text


def test_render_ignores_unknown_keys():
    spec = card_for("subject_swap")
    text = render_card(spec, {"from_type": "S1 单人", "to_type": "S5 多主体", "noise": "garbage"})
    assert "S1 单人" in text and "S5 多主体" in text


def test_render_missing_keys_use_default():
    spec = card_for("add_temporal")
    text = render_card(spec, {})  # no params at all
    # Should use default value (3 段)
    assert "3 段" in text


def test_card_for_unknown_returns_none():
    assert card_for("nonexistent_id") is None


def test_ui_dict_structure():
    ui = cards_to_ui_dict()
    assert "groups" in ui
    assert len(ui["groups"]) == len(GROUPS)
    for g in ui["groups"]:
        assert "name" in g
        assert "cards" in g
        for c in g["cards"]:
            assert "id" in c
            assert "name_zh" in c
            assert "params" in c


def test_new_temporal_cards_exist():
    """Sanity: the new temporal + action cards user asked for."""
    for new_id in ("add_temporal", "add_causal_chain", "add_irreversibility",
                   "action_chain_extend", "add_interaction", "add_micro_action"):
        assert new_id in CARDS_BY_ID, f"missing new card {new_id}"


# ---------------------------------------------------------------------------
# multi_enum: spread across multiple targets instead of pinning to one
# ---------------------------------------------------------------------------

def test_multi_enum_default_expands_to_all_options():
    """scene_shift default is ['__all__'] — rendered text must enumerate all
    18 E# options + tell the LLM to spread uniformly across the batch."""
    spec = card_for("scene_shift")
    # find the multi_enum param
    target_spec = next(p for p in spec.params if p.key == "target_scene")
    assert target_spec.type == "multi_enum"
    assert target_spec.default == ["__all__"]
    text = render_card(spec, {"target_scene": ["__all__"], "preserve_action": "是"})
    # All 18 E options must appear in the rendered fragment
    for opt in target_spec.options:
        assert opt in text, f"option {opt!r} missing from rendered fragment"
    # The instructional language for spread must be present
    assert "均匀随机" in text
    assert "整批" in text


def test_multi_enum_subset_renders_only_subset():
    """User picks a subset of E# values — render only those, not all."""
    spec = card_for("scene_shift")
    text = render_card(spec, {
        "target_scene": ["E1 室外自然", "E5 室内现代", "E8 奇幻虚拟"],
        "preserve_action": "是",
    })
    assert "E1 室外自然" in text
    assert "E5 室内现代" in text
    assert "E8 奇幻虚拟" in text
    # E2 should NOT appear (it's a valid option but not picked)
    assert "E2 室外城市" not in text
    assert "均匀随机" in text


def test_multi_enum_single_pick_renders_as_single():
    """If only one option is picked, no '随机挑一个' wording needed."""
    spec = card_for("scene_shift")
    text = render_card(spec, {
        "target_scene": ["E1 室外自然"],
        "preserve_action": "是",
    })
    assert "[E1 室外自然]" in text
    # Single pick: no spread instruction needed
    assert "均匀随机" not in text


def test_multi_enum_empty_list_treated_as_all():
    """Forgiving UX: empty selection = use all options."""
    spec = card_for("style_apply")
    text = render_card(spec, {"target_style": []})
    target_spec = next(p for p in spec.params if p.key == "target_style")
    # All 20 Y options should appear
    for opt in target_spec.options:
        assert opt in text


def test_multi_enum_unknown_options_filtered_out():
    """Stale UI state could send options that no longer exist; render skips them."""
    spec = card_for("style_apply")
    text = render_card(spec, {
        "target_style": ["Y1 写实电影", "Y_GHOST", "Y5 古风"],
    })
    assert "Y1 写实电影" in text
    assert "Y5 古风" in text
    assert "Y_GHOST" not in text


def test_multi_enum_string_value_normalized_to_list():
    """Backward-compat: old saved directives might have a plain string."""
    spec = card_for("camera_set")
    text = render_card(spec, {"target_camera": "C2 推"})
    assert "[C2 推]" in text


def test_all_target_cards_now_default_multi():
    """The user-facing 'set target' cards must spread by default."""
    expected_multi_keys = {
        "add_interaction": "target",
        "add_micro_action": "focus",
        "scene_shift": "target_scene",
        "style_apply": "target_style",
        "camera_set": "target_camera",
        "add_temporal": "segments",
        "add_causal_chain": "chain_depth",
        "add_irreversibility": "type",
        "action_chain_extend": "n_actions",
        "speed_adjust": "target_speed",
        "difficulty_up": "level",
    }
    for card_id, key in expected_multi_keys.items():
        spec = card_for(card_id)
        param = next((p for p in spec.params if p.key == key), None)
        assert param is not None, f"{card_id}.{key} not found"
        assert param.type == "multi_enum", \
            f"{card_id}.{key} should be multi_enum, got {param.type}"
        assert param.default == ["__all__"], \
            f"{card_id}.{key} default should be ['__all__'], got {param.default!r}"


def test_subject_swap_remains_single_enum():
    """Pair semantics — from/to swap stays as explicit single picks."""
    spec = card_for("subject_swap")
    for p in spec.params:
        assert p.type == "enum", f"{p.key} should stay single enum"


def test_ui_dict_serializes_multi_enum():
    """cards_to_ui_dict must expose multi_enum type so the front-end can
    render the chip picker (not a select)."""
    ui = cards_to_ui_dict()
    scene = next(c for g in ui["groups"] for c in g["cards"] if c["id"] == "scene_shift")
    target = next(p for p in scene["params"] if p["key"] == "target_scene")
    assert target["type"] == "multi_enum"
    assert target["default"] == ["__all__"]
    assert len(target["options"]) == 18
