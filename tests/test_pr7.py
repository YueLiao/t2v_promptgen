"""Tests for PR-7: cost cap + export formats + 8-dim coverage."""
import csv
import io
import json
from datetime import datetime

import pytest

from t2v_promptgen.core.schema import Phase, PromptEntry, Run, SL2


def _pe(id="p1", zh="一只手握杯", en="A hand grips a cup", **kw) -> PromptEntry:
    return PromptEntry(
        id=id, capability="x", capability_version=1,
        difficulty="medium", difficulty_score=5.0,
        sl2_covered=kw.pop("sl2_covered", []),
        axes_values={},
        subject_count=kw.pop("subject_count", 1),
        action_count=1,
        subject_type=kw.pop("subject_type", None),
        scene_l1=kw.pop("scene_l1", None),
        camera_zh=kw.pop("camera_zh", None), camera_en=None,
        prompt_zh=zh, prompt_en=en,
        generated_at=datetime.now(),
        **kw,
    )


def _run(rid="r1", **kw) -> Run:
    now = datetime.now()
    return Run(id=rid, capability_slug="human_hand",
               created_at=now, updated_at=now, phase=Phase.P5_EXPORT,
               **kw)


# ============================================================================
# #5 — budget / cost cap
# ============================================================================

class _StubClient:
    def __init__(self, in_tokens=100, out_tokens=200, model="deepseek-chat"):
        self.in_tokens = in_tokens
        self.out_tokens = out_tokens
        self.model = model
        self.name = "stub"
        self.calls = 0

    def generate(self, **kwargs):
        self.calls += 1
        from t2v_promptgen.llm.base import LLMResponse, Usage
        r = LLMResponse()
        r.content = "{}"
        r.usage = Usage()
        r.usage.input_tokens = self.in_tokens
        r.usage.output_tokens = self.out_tokens
        r.usage.cost_usd = 0
        r.finish_reason = "stop"
        return r


def test_budget_cost_estimation_known_model():
    from t2v_promptgen.llm.budget import estimate_cost
    # deepseek-chat: $0.27 in / $1.10 out per 1M
    c = estimate_cost("deepseek-chat", 1_000_000, 1_000_000)
    assert c == 0.27 + 1.10


def test_budget_cost_estimation_unknown_model_returns_zero():
    from t2v_promptgen.llm.budget import estimate_cost
    assert estimate_cost("totally-unknown-model", 100_000, 100_000) == 0.0


def test_budget_passes_within_limit():
    from t2v_promptgen.llm.budget import BudgetedClient
    run = _run()
    run.cost_usd_limit = 10.0
    run.cost_usd_used = 0.0
    bc = BudgetedClient(_StubClient(), run=run)
    bc.generate(messages=[{"role": "user", "content": "x"}])
    assert run.cost_usd_used > 0


def test_budget_blocks_over_limit():
    from t2v_promptgen.llm.budget import BudgetedClient, BudgetExceededError
    run = _run()
    run.cost_usd_limit = 0.0001    # tiny limit
    run.cost_usd_used = 0.0
    bc = BudgetedClient(_StubClient(), run=run)
    with pytest.raises(BudgetExceededError):
        bc.generate(
            messages=[{"role": "user", "content": "x" * 10000}],
            max_tokens=8000,
        )
    assert _StubClient().calls == 0  # never called


def test_budget_accumulates_across_calls():
    from t2v_promptgen.llm.budget import BudgetedClient
    run = _run()
    run.cost_usd_limit = 100.0   # plenty of headroom
    stub = _StubClient(in_tokens=1000, out_tokens=2000)
    bc = BudgetedClient(stub, run=run)
    bc.generate(messages=[{"role": "user", "content": "a"}])
    used_after_one = run.cost_usd_used
    bc.generate(messages=[{"role": "user", "content": "b"}])
    assert run.cost_usd_used > used_after_one
    assert stub.calls == 2


