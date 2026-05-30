"""Tests for qa.rewrite_quality — keep & adherence judges with mock client."""
from datetime import datetime

import pytest

from t2v_promptgen.core.rewrite_schema import RewriteDirective, SourcePrompt, Transform
from t2v_promptgen.core.schema import PromptEntry
from t2v_promptgen.qa.rewrite_quality import (
    attach_scores_to_entries,
    measure_adherence_scores,
    measure_keep_scores,
)


class _StubClient:
    """Mock LLM returning canned scores."""

    def __init__(self, score_map: dict | None = None, *, raise_for_calls: int = 0):
        # score_map: {source_id: int} — id-specific score override
        self.score_map = score_map or {}
        self.raise_for_calls = raise_for_calls
        self.call_count = 0

    def generate(self, **kwargs):
        self.call_count += 1
        if self.call_count <= self.raise_for_calls:
            raise RuntimeError("simulated LLM error")
        # Parse source_ids from user msg
        msg = next(m for m in kwargs["messages"] if m["role"] == "user")["content"]
        import re
        ids = re.findall(r'"source_id":\s*"(\d+)"', msg)
        return type("R", (), {"content": {
            "scores": [
                {"source_id": sid, "score": self.score_map.get(sid, 8)}
                for sid in ids
            ]
        }})


def _build_pairs(n: int) -> list[tuple[SourcePrompt, PromptEntry]]:
    now = datetime.now()
    pairs = []
    for i in range(n):
        sid = str(i + 1)
        sp = SourcePrompt(source_id=sid, original_text=f"原 {sid}")
        pe = PromptEntry(
            id=f"rw_{sid}", capability="x", capability_version=1,
            difficulty="medium", difficulty_score=0.0,
            sl2_covered=[], axes_values={}, subject_count=1, action_count=1,
            camera_zh=None, camera_en=None,
            prompt_zh=f"改 {sid}", prompt_en=f"rewritten {sid}",
            generated_at=now, source_id=sid,
        )
        pairs.append((sp, pe))
    return pairs


# ---------- keep scores ----------

def test_keep_scores_basic():
    pairs = _build_pairs(3)
    client = _StubClient(score_map={"1": 9, "2": 4, "3": 7})
    result = measure_keep_scores(pairs, client)
    assert result == {"1": 9, "2": 4, "3": 7}


def test_keep_scores_clamped_to_0_10():
    pairs = _build_pairs(2)
    client = _StubClient(score_map={"1": 15, "2": -3})
    result = measure_keep_scores(pairs, client)
    assert result == {"1": 10, "2": 0}


def test_keep_scores_batches():
    pairs = _build_pairs(25)
    client = _StubClient()
    result = measure_keep_scores(pairs, client, batch_size=10)
    assert len(result) == 25
    assert client.call_count == 3   # 10 + 10 + 5


def test_keep_scores_skip_failed_batch():
    pairs = _build_pairs(15)
    client = _StubClient(raise_for_calls=1)   # first batch raises
    result = measure_keep_scores(pairs, client, batch_size=10)
    assert len(result) == 5   # only second batch succeeded
    # The 5 are from ids 11-15
    assert set(result.keys()) == {"11", "12", "13", "14", "15"}


def test_keep_scores_no_client():
    pairs = _build_pairs(3)
    result = measure_keep_scores(pairs, None)
    assert result == {}


def test_keep_scores_empty_pairs():
    client = _StubClient()
    assert measure_keep_scores([], client) == {}
    assert client.call_count == 0


# ---------- adherence scores ----------

def test_adherence_scores_basic():
    pairs = _build_pairs(2)
    directive = RewriteDirective(free_text="加时序")
    client = _StubClient(score_map={"1": 9, "2": 6})
    result = measure_adherence_scores(pairs, directive, client)
    assert result == {"1": 9, "2": 6}


def test_adherence_scores_no_directive():
    pairs = _build_pairs(2)
    client = _StubClient()
    result = measure_adherence_scores(pairs, None, client)
    assert result == {}


def test_adherence_scores_with_cards():
    pairs = _build_pairs(3)
    directive = RewriteDirective(
        transforms=[
            Transform(id="add_temporal", name_zh="加时序段数", params={"segments": "3 段"}, order=0),
            Transform(id="add_causal_chain", name_zh="加因果链", params={"chain_depth": "2 层"}, order=1),
        ],
        free_text="保持原意",
    )
    client = _StubClient(score_map={"1": 8, "2": 5, "3": 9})
    result = measure_adherence_scores(pairs, directive, client)
    assert result == {"1": 8, "2": 5, "3": 9}


# ---------- attach + summary ----------

def test_attach_writes_back_scores():
    pairs = _build_pairs(3)
    keep = {"1": 9, "2": 6, "3": 4}
    adh = {"1": 8, "2": 7, "3": 5}
    summary = attach_scores_to_entries(pairs, keep, adh)

    assert summary["total"] == 3
    assert summary["keep_pass"] == 2     # 1, 2 (>=5)
    assert summary["adherence_pass"] == 2  # 1, 2 (>=7)
    assert summary["both_pass"] == 2     # 1, 2
    assert summary["keep_avg"] == round((9 + 6 + 4) / 3, 1)
    assert summary["adherence_avg"] == round((8 + 7 + 5) / 3, 1)

    # Scores landed on entries
    assert pairs[0][1].rewrite_kept_score == 9
    assert pairs[0][1].rewrite_adherence_score == 8
    assert pairs[2][1].rewrite_kept_score == 4
    assert pairs[2][1].rewrite_adherence_score == 5


def test_attach_handles_missing_scores():
    """If a judge didn't score one id, the entry's field stays None (not 0)."""
    pairs = _build_pairs(3)
    keep = {"1": 9}      # only id 1 scored
    adh = {}             # adherence judge entirely failed
    summary = attach_scores_to_entries(pairs, keep, adh)

    # Without a score, default is "pass" (don't penalize for judge outage)
    assert summary["keep_pass"] == 3
    assert summary["adherence_pass"] == 3
    assert summary["both_pass"] == 3
    # But averages only computed over available scores
    assert summary["keep_avg"] == 9.0
    assert summary["adherence_avg"] is None

    # Unscored entries: field stays None
    assert pairs[1][1].rewrite_kept_score is None
    assert pairs[2][1].rewrite_adherence_score is None


def test_attach_thresholds_custom():
    pairs = _build_pairs(2)
    keep = {"1": 6, "2": 4}
    adh = {"1": 7, "2": 8}
    summary = attach_scores_to_entries(pairs, keep, adh,
                                        keep_threshold=7, adherence_threshold=8)
    # Now keep threshold is 7: both fail keep
    assert summary["keep_pass"] == 0
    # Adherence threshold 8: 1 fails, 2 passes
    assert summary["adherence_pass"] == 1
    assert summary["both_pass"] == 0
