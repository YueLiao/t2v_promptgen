"""CLI entry: `t2v-promptgen <subcommand>`.

Subcommands:
    create     Start a new specialty-capability prompt-set generation
    resume     Resume an in-flight run
    list       List runs
    show       Show one run's status / inspect prompts
    export     (Re)-export an already-completed run

    memory list                  List capabilities in memory
    memory show <slug>           Show latest version of a capability
    memory diff <slug> vN1 vN2   Diff between two versions
    memory export <slug>         Bundle for sharing
    memory import <archive>      Restore a bundle
    memory rm <slug> [--version N]    Delete (defaults to all versions)

Interactive flow (`create`):

    $ t2v-promptgen create
    Capability description: 测人手生成质量,特别是手指数量+握持
    > Standardized slug: human_hand
    > Found 2 previous versions:
        v1 (2026-05-14, 60 prompts)
        v2 (2026-05-20, 80 prompts)
    > [c] continue from v2  [f] fresh  [p] cherry-pick : c
    > Loaded 8 SL2 + 4 axes from v2.
    > Set size? [auto=auto/40-120/manual]: auto
    > Provider [anthropic]: <enter>
    > Model [claude-opus-4-7]: <enter>

    [Phase 1 ｜ Round 1/5]
    SL2:   1. hand_finger_count  ...
    Axes:  1. 持物角度: [正面平举, 侧面, 胸前, 举过头顶]  ...
    Edit? [y/n/free-text]: ...

    [Phase 2 ｜ Generating 80 prompts...]
    [Phase 3 ｜ QA pass 1/3...]
    [Phase 4 ｜ Round 1/3]
    Coverage heatmap: ✓ all cells hit
    Difficulty:       medium 48 (60%) | hard 32 (40%)
    Stress:           24 (30%)
    Sample 5 prompts: ...
    Approve? [y/edit/regen-ids]: y

    [Phase 5 ｜ Export]
    → ./out/human_hand__v3__2026-05-21__001/
       prompts.jsonl
       evaluator_handbook.md
       evaluator_handbook.json
       coverage_report.json
       generation_log.json
       set_meta.yaml
    → Memory updated: human_hand v3
"""
from __future__ import annotations

# Will use click or typer; using bare stubs for now
def main() -> None:
    """CLI entry point. Dispatches to subcommands."""
    raise NotImplementedError


# Subcommand stubs

def cmd_create(capability: str | None = None,
               size: str = "auto",
               provider: str = "anthropic",
               model: str = "claude-opus-4-7") -> None:
    """Start a new run. If capability not given, prompt interactively."""
    raise NotImplementedError


def cmd_resume(run_id: str) -> None:
    """Resume an in-flight run from runs.db."""
    raise NotImplementedError


def cmd_list() -> None:
    """List all runs (latest first), with phase and timestamps."""
    raise NotImplementedError


def cmd_show(run_id: str) -> None:
    """Show full details of one run."""
    raise NotImplementedError


def cmd_export(run_id: str, output_dir: str | None = None) -> None:
    """Re-export an already-completed run."""
    raise NotImplementedError


def cmd_memory_list() -> None:
    raise NotImplementedError


def cmd_memory_show(slug: str, version: int | None = None) -> None:
    raise NotImplementedError


def cmd_memory_diff(slug: str, v1: int, v2: int) -> None:
    raise NotImplementedError


def cmd_memory_export(slug: str, to: str) -> None:
    raise NotImplementedError


def cmd_memory_import(archive: str) -> None:
    raise NotImplementedError


def cmd_memory_rm(slug: str, version: int | None = None) -> None:
    raise NotImplementedError


if __name__ == "__main__":
    main()