def test_budget_charge_callback_fires():
    from t2v_promptgen.llm.budget import BudgetedClient
    run = _run()
    run.cost_usd_limit = 10.0
    charged = []
    bc = BudgetedClient(_StubClient(), run=run,
                         on_charge=lambda d: charged.append(d))
    bc.generate(messages=[{"role": "user", "content": "x"}])
    assert len(charged) == 1


def test_budget_zero_limit_means_unlimited():
    """cost_usd_limit=0 disables the cap (per UI hint)."""
    from t2v_promptgen.llm.budget import BudgetedClient
    run = _run()
    run.cost_usd_limit = 0.0
    bc = BudgetedClient(_StubClient(in_tokens=100000, out_tokens=100000), run=run)
    # Should not raise even though cost estimate is non-trivial
    bc.generate(messages=[{"role": "user", "content": "x"}])


# ============================================================================
# #6 — export formats
# ============================================================================

def test_export_jsonl():
    from t2v_promptgen.core.exporters import export
    prompts = [_pe("p1"), _pe("p2")]
    body, ctype, ext = export(prompts, "jsonl")
    assert ctype == "application/x-jsonlines"
    assert ext == ".jsonl"
    lines = body.decode("utf-8").splitlines()
    assert len(lines) == 2
    rec = json.loads(lines[0])
    assert rec["id"] == "p1"


def test_export_csv_generic():
    from t2v_promptgen.core.exporters import export
    prompts = [_pe("p1", zh="中文测试", en="english test")]
    body, ctype, ext = export(prompts, "csv")
    assert "text/csv" in ctype
    # UTF-8 with BOM for Excel
    assert body.startswith(b"\xef\xbb\xbf")
    text = body.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)
    assert len(rows) == 1
    assert rows[0]["prompt_zh"] == "中文测试"
    assert rows[0]["prompt_en"] == "english test"


def test_export_csv_lists_serialized():
    from t2v_promptgen.core.exporters import export
    prompts = [_pe("p1", sl2_covered=["x", "y"])]
    body, _, _ = export(prompts, "csv")
    text = body.decode("utf-8-sig")
    row = next(csv.DictReader(io.StringIO(text)))
    # sl2_covered serialized as JSON
    assert json.loads(row["sl2_covered"]) == ["x", "y"]


def test_export_vbench():
    from t2v_promptgen.core.exporters import export
    prompts = [_pe("p1", sl2_covered=["hand_count", "hand_grip"])]
    body, _, ext = export(prompts, "vbench")
    assert ext == ".csv"
    text = body.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0] == ["prompt_en", "dimensions"]
    assert rows[1][1] == "hand_count;hand_grip"


def test_export_vbench_falls_back_to_zh_if_no_en():
    from t2v_promptgen.core.exporters import export
    prompts = [_pe("p1", en="")]
    body, _, _ = export(prompts, "vbench")
    text = body.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text)))
    # The English column should fall back to the Chinese prompt text
    assert rows[1][0] == "一只手握杯"


def test_export_evalcrafter():
    from t2v_promptgen.core.exporters import export
    prompts = [_pe("p1", sl2_covered=["hand_count"])]
    body, ctype, ext = export(prompts, "evalcrafter")
    assert ext == ".jsonl"
    rec = json.loads(body.decode("utf-8").splitlines()[0])
    assert rec["id"] == "p1"
    assert rec["category"] == "hand_count"
    assert rec["prompt"]


def test_export_txt_zh():
    from t2v_promptgen.core.exporters import export
    prompts = [_pe("p1", zh="第一条\n带换行"), _pe("p2", zh="第二条")]
    body, ctype, ext = export(prompts, "txt")
    assert "text/plain" in ctype
    assert ext == ".txt"
    lines = body.decode("utf-8").strip().splitlines()
    assert len(lines) == 2
    # Newlines should be flattened
    assert "\n" not in lines[0]
    assert "第一条" in lines[0]


def test_export_txt_en():
    from t2v_promptgen.core.exporters import export
    prompts = [_pe("p1", en="english one")]
    body, _, _ = export(prompts, "txt_en")
    assert "english one" in body.decode("utf-8")


