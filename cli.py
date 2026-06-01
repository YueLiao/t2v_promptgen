"""Command-line entry point — automation / CI integration.

Invoke with `python -m t2v_promptgen.cli <subcmd>` (no console-script
shim is installed — there is no pyproject.toml/setup.py packaging in
this prototype).

Examples (run from the repo root):
    # End-to-end: take description → produce prompts + manifest
    python -m t2v_promptgen.cli create \\
        --description="测试视频里人手细节" \\
        --size=60 --provider=deepseek --api-key=sk-... \\
        --out=./out --seed=42

    # List all stored runs
    python -m t2v_promptgen.cli list

    # Show one run's metadata (JSON)
    python -m t2v_promptgen.cli show abc12345

    # Clone an existing run's design (skips re-generation)
    python -m t2v_promptgen.cli clone abc12345 --size=80

    # Re-export an existing run
    python -m t2v_promptgen.cli export abc12345 --out=./out/

    # Delete
    python -m t2v_promptgen.cli delete abc12345

    # DB stats
    python -m t2v_promptgen.cli stats

Every create/export pairs the prompts file with a manifest.json
containing tool version, LLM model id, --seed, tag-taxonomy hash, and
counts — for reproducibility audits.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

from .core.persistence import (
    db_stats,
    delete_run as db_delete,
    list_run_summaries,
    load_run,
    save_run,
)
from .core.schema import Phase, Run

TOOL_VERSION = "0.8"


def _build_client(provider: str, model: str, api_key: str | None, base_url: str | None):
    from .web import llm_phases
    if not api_key:
        raise SystemExit("--api-key required (or set T2V_LLM_API_KEY env)")
    return llm_phases.build_client(provider=provider, model=model,
                                    api_key=api_key, base_url=base_url)


def _tag_library_hash() -> str:
    """Hash the canonical tag taxonomy for manifest pinning."""
    from .core.annotation_schema import ALL_DIMENSIONS
    payload = json.dumps([
        {"d": d.code, "values": [v.code for v in d.values]}
        for d in ALL_DIMENSIONS
    ], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _build_manifest(run: Run, seed: int | None, extras: dict | None = None) -> dict:
    return {
        "tool_version": TOOL_VERSION,
        "schema_version": 1,
        "generated_at": datetime.now().isoformat(),
        "run_id": run.id,
        "capability_slug": run.capability_slug,
        "source": run.source,
        "provider": run.provider,
        "model": run.model,
        "seed": seed,
        "target_set_size": run.target_set_size,
        "tag_library_sha256_short": _tag_library_hash(),
        "sl2_count": len(run.sl2_list),
        "axes_count": len(run.axes),
        "recommended_tags_count": sum(len(v) for v in run.recommended_tags.values()),
        "prompt_count": len(run.prompts),
        "p1_round": run.p1_round,
        "p4_round": run.p4_round,
        "rewrite_round": run.rewrite_round,
        **(extras or {}),
    }


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

def cmd_list(args):
    sums = list_run_summaries()
    if not sums:
        print("(no runs)")
        return
    print(f"{'ID':<10} {'SRC':<8} {'PHASE':<16} {'CAPABILITY':<24} {'UPDATED'}")
    print("-" * 80)
    for s in sums:
        upd = s["updated_at"][:19].replace("T", " ")
        print(f"{s['id']:<10} {s['source']:<8} {s['phase']:<16} "
              f"{s['capability_slug']:<24} {upd}")


def cmd_show(args):
    pair = load_run(args.run_id)
    if not pair:
        sys.exit(f"run {args.run_id} not found")
    run, extras = pair
    out = {
        "id": run.id,
        "capability_slug": run.capability_slug,
        "source": run.source,
        "phase": run.phase.value,
        "created_at": run.created_at.isoformat(),
        "updated_at": run.updated_at.isoformat(),
        "provider": run.provider, "model": run.model,
        "sl2_count": len(run.sl2_list),
        "axes_count": len(run.axes),
        "prompt_count": len(run.prompts),
        "recommended_tags": run.recommended_tags,
        "has_creds": "creds" in extras,
    }
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))


def cmd_delete(args):
    if db_delete(args.run_id):
        print(f"deleted {args.run_id}")
    else:
        sys.exit(f"run {args.run_id} not found")


def cmd_stats(args):
    print(json.dumps(db_stats(), ensure_ascii=False, indent=2))


def cmd_create(args):
    """End-to-end: P0 intake → P1 dimensions → P2 generate → optional P3 → export."""
    if args.seed is not None:
        random.seed(args.seed)

    api_key = args.api_key or os.environ.get("T2V_LLM_API_KEY")
    if not api_key:
        sys.exit("--api-key required (or T2V_LLM_API_KEY env)")

    from .phases.intake import classify_with_fallback
    from .web import llm_phases, mock_data

    client_p1 = _build_client(args.provider, args.model_p1, api_key, args.base_url)
    client_p2 = (client_p1 if args.model_p1 == args.model_p2
                 else _build_client(args.provider, args.model_p2, api_key, args.base_url))

    # P0
    t0 = time.time()
    print("[P0] intake...", flush=True)
    intake = classify_with_fallback(args.description, client=client_p1)
    slug = intake["slug"]
    print(f"     slug={slug} confidence={intake['confidence']} ({time.time()-t0:.1f}s)")

    # Run shell
    now = datetime.now()
    run = Run(
        id=str(uuid.uuid4())[:8],
        capability_slug=slug,
        created_at=now, updated_at=now,
        phase=Phase.P1_DIMENSIONS,
        user_description=args.description,
        provider=args.provider,
        model=f"{args.model_p1} / {args.model_p2}",
    )

    # P1
    t0 = time.time()
    print("[P1] dimensions...", flush=True)
    try:
        sl2, axes, rec_tags = llm_phases.generate_dimensions_real(
            description=args.description, client=client_p1,
        )
        run.sl2_list, run.axes = sl2, axes
        run.recommended_tags = rec_tags or mock_data.default_recommended_tags(slug)
        run.original_ai_tags = {k: list(v) for k, v in run.recommended_tags.items()}
    except Exception as exc:
        print(f"     LLM failed → mock fallback: {exc}", flush=True)
        run.sl2_list, run.axes = mock_data.generate_mock_dimensions(
            args.description, capability_slug=slug,
        )
        run.recommended_tags = mock_data.default_recommended_tags(slug)
        run.original_ai_tags = dict(run.recommended_tags)
    print(f"     SL2={len(run.sl2_list)} axes={len(run.axes)} "
          f"tags={sum(len(v) for v in run.recommended_tags.values())} "
          f"({time.time()-t0:.1f}s)")

    # Target size
    from .phases.dimensions import compute_min_set_size
    if args.size == "auto":
        run.target_set_size = compute_min_set_size(run.axes)
    else:
        try:
            run.target_set_size = max(40, min(120, int(args.size)))
        except ValueError:
            run.target_set_size = compute_min_set_size(run.axes)
    print(f"     target_set_size={run.target_set_size}")

    # P2 generate
    t0 = time.time()
    print(f"[P2] generating {run.target_set_size} prompts...", flush=True)
    run.phase = Phase.P2_PROMPTS
    try:
        prompts = llm_phases.generate_prompts_real(
            capability=slug, sl2_list=run.sl2_list, axes=run.axes,
            target_size=run.target_set_size, client=client_p2,
        )
        run.prompts = prompts
    except Exception as exc:
        print(f"     LLM failed → mock fallback: {exc}", flush=True)
        run.prompts = mock_data.generate_mock_prompts(
            run.sl2_list, run.axes, run.target_set_size,
        )
    print(f"     got {len(run.prompts)} prompts ({time.time()-t0:.1f}s)")

    # P3 qa (optional)
    if args.skip_qa:
        print("[P3] skipped (--skip-qa)")
    else:
        t0 = time.time()
        print("[P3] qa...", flush=True)
        try:
            from .phases.qa import run as run_qa
            report = run_qa(run.prompts, run.sl2_list, run.axes, client=client_p2)
            print(f"     pass_rate={report.pass_rate:.0%} "
                  f"({report.passed}/{report.total}) ({time.time()-t0:.1f}s)")
        except Exception as exc:
            print(f"     qa failed (continuing): {exc}", flush=True)

    run.phase = Phase.P5_EXPORT
    run.updated_at = datetime.now()
    save_run(run, creds={
        "provider": args.provider, "model": args.model_p1,
        "model_p1": args.model_p1, "model_p2": args.model_p2,
        "api_key": api_key, "base_url": args.base_url or None,
    })

    _export_files(run, Path(args.out), seed=args.seed)
    print(f"\n✓ run {run.id} done; files written under {args.out}/")


def cmd_clone(args):
    pair = load_run(args.run_id)
    if not pair:
        sys.exit(f"run {args.run_id} not found")
    src, src_extras = pair
    now = datetime.now()
    new = src.model_copy(update={
        "id": str(uuid.uuid4())[:8],
        "created_at": now,
        "updated_at": now,
        "phase": Phase.P1_DIMENSIONS,
        "user_description": (src.user_description or "") + f"\n[clone of {src.id}]",
        "prompts": [],
        "source_file": None,
        "source_prompts": [],
        "p1_round": 0, "p4_round": 0, "rewrite_round": 0,
        "target_set_size": args.size if args.size else src.target_set_size,
    })
    save_run(new, creds=src_extras.get("creds"))
    print(f"cloned {src.id} → {new.id} "
          f"(phase=P1_DIMENSIONS, SL2={len(new.sl2_list)}, "
          f"axes={len(new.axes)}, tags={sum(len(v) for v in new.recommended_tags.values())})")


def cmd_export(args):
    pair = load_run(args.run_id)
    if not pair:
        sys.exit(f"run {args.run_id} not found")
    run, _ = pair
    if not run.prompts:
        sys.exit(f"run {args.run_id} has no prompts to export")
    _export_files(run, Path(args.out), seed=None)


def _export_files(run: Run, out_dir: Path, seed: int | None) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    # prompts.jsonl
    pf = out_dir / f"prompts_{run.capability_slug}_{run.id}.jsonl"
    with pf.open("w", encoding="utf-8") as f:
        for p in run.prompts:
            f.write(p.model_dump_json() + "\n")
    print(f"  → {pf}")

    # manifest.json
    mf = out_dir / f"manifest_{run.capability_slug}_{run.id}.json"
    mf.write_text(
        json.dumps(_build_manifest(run, seed=seed), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"  → {mf}")

    # Rewrite diff (if applicable)
    if run.source == "rewrite" and run.source_prompts:
        sp_by_id = {sp.source_id: sp for sp in run.source_prompts}
        df = out_dir / f"diff_{run.id}.jsonl"
        with df.open("w", encoding="utf-8") as f:
            for p in run.prompts:
                sp = sp_by_id.get(p.source_id) if p.source_id else None
                rec = {
                    "id": p.id, "source_id": p.source_id,
                    "original_text": sp.original_text if sp else None,
                    "prompt_zh": p.prompt_zh, "prompt_en": p.prompt_en,
                    "rewrite_diff": p.rewrite_diff,
                    "keep": p.rewrite_kept_score,
                    "adh": p.rewrite_adherence_score,
                    "accepted": p.rewrite_accepted,
                }
                f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        print(f"  → {df}")


# ---------------------------------------------------------------------------
# Argparse
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="t2v-promptgen",
                                description="T2V prompt benchmark generator")
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("list", help="List runs in local DB")
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("show", help="Show one run's metadata")
    sp.add_argument("run_id")
    sp.set_defaults(func=cmd_show)

    sp = sub.add_parser("delete", help="Remove a run from local DB")
    sp.add_argument("run_id")
    sp.set_defaults(func=cmd_delete)

    sp = sub.add_parser("stats", help="DB health stats")
    sp.set_defaults(func=cmd_stats)

    sp = sub.add_parser("create",
                         help="End-to-end: intake → dimensions → generate → export")
    sp.add_argument("--description", required=True, help="What capability to test")
    sp.add_argument("--size", default="auto",
                    help="Target prompt count (int) or 'auto'")
    sp.add_argument("--provider", default="deepseek")
    sp.add_argument("--model-p1", default="deepseek-chat", dest="model_p1",
                    help="Analysis model (one-shot)")
    sp.add_argument("--model-p2", default="deepseek-chat", dest="model_p2",
                    help="Generation model (batched)")
    sp.add_argument("--api-key", dest="api_key", default=None,
                    help="LLM API key (or set T2V_LLM_API_KEY env)")
    sp.add_argument("--base-url", dest="base_url", default=None)
    sp.add_argument("--out", default="./out", help="Output directory")
    sp.add_argument("--seed", type=int, default=None,
                    help="Pin RNG seed for reproducible sampling")
    sp.add_argument("--skip-qa", action="store_true",
                    help="Skip P3 quality judges (saves LLM cost)")
    sp.set_defaults(func=cmd_create)

    sp = sub.add_parser("clone",
                         help="Copy an existing run's SL2/axes/tags into a fresh P1 run")
    sp.add_argument("run_id")
    sp.add_argument("--size", type=int, default=None,
                    help="Override target size (default: keep source's)")
    sp.set_defaults(func=cmd_clone)

    sp = sub.add_parser("export",
                         help="Re-export an existing run's prompts.jsonl + manifest")
    sp.add_argument("run_id")
    sp.add_argument("--out", default="./out")
    sp.set_defaults(func=cmd_export)

    return p


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except KeyboardInterrupt:
        print("\nInterrupted", file=sys.stderr)
        sys.exit(130)


if __name__ == "__main__":
    main()
