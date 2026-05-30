"""Tests for parsers.prompt_loader — covers 5 formats + §12 boundaries."""
import io
import json

import pytest

from t2v_promptgen.core.rewrite_schema import ParseError, MAX_FILE_BYTES, MAX_ROWS
from t2v_promptgen.parsers.prompt_loader import detect_format, load_prompts


# ---------- Format detection ----------

def test_detect_json_by_suffix():
    assert detect_format("a.json", b"[]") == "json"


def test_detect_jsonl_by_suffix():
    assert detect_format("a.jsonl", b'{"x":1}\n{"y":2}') == "jsonl"


def test_detect_csv_by_suffix():
    assert detect_format("a.csv", b"a,b\n1,2") == "csv"


def test_detect_xlsx_by_magic():
    # xlsx is a zip — header starts with PK\x03\x04
    assert detect_format("a.xlsx", b"PK\x03\x04rest") == "xlsx"


def test_detect_format_mismatch_xlsx():
    # Suffix .xlsx but content isn't zip
    with pytest.raises(ParseError) as exc:
        detect_format("a.xlsx", b"not a zip")
    assert exc.value.code == "PARSE_FORMAT_MISMATCH"


def test_detect_by_head_when_no_suffix():
    # No extension — JSON head wins
    assert detect_format("noext", b"[{}]") == "json"


def test_detect_format_unknown_empty():
    with pytest.raises(ParseError) as exc:
        detect_format("noext", b"")
    assert exc.value.code == "PARSE_FORMAT_UNKNOWN"


# ---------- JSON ----------

def test_json_array():
    data = b'[{"id":1,"prompt":"foo"},{"id":2,"prompt":"bar"}]'
    sf, rows = load_prompts(data, "a.json")
    assert sf.format == "json"
    assert sf.row_count == 2
    assert rows[0] == {"id": 1, "prompt": "foo"}


def test_json_wrapper_dict():
    data = b'{"prompts":[{"text":"x"}]}'
    sf, rows = load_prompts(data, "a.json")
    assert sf.row_count == 1
    assert rows[0]["text"] == "x"


def test_json_alt_wrappers():
    for key in ("data", "items", "rows"):
        data = json.dumps({key: [{"x": 1}]}).encode()
        sf, rows = load_prompts(data, "a.json")
        assert rows[0]["x"] == 1


def test_json_strings_become_text_dicts():
    data = b'["one", "two"]'
    sf, rows = load_prompts(data, "a.json")
    assert rows == [{"text": "one"}, {"text": "two"}]


def test_json_invalid_syntax():
    with pytest.raises(ParseError) as exc:
        load_prompts(b"{not json}", "a.json")
    assert exc.value.code == "PARSE_JSON_INVALID"
    assert "1" in (exc.value.location or "")


def test_json_dict_without_known_key():
    with pytest.raises(ParseError) as exc:
        load_prompts(b'{"weird":"x"}', "a.json")
    assert exc.value.code == "EMPTY_FILE"


# ---------- JSONL ----------

def test_jsonl_normal():
    data = b'{"id":1}\n{"id":2}\n{"id":3}'
    sf, rows = load_prompts(data, "a.jsonl")
    assert sf.format == "jsonl"
    assert len(rows) == 3


def test_jsonl_skips_bad_lines():
    data = b'{"id":1}\nNOT JSON\n{"id":2}'
    sf, rows = load_prompts(data, "a.jsonl")
    assert len(rows) == 2
    assert {r["id"] for r in rows} == {1, 2}


def test_jsonl_all_bad_lines():
    with pytest.raises(ParseError) as exc:
        load_prompts(b"NOT JSON\nNEITHER", "a.jsonl")
    assert exc.value.code == "PARSE_JSON_INVALID"


# ---------- TXT ----------

def test_txt_one_per_line():
    sf, rows = load_prompts(b"one\ntwo\nthree", "a.txt")
    assert [r["text"] for r in rows] == ["one", "two", "three"]


def test_txt_paragraph_mode():
    data = b"para1 line1\npara1 line2\n\npara2"
    sf, rows = load_prompts(data, "a.txt")
    assert len(rows) == 2
    assert "para1 line1" in rows[0]["text"]
    assert "para2" in rows[1]["text"]


def test_txt_empty():
    with pytest.raises(ParseError) as exc:
        load_prompts(b"\n\n\n", "a.txt")
    assert exc.value.code == "EMPTY_FILE"


