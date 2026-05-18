"""Phase 5 — Export + Memory write.

Output files (written to a single run directory):
    prompts.jsonl                    — main data (PromptEntry per line)
    evaluator_handbook.md            — Markdown rubric for evaluators
    evaluator_handbook.json          — structured rubric for platform ingest
    coverage_report.json             — CoverageReport
    generation_log.json              — provider/model/tokens/cost/iterations
    set_meta.yaml                    — capability slug, version, decisions

Memory writes:
    capabilities/{slug}/v{N+1}.yaml  — new CapabilityVersion snapshot
    seed_pool/{slug}.jsonl           — append P4-confirmed prompts (200 cap)
"""
from __future__ import annotations

from pathlib import Path

from ..core.schema import Run


def run(run: Run, output_dir: Path | None = None) -> Path:
    """Execute P5. Returns the output directory path.

    output_dir default: ./out/{capability_slug}__v{N}__{timestamp}/
    """
    raise NotImplementedError


def write_prompts_jsonl(run: Run, path: Path) -> None:
    raise NotImplementedError


def write_coverage_report(run: Run, path: Path) -> None:
    raise NotImplementedError


def write_generation_log(run: Run, path: Path) -> None:
    raise NotImplementedError


def commit_to_memory(run: Run) -> int:
    """Save CapabilityVersion to memory + append seed_pool. Returns new version N."""
    raise NotImplementedError
