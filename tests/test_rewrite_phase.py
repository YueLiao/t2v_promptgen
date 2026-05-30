"""Tests for phases.rewrite — orchestration with a mock LLM client."""
from datetime import datetime

import pytest

from t2v_promptgen.core.rewrite_schema import (
    RewriteDirective, SourceFile, SourcePrompt, Transform,
)
from t2v_promptgen.core.schema import Phase, Run
from t2v_promptgen.phases.rewrite import rewrite_run, iterate_rewrite


class _StubClient:
    """Mock LLM client. Configurable per-call response."""

    def __init__(self, behavior="all_success"):
        self.behavior = behavior
        self.call_count = 0

    def generate(self, **kwargs):
        self.call_count += 1
        if self.behavior == "raise_each_call":
            raise RuntimeError("simulated LLM failure")

        # Parse the user msg to grab the source_ids being asked about
        msg = next(m for m in kwargs["messages"] if m["role"] == "user")["content"]
        import re
        ids = re.findall(r'"source_id":\s*"(\d+)"', msg)

        if self.behavior == "skip_one":
            ids = ids[:-1]
        elif self.behavior == "return_none":
            ids = []

        prompts = [
            {
                "source_id": sid,
                "prompt_zh": f"改写后第 {sid} 条:先 A 然后 B 最后 C",
                "prompt_en": f"Rewritten {sid}: first A then B finally C",
                "subject_type": "human",
                "subject_count": 1,
                "difficulty": "medium",
                "rewrite_diff": "加了三段时序",
            }
            for sid in ids
        ]
        return type("R", (), {"content": {"prompts": prompts}})


def _build_rewrite_run(n_prompts: int = 5) -> Run:
    now = datetime.now()
    return Run(
        id="test", capability_slug="custom", created_at=now, updated_at=now,
        phase=Phase.P1_DIMENSIONS, source="rewrite",
        source_file=SourceFile(
            filename="t.json", format="json", size_bytes=100, row_count=n_prompts,
        ),
        source_prompts=[
            SourcePrompt(source_id=str(i + 1), original_text=f"原 prompt {i + 1}")
            for i in range(n_prompts)
        ],
        rewrite_directive=RewriteDirective(
            transforms=[Transform(id="add_temporal", name_zh="加时序段数",
                                   params={"segments": "3 段"}, order=0)],
        ),
    )


# ---------- happy path ----------

def test_rewrite_all_success():
    run = _build_rewrite_run(5)
    client = _StubClient("all_success")
    result = rewrite_run(run, client)

    assert result.succeeded == 5
    assert result.failed == 0
    assert not result.cancelled
    assert len(run.prompts) == 5
    # Check entries trace back to source
    assert {p.source_id for p in run.prompts} == {"1", "2", "3", "4", "5"}
    # rewrite_diff populated
    assert all(p.rewrite_diff for p in run.prompts)
    # capability_slug overwritten on entries
    assert all(p.capability == "custom" for p in run.prompts)


# ---------- partial failure ----------

def test_rewrite_one_missing_per_batch():
    run = _build_rewrite_run(8)
    client = _StubClient("skip_one")
    result = rewrite_run(run, client, batch_size=4)

    # Each batch of 4 returns 3 → 6 entries, 2 failed
    assert result.succeeded == 6
    assert result.failed == 2
    # Check failed flags
    failed_count = sum(1 for sp in run.source_prompts if sp.failed_to_rewrite)
    assert failed_count == 2


# ---------- whole-batch error ----------

def test_rewrite_all_batches_raise():
    run = _build_rewrite_run(6)
    client = _StubClient("raise_each_call")
    result = rewrite_run(run, client, batch_size=3)

    assert result.succeeded == 0
    assert result.failed == 6
    assert result.error_breakdown.get("RuntimeError") == 6


# ---------- empty pool ----------

def test_rewrite_no_eligible():
    run = _build_rewrite_run(3)
    # Mark all failed → none eligible
    for sp in run.source_prompts:
        sp.failed_to_rewrite = True
    client = _StubClient("all_success")
    result = rewrite_run(run, client)

    assert result.succeeded == 0
    assert client.call_count == 0   # never called


# ---------- selected subset ----------

def test_rewrite_only_selected_ids():
    run = _build_rewrite_run(5)
    run.rewrite_directive.selected_source_ids = ["1", "3"]
    client = _StubClient("all_success")
    result = rewrite_run(run, client)

    assert result.succeeded == 2
    assert {p.source_id for p in run.prompts} == {"1", "3"}


# ---------- cancel ----------

def test_rewrite_cancel_between_batches():
    run = _build_rewrite_run(10)
    client = _StubClient("all_success")

    cancelled = [False]
    def cancel_flag():
        # Cancel after the first batch
        was = cancelled[0]
        cancelled[0] = True
        return was

    result = rewrite_run(run, client, cancel_flag=cancel_flag, batch_size=3)
    assert result.cancelled
    # First batch finished; second batch was skipped
    assert result.succeeded == 3
    assert client.call_count == 1


# ---------- progress callback ----------

def test_rewrite_progress_callback_fires_per_batch():
    run = _build_rewrite_run(6)
    client = _StubClient("all_success")

    calls = []
    def cb(done, total):
        calls.append((done, total))

    result = rewrite_run(run, client, progress_cb=cb, batch_size=2)
    assert result.succeeded == 6
    # 3 batches → 3 cb calls
    assert len(calls) == 3
    assert calls[-1] == (6, 6)


# ---------- iterate ----------

def test_iterate_replaces_rejected_only():
    run = _build_rewrite_run(4)
    client = _StubClient("all_success")
    rewrite_run(run, client)
    assert len(run.prompts) == 4

    # Pretend ids 2 + 4 got rejected
    result = iterate_rewrite(run, ["2", "4"], "再加点紧张感", client)
    assert result.succeeded == 2
    assert run.rewrite_round == 1
    # Should still have 4 entries total
    assert len(run.prompts) == 4


def test_iterate_max_rounds_reached():
    run = _build_rewrite_run(3)
    run.rewrite_round = 3
    run.rewrite_max_rounds = 3
    client = _StubClient("all_success")

    with pytest.raises(ValueError):
        iterate_rewrite(run, ["1"], "再改", client)


# ---------- error guards ----------

def test_rewrite_run_rejects_generate_runs():
    now = datetime.now()
    r = Run(id="x", capability_slug="x", created_at=now, updated_at=now,
            phase=Phase.P0_INTAKE, source="generate")
    with pytest.raises(RuntimeError):
        rewrite_run(r, _StubClient())


def test_rewrite_run_requires_directive():
    run = _build_rewrite_run(3)
    run.rewrite_directive = None
    with pytest.raises(RuntimeError):
        rewrite_run(run, _StubClient())


def test_rewrite_run_requires_client():
    run = _build_rewrite_run(3)
    with pytest.raises(RuntimeError):
        rewrite_run(run, None)