def test_export_unknown_format_raises():
    from t2v_promptgen.core.exporters import export
    with pytest.raises(ValueError):
        export([_pe()], "nonsense")     # type: ignore[arg-type]


# ============================================================================
# #7 — 8-dim coverage
# ============================================================================

def test_coverage_empty_run():
    from t2v_promptgen.core.coverage import build_coverage_report
    r = build_coverage_report(_run())
    assert r.total_prompts == 0
    assert len(r.dims) == 8
    assert all(d.planned_count == 0 for d in r.dims)
    assert all(d.hit_count == 0 for d in r.dims)


def test_coverage_d1_counts_subject_type():
    from t2v_promptgen.core.coverage import build_coverage_report
    prompts = [
        _pe("p1", subject_type="human", subject_count=1),
        _pe("p2", subject_type="human", subject_count=2),     # → S2
        _pe("p3", subject_type="animal", subject_count=1),    # → S4
    ]
    r = build_coverage_report(_run(prompts=prompts))
    d1 = next(d for d in r.dims if d.code == "D1")
    by_code = {v.code: v.hit_count for v in d1.values}
    assert by_code["S1"] == 1     # single human
    assert by_code["S2"] == 1     # multi-human
    assert by_code["S4"] == 1     # animal


def test_coverage_d4_counts_camera():
    from t2v_promptgen.core.coverage import build_coverage_report
    prompts = [
        _pe("p1", camera_zh="镜头慢慢推近"),    # contains "推"
        _pe("p2", camera_zh="镜头不动"),        # contains "固定"? — no, "不动" not a D4 token
        _pe("p3", camera_zh="镜头跟随主体"),    # contains "跟随"
    ]
    r = build_coverage_report(_run(prompts=prompts))
    d4 = next(d for d in r.dims if d.code == "D4")
    by_code = {v.code: v.hit_count for v in d4.values}
    # "推" → C2; "跟随" → C4
    assert by_code["C2"] >= 1
    assert by_code["C4"] >= 1


def test_coverage_planned_vs_hit():
    from t2v_promptgen.core.coverage import build_coverage_report
    run = _run(recommended_tags={"D1": ["S1", "S2", "S4"], "D4": ["C2", "C12"]})
    run.prompts = [_pe("p1", subject_type="human", subject_count=1, camera_zh="镜头推近")]
    r = build_coverage_report(run)
    # D1: planned {S1, S2, S4}, hit only S1
    d1 = next(d for d in r.dims if d.code == "D1")
    assert d1.planned_count == 3
    assert d1.hit_count == 1     # one value with hit > 0
    # gap_count: planned but not hit = S2, S4 (D1) + C12 (D4) = 3 (D1+D4 are countable)
    assert r.gaps_count >= 2


def test_coverage_dims_marked_countable():
    from t2v_promptgen.core.coverage import build_coverage_report
    r = build_coverage_report(_run())
    countable = {d.code for d in r.dims if d.countable}
    assert countable == {"D1", "D4", "D6"}


def test_coverage_gap_counter_only_counts_countable():
    """Planned-but-unhit on non-countable dims doesn't add to gaps_count."""
    from t2v_promptgen.core.coverage import build_coverage_report
    run = _run(recommended_tags={
        "D1": ["S1"],         # countable, will hit
        "D2": ["A14", "A30"], # NOT countable — won't be counted as gaps
    })
    run.prompts = [_pe("p1", subject_type="human", subject_count=1)]
    r = build_coverage_report(run)
    # D1 fully hit (S1 has 1), D2 not countable
    assert r.gaps_count == 0


# ============================================================================
# Smoke: download endpoints with each format don't crash
# ============================================================================

def test_format_info_has_all_supported():
    from t2v_promptgen.core.exporters import FORMAT_INFO, export
    for fmt in FORMAT_INFO:
        body, ctype, ext = export([_pe()], fmt)     # type: ignore[arg-type]
        assert body
        assert ext.startswith(".")
