"""Render the evaluator handbook as a structured JSON for platform ingestion.

The GSB AB-test platform consumes this to render checkboxes per SL2.

Shape:

    {
      "capability": "human_hand",
      "capability_version": 2,
      "schema_version": 1,
      "sl2_items": [
        {
          "id": "hand_finger_count",
          "name_zh": "手指数量错误",
          "name_en": "Finger count error",
          "yes_criteria_zh": "...",
          "yes_criteria_en": "...",
          "no_cases_zh": ["..."],
          "no_cases_en": ["..."],
          "notes_zh": "...",
          "notes_en": "...",
          "example_pass_url": null,    // v1 placeholder
          "example_fail_url": null,
          "weight": 1.0                // future: SL2-level weighting
        }, ...
      ]
    }
"""
from __future__ import annotations

from pathlib import Path

from ..core.schema import CapabilityVersion


def render(cap: CapabilityVersion) -> dict:
    """Return platform-ingest JSON as a Python dict."""
    raise NotImplementedError


def write(cap: CapabilityVersion, path: Path) -> None:
    """Write JSON to disk."""
    raise NotImplementedError
