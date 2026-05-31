"""Multi-format prompt-set exporters.

Each exporter takes a list[PromptEntry] and returns (bytes, mime_type,
suffix). Adapters cover common downstream evaluation platforms so
downstream consumers don't have to write parsers.

Supported formats:
  - jsonl       : native (one PromptEntry per line, full schema)
  - csv         : generic CSV — flat columns suitable for spreadsheets
  - vbench      : VBench-compatible CSV (prompt_en + dimension labels)
  - evalcrafter : EvalCrafter format (id + prompt + category)
  - txt         : plain text, one prompt per line (zh)
  - txt_en      : plain text, one prompt per line (en)
"""
from __future__ import annotations

import csv
import io
import json
from typing import Literal

from .schema import PromptEntry


ExportFormat = Literal["jsonl", "csv", "vbench", "evalcrafter", "txt", "txt_en"]


def export(prompts: list[PromptEntry], fmt: ExportFormat) -> tuple[bytes, str, str]:
    """Dispatcher. Returns (body_bytes, content_type, file_suffix)."""
    if fmt == "jsonl":
        return _export_jsonl(prompts)
    if fmt == "csv":
        return _export_csv_generic(prompts)
    if fmt == "vbench":
        return _export_vbench(prompts)
    if fmt == "evalcrafter":
        return _export_evalcrafter(prompts)
    if fmt == "txt":
        return _export_txt(prompts, lang="zh")
    if fmt == "txt_en":
        return _export_txt(prompts, lang="en")
    raise ValueError(f"unknown export format: {fmt!r}")


# ---------------------------------------------------------------------------
# Native JSONL (full schema)
# ---------------------------------------------------------------------------

def _export_jsonl(prompts: list[PromptEntry]) -> tuple[bytes, str, str]:
    body = "\n".join(p.model_dump_json() for p in prompts)
    return body.encode("utf-8"), "application/x-jsonlines", ".jsonl"


# ---------------------------------------------------------------------------
# Generic CSV (flat columns)
# ---------------------------------------------------------------------------

_CSV_COLUMNS = [
    "id", "prompt_zh", "prompt_en", "difficulty", "is_stress",
    "subject_type", "subject_count", "action_count",
    "camera_zh", "camera_en",
    "scene_l1", "scene_l2", "scene_l3", "scene_l4",
    "sl2_covered", "axes_values",
    "qa_passed", "qa_naturalness_zh", "qa_naturalness_en",
    "difficulty_score",
    # Rewrite fields (None for generate runs)
    "source_id", "rewrite_diff",
    "rewrite_kept_score", "rewrite_adherence_score", "rewrite_accepted",
]


def _export_csv_generic(prompts: list[PromptEntry]) -> tuple[bytes, str, str]:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=_CSV_COLUMNS, extrasaction="ignore")
    w.writeheader()
    for p in prompts:
        row = p.model_dump(mode="json")
        # Flatten list/dict fields to JSON strings for CSV
        row["sl2_covered"] = json.dumps(row.get("sl2_covered") or [],
                                          ensure_ascii=False)
        row["axes_values"] = json.dumps(row.get("axes_values") or {},
                                          ensure_ascii=False)
        w.writerow(row)
    return buf.getvalue().encode("utf-8-sig"), "text/csv; charset=utf-8", ".csv"


# ---------------------------------------------------------------------------
# VBench-style CSV
# ---------------------------------------------------------------------------

# VBench public format uses an English prompt + a dimension label list.
# Reference: https://github.com/Vchitect/VBench  prompts_per_dimension/*.txt
# We approximate: one prompt per row, with columns (prompt_en, dimensions)
# where dimensions = ; -joined list of SL2 ids it covers.

def _export_vbench(prompts: list[PromptEntry]) -> tuple[bytes, str, str]:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["prompt_en", "dimensions"])
    for p in prompts:
        en = p.prompt_en or p.prompt_zh   # fall back to zh if no en
        w.writerow([en, ";".join(p.sl2_covered or [])])
    return buf.getvalue().encode("utf-8-sig"), "text/csv; charset=utf-8", ".csv"


# ---------------------------------------------------------------------------
# EvalCrafter format
# ---------------------------------------------------------------------------

# EvalCrafter https://github.com/evalcrafter/EvalCrafter uses
# {"id": ..., "prompt": ..., "category": ...} jsonl.

def _export_evalcrafter(prompts: list[PromptEntry]) -> tuple[bytes, str, str]:
    out: list[str] = []
    for p in prompts:
        rec = {
            "id": p.id,
            "prompt": p.prompt_en or p.prompt_zh,
            "category": (p.sl2_covered[0] if p.sl2_covered else p.capability),
        }
        out.append(json.dumps(rec, ensure_ascii=False))
    body = "\n".join(out)
    return body.encode("utf-8"), "application/x-jsonlines", ".jsonl"


# ---------------------------------------------------------------------------
# Plain text (one prompt per line)
# ---------------------------------------------------------------------------

def _export_txt(prompts: list[PromptEntry], lang: Literal["zh", "en"]
                ) -> tuple[bytes, str, str]:
    lines = []
    for p in prompts:
        text = p.prompt_zh if lang == "zh" else (p.prompt_en or p.prompt_zh)
        # Replace newlines so each prompt stays on one line
        lines.append(text.replace("\n", " ").strip())
    body = "\n".join(lines) + "\n"
    return body.encode("utf-8"), "text/plain; charset=utf-8", ".txt"


# ---------------------------------------------------------------------------
# UI/CLI helper
# ---------------------------------------------------------------------------

FORMAT_INFO = {
    "jsonl":       {"label": "测试用例(JSONL,主格式)", "ext": ".jsonl",
                    "desc": "原生格式,完整 schema,行内 JSON"},
    "csv":         {"label": "通用 CSV",            "ext": ".csv",
                    "desc": "Excel/标注平台导入,带 BOM 兼容中文"},
    "vbench":      {"label": "VBench 兼容 CSV",     "ext": ".csv",
                    "desc": "prompt_en + dimensions 两列,VBench 风格"},
    "evalcrafter": {"label": "EvalCrafter JSONL",  "ext": ".jsonl",
                    "desc": "{id, prompt, category} 简化 JSONL"},
    "txt":         {"label": "纯文本(中文)",       "ext": ".txt",
                    "desc": "一行一条 prompt(zh),给老派评测员手填用"},
    "txt_en":      {"label": "纯文本(英文)",       "ext": ".txt",
                    "desc": "一行一条 prompt(en)"},
}
