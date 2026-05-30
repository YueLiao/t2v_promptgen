"""Tests for parsers.encoding_detect — covers §12 encoding rows."""
import pytest

from t2v_promptgen.core.rewrite_schema import ParseError
from t2v_promptgen.parsers.encoding_detect import detect_and_decode


def test_utf8_plain():
    text, enc = detect_and_decode("你好 hello".encode("utf-8"))
    assert text == "你好 hello"
    assert enc == "utf-8"


def test_utf8_with_bom():
    raw = b"\xef\xbb\xbf" + "你好".encode("utf-8")
    text, enc = detect_and_decode(raw)
    assert text == "你好"
    assert enc == "utf-8-sig"


def test_utf16_le_bom():
    raw = b"\xff\xfe" + "你好".encode("utf-16-le")
    text, enc = detect_and_decode(raw)
    assert text == "你好"
    assert enc == "utf-16-le"


def test_utf16_be_bom():
    raw = b"\xfe\xff" + "你好".encode("utf-16-be")
    text, enc = detect_and_decode(raw)
    assert text == "你好"
    assert enc == "utf-16-be"


def test_gb18030():
    text, enc = detect_and_decode("你好世界".encode("gb18030"))
    assert text == "你好世界"
    assert enc == "gb18030"


def test_empty_bytes():
    text, enc = detect_and_decode(b"")
    assert text == ""
    assert enc == "utf-8"


def test_ascii_only():
    text, enc = detect_and_decode(b"hello world")
    assert text == "hello world"
    # ASCII decodes as utf-8 first
    assert enc == "utf-8"


def test_garbage_does_not_crash():
    # Should either decode (via fallback) or raise ParseError, never crash
    try:
        text, enc = detect_and_decode(b"\x80\x81\x82\x83\xff")
        # If it succeeded, fine
        assert isinstance(text, str)
    except ParseError as e:
        assert e.code == "PARSE_ENCODING_FAIL"
