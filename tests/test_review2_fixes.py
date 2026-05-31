"""Tests for Comprehensive Review 2: LLM client robustness + filename safety."""
import pytest

from t2v_promptgen.llm.providers.openai_compat import (
    _parse_json_lenient,
    _validate_schema_keys,
)


# ---------- _parse_json_lenient ----------

def test_parse_json_lenient_string_with_brace_inside():
    """Strings containing } should not throw off brace counter."""
    text = '{"text": "}"}'
    result = _parse_json_lenient(text)
    assert result == {"text": "}"}


def test_parse_json_lenient_escaped_quote_in_string():
    """Escaped quotes inside strings should not exit string-tracking."""
    text = '{"text": "a \\"b\\" c"}'
    result = _parse_json_lenient(text)
    assert result == {"text": 'a "b" c'}


def test_parse_json_lenient_multiple_braces_in_string():
    """Strings with { and } both should parse correctly."""
    text = '{"json": "{nested}"}'
    result = _parse_json_lenient(text)
    assert result == {"json": "{nested}"}


def test_parse_json_lenient_empty_input_raises():
    with pytest.raises(ValueError, match="Empty LLM"):
        _parse_json_lenient("")
    with pytest.raises(ValueError, match="Empty LLM"):
        _parse_json_lenient("   \n  ")


def test_parse_json_lenient_strips_markdown_fence():
    text = '```json\n{"a": 1}\n```'
    result = _parse_json_lenient(text)
    assert result == {"a": 1}


def test_parse_json_lenient_no_json_in_text_raises():
    with pytest.raises(ValueError, match="No JSON object"):
        _parse_json_lenient("here is some prose without any json")


def test_parse_json_lenient_unbalanced_braces_raises():
    with pytest.raises(ValueError, match=r"(Malformed JSON|Unbalanced braces)"):
        _parse_json_lenient('{"a": 1')


def test_parse_json_lenient_recovers_from_trailing_prose():
    """LLM sometimes adds prose after the JSON — should still parse the JSON part."""
    text = '{"a": 1, "b": 2}\n\nHope this helps!'
    result = _parse_json_lenient(text)
    assert result == {"a": 1, "b": 2}


# ---------- _validate_schema_keys ----------

def test_validate_schema_keys_missing():
    with pytest.raises(ValueError, match="missing required keys"):
        _validate_schema_keys({"a": 1}, {"required": ["a", "b", "c"]})


def test_validate_schema_keys_extra_ok():
    """Extra keys beyond required ones are fine."""
    _validate_schema_keys({"a": 1, "b": 2, "extra": 99}, {"required": ["a", "b"]})


def test_validate_schema_keys_non_dict_raises():
    with pytest.raises(ValueError, match="Expected dict"):
        _validate_schema_keys([1, 2, 3], {"required": ["a"]})


def test_validate_schema_keys_empty_required_passes():
    _validate_schema_keys({}, {"required": []})
    _validate_schema_keys({"x": 1}, {})


# ---------- intake empty-description guard ----------

def test_intake_classify_with_fallback_empty_description():
    """Empty description shouldn't call LLM, returns deterministic shape."""
    from t2v_promptgen.phases.intake import classify_with_fallback
    result = classify_with_fallback("")
    assert result["slug"] == "custom_capability"
    assert result["source"] == "empty"
    assert result["confidence"] == "low"


def test_intake_classify_with_fallback_whitespace_description():
    from t2v_promptgen.phases.intake import classify_with_fallback
    result = classify_with_fallback("   \n  \t ")
    assert result["source"] == "empty"


def test_intake_classify_capability_llm_empty_raises():
    """Direct LLM call with empty description should reject early."""
    from t2v_promptgen.phases.intake import classify_capability_llm
    with pytest.raises(ValueError, match="non-empty"):
        classify_capability_llm("", client=None)


# ---------- Content-Disposition with non-ASCII filenames ----------

def test_content_disposition_ascii_filename():
    from t2v_promptgen.web.app import _content_disposition
    h = _content_disposition("prompts_human_hand.jsonl")
    assert "filename=" in h
    assert "filename*=" in h
    assert "prompts_human_hand.jsonl" in h


def test_content_disposition_non_ascii_filename():
    from t2v_promptgen.web.app import _content_disposition
    h = _content_disposition("人手测试_60条.jsonl")
    # ASCII fallback must be present
    assert 'filename="' in h
    # RFC 5987 utf-8 form must be present
    assert "filename*=UTF-8''" in h
    # The encoded filename should NOT contain the raw Chinese (would break header)
    import re
    ascii_part_match = re.search(r'filename="([^"]+)"', h)
    assert ascii_part_match
    ascii_part = ascii_part_match.group(1)
    # All chars in the ASCII-named fallback must be < 128
    assert all(ord(c) < 128 for c in ascii_part)


def test_content_disposition_dangerous_chars_stripped():
    from t2v_promptgen.web.app import _content_disposition
    h = _content_disposition('file"with;quotes.jsonl')
    # Should not contain raw quote (would break the header)
    import re
    fallback = re.search(r'filename="([^"]+)"', h).group(1)
    assert '"' not in fallback
    assert ";" not in fallback
