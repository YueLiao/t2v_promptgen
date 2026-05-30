"""Tests for parsers.field_mapper — heuristic + LLM-mock paths."""
import pytest

from t2v_promptgen.parsers.field_mapper import heuristic_guess, llm_guess


# ---------- Standard patterns ----------

def test_prompt_column_english():
    m = heuristic_guess(
        ["id", "prompt"],
        [{"id": 1, "prompt": "hello world"}, {"id": 2, "prompt": "foo bar"}],
    )
    assert m.source_id == "id"
    assert m.prompt_en == "prompt"
    assert m.prompt_zh is None


def test_prompt_column_chinese():
    m = heuristic_guess(
        ["id", "prompt"],
        [{"id": 1, "prompt": "你好世界"}, {"id": 2, "prompt": "再见"}],
    )
    assert m.prompt_zh == "prompt"
    assert m.prompt_en is None


def test_description_column():
    m = heuristic_guess(
        ["id", "description"],
        [{"id": "p1", "description": "A person walks"}],
    )
    assert m.prompt_en == "description"


def test_caption_column():
    m = heuristic_guess(
        ["caption"],
        [{"caption": "a hand"}],
    )
    assert m.prompt_en == "caption"


def test_bilingual_caption_columns():
    m = heuristic_guess(
        ["rowIdx", "caption", "caption_en"],
        [{"rowIdx": 1, "caption": "一只手", "caption_en": "a hand"}],
    )
    assert m.prompt_zh == "caption"
    assert m.prompt_en == "caption_en"
    assert m.source_id == "rowIdx"


def test_zh_en_suffix_columns():
    m = heuristic_guess(
        ["uid", "text_zh", "text_en"],
        [{"uid": "a", "text_zh": "你好", "text_en": "hi"}],
    )
    assert m.prompt_zh == "text_zh"
    assert m.prompt_en == "text_en"


def test_explicit_chinese_keyword():
    m = heuristic_guess(
        ["id", "中文prompt", "english"],
        [{"id": 1, "中文prompt": "你好", "english": "hello"}],
    )
    assert m.prompt_zh == "中文prompt"
    assert m.prompt_en == "english"


# ---------- ID detection ----------

def test_id_column_variants():
    for id_col in ("id", "ID", "idx", "index", "row_id", "uid"):
        m = heuristic_guess(
            [id_col, "prompt"],
            [{id_col: 1, "prompt": "x"}],
        )
        assert m.source_id == id_col, f"failed on {id_col!r}"


# ---------- Fallback when no obvious prompt column ----------

def test_unknown_columns_long_string_fallback():
    m = heuristic_guess(
        ["col_1", "col_2"],
        [{"col_1": 1, "col_2": "this is a much longer string with real content"}],
    )
    # col_2 has long content → wins as prompt_en (ASCII)
    assert m.prompt_en == "col_2"


def test_short_metadata_only_no_prompt():
    """All columns are short metadata-like — no prompt assigned."""
    m = heuristic_guess(
        ["id", "author", "date"],
        [{"id": 1, "author": "a", "date": "2020"}, {"id": 2, "author": "b", "date": "2021"}],
    )
    assert m.prompt_zh is None
    assert m.prompt_en is None
    assert m.source_id == "id"


# ---------- LLM path ----------

class _FakeClient:
    """Mock LLM client returning a canned mapping."""
    def __init__(self, response: dict, raise_exc: Exception | None = None):
        self.response = response
        self.raise_exc = raise_exc

    def generate(self, **kwargs):
        if self.raise_exc:
            raise self.raise_exc
        # Match the openai_compat client API roughly
        return type("R", (), {"content": self.response})


def test_llm_guess_no_client_falls_back():
    m, why = llm_guess(
        ["id", "prompt"],
        [{"id": 1, "prompt": "hi"}],
        client=None,
    )
    assert m.prompt_en == "prompt"
    assert "无 LLM" in why


def test_llm_guess_returns_mapping():
    client = _FakeClient({
        "mapping": {"prompt_zh": "desc_zh", "prompt_en": "desc_en", "source_id": "id"},
        "confidence": "high",
        "reasoning": "中文列以 _zh 结尾",
    })
    m, why = llm_guess(
        ["id", "desc_zh", "desc_en"],
        [{"id": 1, "desc_zh": "你好", "desc_en": "hi"}],
        client=client,
    )
    assert m.prompt_zh == "desc_zh"
    assert m.prompt_en == "desc_en"
    assert m.source_id == "id"
    assert "中文" in why


def test_llm_guess_invalid_column_falls_back():
    """LLM returns column that doesn't exist → silently drop, fall back."""
    client = _FakeClient({
        "mapping": {"prompt_zh": "nonexistent_col", "prompt_en": None, "source_id": None},
    })
    m, why = llm_guess(
        ["id", "prompt"],
        [{"id": 1, "prompt": "hello"}],
        client=client,
    )
    # Should fall back to heuristic since LLM gave no valid prompt
    assert m.prompt_en == "prompt"
    assert "回退" in why


def test_llm_guess_exception_falls_back():
    client = _FakeClient({}, raise_exc=RuntimeError("API timeout"))
    m, why = llm_guess(
        ["id", "prompt"],
        [{"id": 1, "prompt": "hello"}],
        client=client,
    )
    assert m.prompt_en == "prompt"
    assert "回退" in why
    assert "RuntimeError" in why


# ---------- FieldMapping validators (cross-module guarantee) ----------

def test_field_mapping_requires_at_least_one_prompt():
    from t2v_promptgen.core.rewrite_schema import FieldMapping
    with pytest.raises(ValueError):
        FieldMapping(source_id="id")  # both prompts None — should reject
