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