# ---------- CSV ----------

def test_csv_normal():
    data = b'id,prompt,tag\n1,hello,a\n2,world,b'
    sf, rows = load_prompts(data, "a.csv")
    assert sf.format == "csv"
    assert rows[0] == {"id": "1", "prompt": "hello", "tag": "a"}


def test_csv_with_bom():
    data = b'\xef\xbb\xbfid,prompt\n1,hello'
    sf, rows = load_prompts(data, "a.csv")
    # BOM stripped
    assert "id" in rows[0]
    assert rows[0]["id"] == "1"


def test_csv_embedded_commas():
    data = b'id,prompt\n1,"foo, bar"\n2,baz'
    sf, rows = load_prompts(data, "a.csv")
    assert rows[0]["prompt"] == "foo, bar"


def test_csv_only_header():
    with pytest.raises(ParseError) as exc:
        load_prompts(b"id,prompt", "a.csv")
    assert exc.value.code == "EMPTY_FILE"


# ---------- XLSX ----------

def _make_xlsx(sheets: dict[str, list[list]]) -> bytes:
    from openpyxl import Workbook
    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for row in rows:
            ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_xlsx_first_sheet_default():
    data = _make_xlsx({"sheet1": [["id", "prompt"], [1, "hello"], [2, "world"]]})
    sf, rows = load_prompts(data, "a.xlsx")
    assert sf.format == "xlsx"
    assert sf.sheet_name == "sheet1"
    assert rows == [{"id": 1, "prompt": "hello"}, {"id": 2, "prompt": "world"}]


def test_xlsx_choose_sheet():
    data = _make_xlsx({
        "s1": [["a"], [1]],
        "s2": [["b"], [2]],
    })
    sf, rows = load_prompts(data, "a.xlsx", sheet_name="s2")
    assert sf.sheet_name == "s2"
    assert rows == [{"b": 2}]


def test_xlsx_skips_empty_rows():
    data = _make_xlsx({"s": [
        ["id", "prompt"],
        [1, "a"],
        [None, None],
        [2, "b"],
    ]})
    sf, rows = load_prompts(data, "a.xlsx")
    assert len(rows) == 2


def test_xlsx_no_sheet():
    # openpyxl always creates at least one sheet, but we can simulate empty content
    data = _make_xlsx({"empty": []})
    with pytest.raises(ParseError) as exc:
        load_prompts(data, "a.xlsx")
    assert exc.value.code == "EMPTY_FILE"


def test_xlsx_duplicate_headers():
    data = _make_xlsx({"s": [["x", "x", "x"], [1, 2, 3], [4, 5, 6]]})
    sf, rows = load_prompts(data, "a.xlsx")
    # Headers de-duped: x, x_1, x_2
    assert set(rows[0].keys()) == {"x", "x_1", "x_2"}


# ---------- Size & count limits ----------

def test_size_exceeded():
    big = b"x" * (MAX_FILE_BYTES + 1)
    with pytest.raises(ParseError) as exc:
        load_prompts(big, "a.txt")
    assert exc.value.code == "SIZE_EXCEEDED"


def test_empty_file():
    with pytest.raises(ParseError) as exc:
        load_prompts(b"", "a.json")
    assert exc.value.code == "EMPTY_FILE"


def test_row_count_capped():
    # Build a JSONL with MAX_ROWS + 1 rows
    data = ("\n".join(f'{{"id":{i}}}' for i in range(MAX_ROWS + 1))).encode()
    with pytest.raises(ParseError) as exc:
        load_prompts(data, "big.jsonl")
    assert exc.value.code == "ROW_EXCEEDED"


# ---------- Filename safety ----------

def test_filename_path_traversal_stripped():
    sf, _ = load_prompts(b'[{"x":1}]', "../../../etc/passwd.json")
    assert "/" not in sf.filename
    assert "passwd.json" in sf.filename


# ---------- Sample preview ----------

def test_sample_has_at_most_5_rows():
    data = ("\n".join(f'{{"id":{i}}}' for i in range(20))).encode()
    sf, _ = load_prompts(data, "a.jsonl")
    assert len(sf.sample) == 5


def test_sample_truncates_long_strings():
    long = "x" * 500
    data = json.dumps([{"id": 1, "text": long}]).encode()
    sf, _ = load_prompts(data, "a.json")
    assert len(sf.sample[0]["text"]) <= 210   # 200 + "..."
