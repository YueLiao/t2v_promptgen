"""R3 — orchestrate batched LLM rewrites + iteration.

Public API:
  - rewrite_run(run, client, progress_cb, cancel_flag) -> RewriteResult
  - iterate_rewrite(run, rejected_source_ids, refinement, client) -> RewriteResult

Behavior:
  - Iterate over selected source_prompts (excluding ones already
    failed_to_rewrite=True or selected=False)
  - Batch 10 per LLM call
  - Per-prompt failures don't abort; tagged on the SourcePrompt
  - Successful rewrites become new PromptEntry rows in run.prompts
  - progress_cb(done, total) called once per completed batch
  - cancel_flag is a callable returning bool — checked between batches
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Iterable

from ..core.rewrite_schema import RewriteDirective, SourcePrompt
from ..core.schema import PromptEntry, Run

ProgressCallback = Callable[[int, int], None]
CancelFlag = Callable[[], bool]


@dataclass
class RewriteResult:
    succeeded: int = 0
    failed: int = 0
    cancelled: bool = False
    elapsed_seconds: float = 0.0
    error_breakdown: dict[str, int] = field(default_factory=dict)


def _eligible_prompts(run: Run) -> list[SourcePrompt]:
    """Pick source_prompts that should be rewritten this round."""
    if not run.rewrite_directive:
        return []
    selected_ids = run.rewrite_directive.selected_source_ids
    pool = run.source_prompts
    if selected_ids:
        idset = set(selected_ids)
        pool = [p for p in pool if p.source_id in idset]
    # Skip ones already flagged failed or unselected
    return [p for p in pool if p.selected and not p.failed_to_rewrite]


def rewrite_run(
    run: Run,
    client,
    progress_cb: ProgressCallback | None = None,
    cancel_flag: CancelFlag | None = None,
    batch_size: int = 10,
) -> RewriteResult:
    """Drive R3 over `run`. Mutates run.prompts and run.source_prompts."""
    from ..web.llm_phases import rewrite_prompts_real

    if run.source != "rewrite":
        raise RuntimeError("rewrite_run called on non-rewrite run")
    if not run.rewrite_directive:
        raise RuntimeError("no rewrite_directive on run")
    if client is None:
        raise RuntimeError("no LLM client — rewrite has no mock fallback")

    pending = _eligible_prompts(run)
    total = len(pending)
    if total == 0:
        return RewriteResult()

    t0 = time.time()
    result = RewriteResult()

    # Index for fast metadata lookup on commit
    sp_by_id = {p.source_id: p for p in run.source_prompts}

    # Server-side pre-assignment: deterministically spread multi_enum targets
    # across all pending source_ids so the LLM doesn't have to "pick" anymore.
    # Computed ONCE over the full pool (not per-batch) so distribution stays
    # balanced even when the batch boundary doesn't divide cleanly by m.
    from .rewrite_assign import pre_assign, derive_seed
    seed = run.rewrite_seed if run.rewrite_seed is not None else derive_seed(run.id)
    assignments = pre_assign(
        source_ids=[p.source_id for p in pending],
        directive=run.rewrite_directive,
        seed=seed,
    )
    # Telemetry: anchor any later "preview lied" debug — pool may have
    # changed between preview render and actual rewrite kick-off.
    print(f"[rewrite-assign] run={run.id} seed={seed} pool={total} "
          f"cards_assigned={sum(1 for a in assignments.values() if a)}",
          flush=True)

    done = 0
    for start in range(0, total, batch_size):
        if cancel_flag and cancel_flag():
            result.cancelled = True
            break

        batch = pending[start:start + batch_size]
        try:
            entries, failed_ids = rewrite_prompts_real(
                source_prompts=batch,
                directive=run.rewrite_directive,
                client=client,
                assignments=assignments,
            )
        except Exception as exc:
            # Whole-batch failure — mark them all and continue
            code = type(exc).__name__
            result.error_breakdown[code] = result.error_breakdown.get(code, 0) + len(batch)
            for sp in batch:
                sp.failed_to_rewrite = True
                sp.fail_reason = f"batch failure: {code}"
            result.failed += len(batch)
            done += len(batch)
            if progress_cb:
                progress_cb(done, total)
            continue

        # Commit successes
        for entry in entries:
            entry.capability = run.capability_slug
            entry.capability_version = 1
            run.prompts.append(entry)
            result.succeeded += 1

        # Mark per-prompt failures
        for sid in failed_ids:
            sp = sp_by_id.get(sid)
            if sp is not None:
                sp.failed_to_rewrite = True
                sp.fail_reason = sp.fail_reason or "LLM did not return entry"
                result.failed += 1
                result.error_breakdown["no_entry"] = result.error_breakdown.get("no_entry", 0) + 1

        done += len(batch)
        if progress_cb:
            progress_cb(done, total)

    result.elapsed_seconds = time.time() - t0
    return result


def iterate_rewrite(
    run: Run,
    rejected_source_ids: list[str],
    refinement_text: str,
    client,
    progress_cb: ProgressCallback | None = None,    # P1-2
) -> RewriteResult:
    """R5 iteration: only rewrite the rejected subset with appended refinement.

    Drops the previously-generated PromptEntry for each rejected id,
    runs a focused rewrite, replaces them.

    Pre: run.rewrite_round < run.rewrite_max_rounds; else raises ValueError.
    """
    if run.rewrite_round >= run.rewrite_max_rounds:
        raise ValueError("max rewrite rounds reached")
    if not rejected_source_ids:
        return RewriteResult()
    if not run.rewrite_directive:
        raise RuntimeError("no rewrite_directive on run")

    # Drop existing entries for the rejected ids
    rejected_set = set(rejected_source_ids)
    run.prompts = [p for p in run.prompts if p.source_id not in rejected_set]

    # Reset failed flag for re-attempt + scope directive to only these ids
    sp_by_id = {p.source_id: p for p in run.source_prompts}
    for sid in rejected_source_ids:
        sp = sp_by_id.get(sid)
        if sp is not None:
            sp.failed_to_rewrite = False
            sp.fail_reason = None

    # Build a transient directive copy with appended refinement + scope.
    # P2-2: only append "附加修改:" header if refinement is non-empty.
    new_free_text = run.rewrite_directive.free_text
    if refinement_text and refinement_text.strip():
        sep = "\n\n附加修改:\n" if new_free_text else ""
        new_free_text = (new_free_text + sep + refinement_text.strip()).strip()
    directive = run.rewrite_directive.model_copy(update={
        "free_text": new_free_text,
        "selected_source_ids": list(rejected_source_ids),
    })

    # Temporarily swap in for the rewrite_run call
    orig_directive = run.rewrite_directive
    run.rewrite_directive = directive
    try:
        result = rewrite_run(run, client, progress_cb=progress_cb)
    finally:
        run.rewrite_directive = orig_directive

    # P2-3: only count a round if something actually changed
    if result.succeeded > 0 or result.failed > 0:
        run.rewrite_round += 1
    return result
